import os
import pytest
from unittest.mock import MagicMock

# Add project roots to path so we can import app and tasks
_tests_dir = os.path.dirname(__file__)
# Ensure the root of the project is in sys.path to resolve 'config' and 'secrets_manager'
sys.path.insert(0, os.path.abspath(os.path.join(_tests_dir, '..')))
sys.path.insert(0, os.path.abspath(os.path.join(_tests_dir, '../web')))
sys.path.insert(0, os.path.abspath(os.path.join(_tests_dir, '../worker')))


# Pre-import web app at module level so it's cached BEFORE test_worker.py's
# sys.modules mocking runs during collection. Both 'app' and 'web.app' are
# registered so tests using either import path see the same cached module.
import web.app as _cached_web_app  # noqa: E402
import config
import secrets_manager
sys.modules.setdefault('web.app', _cached_web_app)


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
        # Use secrets loaded by the secrets_manager
        "MASTER_ENCRYPTION_KEY": loaded_secrets.get('MASTER_ENCRYPTION_KEY'),
        "CELERY_SIGNING_KEY": loaded_secrets.get('CELERY_SIGNING_KEY'),
        "CLOUDFLARE_TUNNEL_TOKEN": loaded_secrets.get('CLOUDFLARE_TUNNEL_TOKEN'),
    }

    # Create a new Settings instance with overrides
    # Use config.Settings here directly as it's the class, not the singleton
    test_app_settings = config.Settings(_env_file=None, **settings_override_data)

    yield test_app_settings

    # Clean up environment variable after tests
    del os.environ['FLASK_ENV']


@pytest.fixture
def app(test_settings):
    """
    Fixture for the Flask app, configured with test_settings.
    """
    import web.app as app_module

    # Override the app_settings in the actual app_module for this test session
    app_module.app_settings = test_settings

    app_module.app.config.update({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": test_settings.secret_key.get_secret_value() if test_settings.secret_key else 'default-insecure-test-key',
        "UPLOAD_FOLDER": test_settings.upload_folder,
        "OUTPUT_FOLDER": test_settings.output_folder,
        "MAX_CONTENT_LENGTH": test_settings.max_content_length,
        "PERMANENT_SESSION_LIFETIME": test_settings.permanent_session_lifetime,
        "SESSION_COOKIE_SECURE": test_settings.session_cookie_secure,
    })

    # Mock redis_client and celery to avoid real connections
    app_module.redis_client = MagicMock()
    app_module.celery = MagicMock()
    # Disable rate limiting to prevent 429 errors from accumulated test requests
    app_module.limiter.enabled = False

    yield app_module.app

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
