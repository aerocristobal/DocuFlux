import sys
import os
import pytest
from unittest.mock import MagicMock

# Set test environment variables at module level (before any app imports)
# FLASK_ENV=testing causes secrets_manager to auto-generate MASTER_ENCRYPTION_KEY
# instead of requiring it as an explicit env var.
os.environ.setdefault('FLASK_ENV', 'testing')
os.environ.setdefault('SECRET_KEY', 'test-secret-key')
os.environ.setdefault('REDIS_METADATA_URL', 'redis://localhost:6379/1')
os.environ.setdefault('CELERY_BROKER_URL', 'redis://localhost:6379/0')
os.environ.setdefault('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')

# Add project roots to path so we can import app and tasks
_tests_dir = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(os.path.join(_tests_dir, '../web')))
sys.path.insert(0, os.path.abspath(os.path.join(_tests_dir, '../worker')))

# Pre-import web app at module level so it's cached BEFORE test_worker.py's
# sys.modules mocking runs during collection. Both 'app' and 'web.app' are
# registered so tests using either import path see the same cached module.
import app as _cached_web_app  # noqa: E402
sys.modules.setdefault('web.app', _cached_web_app)


@pytest.fixture
def app():
    import app as app_module
    app_module.app.config.update({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
    })

    # Mock redis_client and celery to avoid real connections
    app_module.redis_client = MagicMock()
    app_module.celery = MagicMock()
    # Disable rate limiting to prevent 429 errors from accumulated test requests
    app_module.limiter.enabled = False

    yield app_module.app

@pytest.fixture
def client(app):
    return app.test_client()

@pytest.fixture
def runner(app):
    return app.test_cli_runner()
