import os
import sys
import pytest
from unittest.mock import MagicMock

# Set test environment variables at module level (before any app imports)
os.environ['BUILD_GPU'] = 'false'

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
