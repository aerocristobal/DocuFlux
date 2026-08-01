import os
import sys
import tempfile
import time
import pytest
from unittest.mock import MagicMock

# Set test environment variables at module level (before any app imports)
os.environ['BUILD_GPU'] = 'false'
os.environ['FLASK_ENV'] = 'testing'

# Add project roots to path so we can import app and tasks
_tests_dir = os.path.dirname(__file__)
_root = os.path.abspath(os.path.join(_tests_dir, '..'))
sys.path.insert(0, _root)
sys.path.insert(0, os.path.join(_root, 'shared'))   # shared modules (encryption, key_manager, etc.)
sys.path.insert(0, os.path.join(_root, 'web'))
sys.path.insert(0, os.path.join(_root, 'worker'))


# Import necessary modules for test setup
import config
import secrets_manager
import web.app as web_app
sys.modules['app'] = web_app  # Ensure @patch('app.xxx') patches the same module as web.app

# Stub the worker's heavy deps (marker, torch, PIL, ...) so `import tasks` works from any
# test directory. Must come *after* the real config/secrets_manager/web.app imports above,
# which need the genuine modules — but before pytest imports the first test module, which
# is what makes the stubbing deterministic instead of dependent on collection order.
from tests.support.stubs import install_worker_stubs  # noqa: E402
install_worker_stubs()


def _register_test_only_routes():
    """Add the routes the error-handler tests raise from.

    Registered at conftest import time, not inside the `app` fixture. Flask forbids
    adding routes once the app has served a request, so registering lazily only worked
    while some earlier test happened to build the app before any other test used it.
    That made the outcome depend on collection order: `pytest -m unit` deselects the
    integration test that used to register them first, and every later test using the
    `app` fixture then failed with "The setup method 'route' can no longer be called".
    """
    from werkzeug.exceptions import TooManyRequests

    existing = {r.rule for r in web_app.app.url_map.iter_rules()}
    if '/_test_raise_500' in existing:
        return

    @web_app.app.route('/_test_raise_500')
    def _test_raise_500():
        raise RuntimeError('boom')

    @web_app.app.route('/_test_raise_429')
    def _test_raise_429():
        raise TooManyRequests('slow down')


_register_test_only_routes()


# Map a directory under tests/ to the tier marker every test inside it carries.
# Applied automatically so the 640+ existing tests need no decorating.
_DIR_MARKERS = {
    'unit': 'unit',
    'integration': 'integration',
    'ui': 'ui',
    'bdd': 'bdd',
    'characterization': 'characterization',
}


def pytest_collection_modifyitems(config, items):
    """Tag every test with its tier marker based on the directory it lives in."""
    for item in items:
        parts = item.nodeid.split('/')
        for segment, marker in _DIR_MARKERS.items():
            if segment in parts:
                item.add_marker(marker)


# Written next to coverage.json so scripts/check_coverage_floors.py can tell whether the
# report it is about to enforce came from a complete, passing run.
COVERAGE_RUN_MANIFEST = 'coverage-run.json'


def _run_filters(config):
    """The ways an invocation can cover less than the whole suite."""
    return {
        'markexpr': config.option.markexpr or '',
        'keyword': config.option.keyword or '',
        'args': list(config.args),
        'testpaths': list(config.getini('testpaths')),
        'ignore': list(getattr(config.option, 'ignore', None) or []),
        'deselect': list(getattr(config.option, 'deselect', None) or []),
        'last_failed': bool(getattr(config.option, 'lf', False)),
    }


def pytest_sessionfinish(session, exitstatus):
    """Record how the suite was invoked, alongside the coverage report.

    Coverage is written to a fixed path, so on its own a report cannot say whether it
    came from the whole suite or from `pytest -m unit`, nor whether that run passed.
    Enforcing per-module floors against a partial report produces breaches that are not
    real; enforcing against a stale one silently gates on nothing. This manifest lets the
    checker tell the difference instead of guessing from file timestamps.
    """
    import json as _json

    config = session.config
    filters = _run_filters(config)
    full_run = (
        not filters['markexpr']
        and not filters['keyword']
        and not filters['ignore']
        and not filters['deselect']
        and not filters['last_failed']
        and filters['args'] == filters['testpaths']
    )

    manifest = {
        'full_run': full_run,
        'exit_status': int(exitstatus),
        'filters': filters,
        'collected': getattr(session, 'testscollected', 0),
        'failed': getattr(session, 'testsfailed', 0),
        'finished_at': time.time(),
    }
    try:
        target = os.path.join(str(config.rootpath), COVERAGE_RUN_MANIFEST)
        with open(target, 'w') as fh:
            _json.dump(manifest, fh, indent=2)
    except OSError:  # pragma: no cover - never fail a run over telemetry
        pass


@pytest.fixture(scope='session')
def test_settings():
    """
    Fixture to provide a Pydantic Settings instance with test-specific overrides.
    """
    # Temporarily set FLASK_ENV to 'testing' for secrets_manager to generate keys
    os.environ['FLASK_ENV'] = 'testing'

    # Load secrets using the secrets_manager (will generate MASTER_ENCRYPTION_KEY)
    loaded_secrets = secrets_manager.load_all_secrets()

    # Create a dictionary of settings overrides for Pydantic
    settings_override_data = {
        # Explicitly set test-specific values
        "FLASK_ENV": "testing",
        "SECRET_KEY": "test-secret-key",
        "REDIS_METADATA_URL": "redis://localhost:6379/1",
        "CELERY_BROKER_URL": "redis://localhost:6379/0",
        "CELERY_RESULT_BACKEND": "redis://localhost:6379/0",
        "UPLOAD_FOLDER": "/tmp/test_uploads",
        "OUTPUT_FOLDER": "/tmp/test_outputs",
        "BUILD_GPU": False,
        # Use secrets loaded by the secrets_manager
        "MASTER_ENCRYPTION_KEY": loaded_secrets.get('MASTER_ENCRYPTION_KEY'),
        "CELERY_SIGNING_KEY": loaded_secrets.get('CELERY_SIGNING_KEY'),
        "CLOUDFLARE_TUNNEL_TOKEN": loaded_secrets.get('CLOUDFLARE_TUNNEL_TOKEN'),
    }
    
    # Create a new Settings instance with overrides
    test_app_settings = config.Settings(_env_file=None, **settings_override_data)
    
    yield test_app_settings

    # Clean up environment variable after tests
    del os.environ['FLASK_ENV']


@pytest.fixture
def app(test_settings):
    """
    Fixture for the Flask app, configured with test_settings.
    """
    web_app.app_settings = test_settings
    web_app.app.config.update({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
    })

    # Mock redis_client and celery to avoid real connections in web.app
    web_app.redis_client = MagicMock()
    web_app.celery = MagicMock()

    # Epic 5: Provide a real LocalStorageBackend with temp dirs for tests
    from storage import LocalStorageBackend
    _tmpdir = tempfile.mkdtemp(prefix='docuflux_test_')
    _upload = os.path.join(_tmpdir, 'uploads')
    _output = os.path.join(_tmpdir, 'outputs')
    os.makedirs(_upload, exist_ok=True)
    os.makedirs(_output, exist_ok=True)
    web_app.storage = LocalStorageBackend(upload_folder=_upload, output_folder=_output)
    web_app.UPLOAD_FOLDER = _upload
    web_app.OUTPUT_FOLDER = _output
    web_app.app.config['UPLOAD_FOLDER'] = _upload
    web_app.app.config['OUTPUT_FOLDER'] = _output

    # Disable rate limiting to prevent 429 errors from accumulated test requests
    if hasattr(web_app, 'limiter'):
        web_app.limiter.enabled = False

    yield web_app.app

@pytest.fixture
def client(app):
    """
    Fixture for the Flask test client.
    """
    return app.test_client()

@pytest.fixture
def runner(app):
    """
    Fixture for the Flask CLI runner.
    """
    return app.test_cli_runner()
