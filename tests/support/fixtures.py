"""Fixtures shared by the route-level test modules.

These were previously copy-pasted into `tests/unit/test_api_v1.py`,
`tests/unit/test_api_v1_contracts.py` and `tests/ui/test_ui_workflows.py` in three
near-identical blocks. Import them where you need them, aliasing as required:

    from tests.support.fixtures import (  # noqa: F401
        isolated_client as client, mock_redis, mock_celery,
    )

`isolated_client` is deliberately *not* named `client`: `tests/conftest.py` defines its
own `client` (built on the `app` fixture, with `redis_client`/`celery` already replaced
by MagicMocks). Exporting a second `client` from here would shadow it for every test in
the suite. Alias at the import site so the override stays file-local.

`tests/integration/test_e2e.py` keeps its own fixtures on purpose — its `flask_app` is
module-scoped and its `mock_redis` is pre-configured with return values (`hgetall -> {}`)
rather than a bare MagicMock. Folding it in here would silently change what those tests
assert.
"""
import os
import tempfile
from unittest.mock import patch

import pytest


@pytest.fixture
def isolated_client():
    """Flask test client backed by real storage in a temp dir, with rate limiting off.

    Restores `limiter.enabled` on teardown so a test that needs the limiter later is not
    affected by having run after this one.
    """
    import web.app as web_app_mod
    from storage import LocalStorageBackend
    from web.app import app, limiter

    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False

    tmpdir = tempfile.mkdtemp(prefix='docuflux_test_')
    upload = os.path.join(tmpdir, 'uploads')
    output = os.path.join(tmpdir, 'outputs')
    os.makedirs(upload, exist_ok=True)
    os.makedirs(output, exist_ok=True)

    web_app_mod.storage = LocalStorageBackend(upload_folder=upload, output_folder=output)
    web_app_mod.UPLOAD_FOLDER = upload
    web_app_mod.OUTPUT_FOLDER = output
    app.config['UPLOAD_FOLDER'] = upload
    app.config['OUTPUT_FOLDER'] = output

    original_enabled = limiter.enabled
    limiter.enabled = False
    with app.test_client() as c:
        yield c
    limiter.enabled = original_enabled


@pytest.fixture
def mock_redis():
    """Bare MagicMock over `web.app.redis_client`.

    Attribute access returns a truthy MagicMock unless the test configures it. Tests that
    need falsy defaults (`hgetall -> {}`) must set them explicitly.
    """
    with patch('web.app.redis_client') as mock:
        yield mock


@pytest.fixture
def mock_celery():
    with patch('web.app.celery') as mock:
        yield mock


@pytest.fixture
def mock_disk_space():
    with patch('web.app.check_disk_space', return_value=True) as mock:
        yield mock


@pytest.fixture
def api_headers():
    """Headers carrying a valid `dk_` API key, with validation stubbed to accept it."""
    with patch('web.app._validate_api_key',
               return_value={'created_at': '1700000000.0', 'label': 'test'}):
        yield {'X-API-Key': 'dk_testkey'}


@pytest.fixture
def admin_headers():
    """Headers carrying an admin bearer token, with the secret check stubbed to pass."""
    with patch('web.routes.auth._check_admin_secret', return_value=(None, None)):
        yield {'Authorization': 'Bearer test-admin-secret'}
