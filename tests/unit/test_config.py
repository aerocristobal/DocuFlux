import os
from datetime import timedelta
import pytest
from unittest.mock import patch, MagicMock

# Dynamically add the project root to sys.path to allow imports from config and secrets_manager
import sys
_tests_dir = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(os.path.join(_tests_dir, '../../')))

# Import Settings AFTER sys.path is updated
from config import Settings
from secrets_manager import load_all_secrets, generate_master_encryption_key


# Fixture to clear environment variables and reset global settings instance
@pytest.fixture(autouse=True)
def clear_env_and_reset_settings():
    original_environ = os.environ.copy()
    
    # Clear all relevant environment variables
    keys_to_clear = [
        'UPLOAD_FOLDER', 'OUTPUT_FOLDER', 'REDIS_METADATA_URL', 'CELERY_BROKER_URL',
        'CELERY_RESULT_BACKEND', 'FLASK_DEBUG', 'MAX_CONTENT_LENGTH', 'MIN_FREE_SPACE',
        'SESSION_COOKIE_SECURE', 'PERMANENT_SESSION_LIFETIME_DAYS', 'BEHIND_PROXY',
        'REDIS_TLS_CA_CERTS', 'REDIS_TLS_CERTFILE', 'REDIS_TLS_KEYFILE', 'MCP_SERVER_URL',
        'MAX_MARKER_PAGES', 'MAX_SLM_CONTEXT', 'SLM_MODEL_PATH', 'METRICS_PORT',
        'FLASK_LIMITER_DEFAULT_LIMITS', 'FLASK_LIMITER_STORAGE_URI',
        'SOCKETIO_ASYNC_MODE', 'SOCKETIO_MESSAGE_QUEUE', 'SOCKETIO_CORS_ALLOWED_ORIGINS',
        'SECRET_KEY', 'MASTER_ENCRYPTION_KEY', 'CLOUDFLARE_TUNNEL_TOKEN',
        'FLASK_ENV', 'MARKER_ENABLED', 'BUILD_GPU', 'P_SETTINGS' # P_SETTINGS is for pydantic_settings base configuration
    ]
    for key in keys_to_clear:
        if key in os.environ:
            del os.environ[key]

    # Temporarily remove config.settings from sys.modules to force re-evaluation
    # This is crucial because Settings() is a singleton and would otherwise retain state
    if 'config' in sys.modules:
        del sys.modules['config']
    
    # Reload config to ensure fresh Settings instance
    import importlib
    import config
    importlib.reload(config)
    global settings
    settings = config.settings

    yield

    # Restore original environment variables
    os.environ.clear()
    os.environ.update(original_environ)

    # Reset config.settings singleton for other tests
    if 'config' in sys.modules:
        del sys.modules['config']
    importlib.reload(config)
    global settings
    settings = config.settings


def test_default_settings():
    """Test that settings load with correct default values when no env vars are set."""
    s = Settings(_env_file=None) # Ensure no .env file is loaded for this test

    assert s.upload_folder == "data/uploads"
    assert s.flask_debug is False
    assert s.max_content_length == 100 * 1024 * 1024
    assert s.permanent_session_lifetime_days == 30
    assert s.permanent_session_lifetime == timedelta(days=30)
    assert s.default_limits == ["1000 per day", "200 per hour"]
    assert s.storage_uri == s.redis_metadata_url # Should default to redis_metadata_url
    assert s.marker_enabled is False
    assert s.build_gpu is False
    assert s.secret_key is None # Should be None if not explicitly set


def test_env_variable_override():
    """Test that environment variables correctly override default settings."""
    os.environ['UPLOAD_FOLDER'] = '/custom/uploads'
    os.environ['FLASK_DEBUG'] = 'True'
    os.environ['MAX_CONTENT_LENGTH'] = '200000000' # 200MB
    os.environ['FLASK_LIMITER_DEFAULT_LIMITS'] = '["500 per day"]'
    os.environ['REDIS_METADATA_URL'] = 'redis://localhost:9999/2'
    os.environ['FLASK_LIMITER_STORAGE_URI'] = 'redis://localhost:9999/3'
    os.environ['MARKER_ENABLED'] = 'true'
    os.environ['BUILD_GPU'] = '1'

    s = Settings(_env_file=None)

    assert s.upload_folder == '/custom/uploads'
    assert s.flask_debug is True
    assert s.max_content_length == 200000000
    assert s.default_limits == ["500 per day"]
    assert s.redis_metadata_url == 'redis://localhost:9999/2'
    assert s.storage_uri == 'redis://localhost:9999/3'
    assert s.marker_enabled is True
    assert s.build_gpu is True


def test_env_file_loading(tmp_path):
    """Test that settings load correctly from a .env file."""
    env_file = tmp_path / ".env"
    env_file.write_text("""
UPLOAD_FOLDER=/env_uploads
FLASK_DEBUG=false
PERMANENT_SESSION_LIFETIME_DAYS=15
SECRET_KEY=env-secret
""")
    # Pydantic Settings will look for .env in the current working directory,
    # or specified path. We'll use tmp_path as the base directory for the test.
    with patch('pydantic_settings.main.find_dotenv', return_value=str(env_file)):
        s = Settings(_env_file=str(env_file))

        assert s.upload_folder == '/env_uploads'
        assert s.flask_debug is False
        assert s.permanent_session_lifetime_days == 15
        assert s.permanent_session_lifetime == timedelta(days=15)
        assert s.secret_key.get_secret_value() == 'env-secret'


def test_secret_str_handling():
    """Test that SecretStr fields are handled correctly."""
    os.environ['SECRET_KEY'] = 'my-super-secret-key'
    os.environ['MASTER_ENCRYPTION_KEY'] = 'aes-key-from-env'

    s = Settings(_env_file=None)

    assert s.secret_key.get_secret_value() == 'my-super-secret-key'
    assert s.master_encryption_key.get_secret_value() == 'aes-key-from-env'
    assert repr(s.secret_key) == "SecretStr('**********')" # Check redaction


def test_secrets_manager_integration(clear_env_and_reset_settings):
    """
    Test that the Settings class correctly integrates with the secrets_manager,
    especially for generated keys like MASTER_ENCRYPTION_KEY in testing environment.
    """
    # Simulate FLASK_ENV for secrets_manager logic
    os.environ['FLASK_ENV'] = 'testing'

    # Clear config.settings from sys.modules to ensure secrets_manager.load_all_secrets
    # and subsequent Settings() instantiation is fresh.
    import importlib
    if 'config' in sys.modules:
        del sys.modules['config']
    if 'secrets_manager' in sys.modules:
        del sys.modules['secrets_manager']
    
    # Reload secrets_manager and config to pick up env changes
    import secrets_manager
    import config
    importlib.reload(secrets_manager)
    importlib.reload(config)

    # Load secrets using the secrets_manager (which generates MASTER_ENCRYPTION_KEY in 'testing')
    loaded_secrets = secrets_manager.load_all_secrets()

    # Pass these secrets to the Settings constructor
    settings_override_data = {
        k.lower(): v for k, v in loaded_secrets.items() if v is not None
    }
    s = config.Settings(_env_file=None, **settings_override_data)
    
    assert s.flask_debug is False # Default from Pydantic
    assert s.secret_key.get_secret_value() == 'change-me-in-production' # Default from secrets_manager
    assert s.master_encryption_key.get_secret_value() is not None
    assert len(s.master_encryption_key.get_secret_value()) > 0
    assert "==" in s.master_encryption_key.get_secret_value() # Base64 encoding check


def test_type_conversion():
    """Test that Pydantic automatically handles type conversions."""
    os.environ['FLASK_DEBUG'] = '1' # Pydantic converts '1' to True
    os.environ['MAX_CONTENT_LENGTH'] = '104857600' # Numeric string to int
    os.environ['PERMANENT_SESSION_LIFETIME_DAYS'] = '7'
    os.environ['FLASK_LIMITER_DEFAULT_LIMITS'] = '["50 per minute", "1000 per hour"]'

    s = Settings(_env_file=None)

    assert s.flask_debug is True
    assert isinstance(s.flask_debug, bool)
    assert s.max_content_length == 104857600
    assert isinstance(s.max_content_length, int)
    assert s.permanent_session_lifetime_days == 7
    assert s.permanent_session_lifetime == timedelta(days=7)
    assert s.default_limits == ["50 per minute", "1000 per hour"]
    assert isinstance(s.default_limits, list)


def test_default_storage_uri_logic():
    """Test that storage_uri defaults to redis_metadata_url if not explicitly set."""
    os.environ['REDIS_METADATA_URL'] = 'redis://my-redis:6379/10'
    # Do NOT set FLASK_LIMITER_STORAGE_URI

    s = Settings(_env_file=None)
    assert s.redis_metadata_url == 'redis://my-redis:6379/10'
    assert s.storage_uri == 'redis://my-redis:6379/10' # Should be the same as redis_metadata_url

    # Test when FLASK_LIMITER_STORAGE_URI is explicitly set
    os.environ['FLASK_LIMITER_STORAGE_URI'] = 'redis://my-other-redis:6379/1'
    s = Settings(_env_file=None)
    assert s.storage_uri == 'redis://my-other-redis:6379/1'


def test_default_socketio_message_queue_logic():
    """Test that socketio_message_queue defaults to redis_metadata_url if not explicitly set."""
    os.environ['REDIS_METADATA_URL'] = 'redis://my-redis:6379/11'
    # Do NOT set SOCKETIO_MESSAGE_QUEUE

    s = Settings(_env_file=None)
    assert s.redis_metadata_url == 'redis://my-redis:6379/11'
    assert s.socketio_message_queue == 'redis://my-redis:6379/11'

    # Test when SOCKETIO_MESSAGE_QUEUE is explicitly set
    os.environ['SOCKETIO_MESSAGE_QUEUE'] = 'redis://my-mq:6379/1'
    s = Settings(_env_file=None)
    assert s.socketio_message_queue == 'redis://my-mq:6379/1'