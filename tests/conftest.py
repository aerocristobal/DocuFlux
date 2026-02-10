import sys
import os
import pytest
from unittest.mock import MagicMock

# Add project roots to path so we can import app and tasks
_tests_dir = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(os.path.join(_tests_dir, '../web')))
sys.path.insert(0, os.path.abspath(os.path.join(_tests_dir, '../worker')))

@pytest.fixture(scope='session', autouse=True)
def mock_external_deps():
    """
    Mock external dependencies like Redis and Celery at the socket/library level
    to prevent connection attempts during import.
    """
    # This might be tricky if modules are already imported, but for pytest it usually works
    # if done early enough or if we patch the libraries.
    # For now, we rely on the fact that connection pools are usually lazy or we set ENV vars.
    os.environ['SECRET_KEY'] = 'test-secret-key'
    os.environ['REDIS_METADATA_URL'] = 'redis://mock:6379/1'
    os.environ['CELERY_BROKER_URL'] = 'redis://mock:6379/0'
    os.environ['CELERY_RESULT_BACKEND'] = 'redis://mock:6379/0'

@pytest.fixture
def app():
    from app import app
    app.config.update({
        "TESTING": True,
    })
    
    # Mock the internal redis_client to avoid real connections
    # We do this patching on the instance already created in app.py
    import app as app_module
    app_module.redis_client = MagicMock()
    app_module.celery = MagicMock()
    
    yield app

@pytest.fixture
def client(app):
    return app.test_client()

@pytest.fixture
def runner(app):
    return app.test_cli_runner()
