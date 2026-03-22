"""
Contract tests for REST API v1 — validates response schemas.

Story 9.1: Each API v1 endpoint has schema-verified tests covering
success and error response shapes. Runs without Docker (mocked Redis/Celery).
"""

import io
import json
import uuid
import time

import pytest
from unittest.mock import Mock, patch, MagicMock


# ── Schema validation helper ──────────────────────────────────────────────────

def assert_matches_schema(data, schema, msg=""):
    """Assert that data dict contains all required keys with correct types.

    schema: dict of {key: type_or_tuple_of_types}
    """
    for key, expected_type in schema.items():
        assert key in data, f"Missing key '{key}' in response{' — ' + msg if msg else ''}: {data}"
        assert isinstance(data[key], expected_type), (
            f"Key '{key}' expected {expected_type}, got {type(data[key]).__name__}"
            f"{' — ' + msg if msg else ''}"
        )


ERROR_SCHEMA = {'error': str}


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    """Create test client for Flask app."""
    import os
    import tempfile
    import web.app as web_app_mod
    from storage import LocalStorageBackend
    from web.app import app, limiter

    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    _tmpdir = tempfile.mkdtemp(prefix='docuflux_contract_test_')
    _upload = os.path.join(_tmpdir, 'uploads')
    _output = os.path.join(_tmpdir, 'outputs')
    os.makedirs(_upload, exist_ok=True)
    os.makedirs(_output, exist_ok=True)
    web_app_mod.storage = LocalStorageBackend(upload_folder=_upload, output_folder=_output)
    web_app_mod.UPLOAD_FOLDER = _upload
    web_app_mod.OUTPUT_FOLDER = _output
    app.config['UPLOAD_FOLDER'] = _upload
    app.config['OUTPUT_FOLDER'] = _output
    original_enabled = limiter.enabled
    limiter.enabled = False
    with app.test_client() as c:
        yield c
    limiter.enabled = original_enabled


@pytest.fixture
def mock_redis():
    with patch('web.app.redis_client') as mock:
        yield mock


@pytest.fixture
def mock_celery():
    with patch('web.app.celery') as mock:
        yield mock


@pytest.fixture
def mock_disk_space():
    with patch('web.app.check_disk_space', return_value=True):
        yield


@pytest.fixture
def api_headers():
    with patch('web.app._validate_api_key', return_value={'created_at': '1700000000.0', 'label': 'test'}):
        yield {'X-API-Key': 'dk_testkey'}


@pytest.fixture
def admin_headers():
    with patch('web.routes.auth._check_admin_secret', return_value=(None, None)):
        yield {'Authorization': 'Bearer test-admin-secret'}


# ── Capture session helper ────────────────────────────────────────────────────

def _session_meta(status='active', page_count='2', job_id=None):
    return {
        'status': status,
        'created_at': '1700000000.0',
        'title': 'Test Book',
        'to_format': 'markdown',
        'source_url': 'https://example.com',
        'force_ocr': 'False',
        'page_count': page_count,
        'client_id': 'test-client',
        'job_id': job_id or str(uuid.uuid4()),
        'batches_queued': '0',
        'batches_done': '0',
        'batches_failed': '0',
        'next_batch_start': '0',
    }


# ============================================================================
# Conversion endpoint contracts
# ============================================================================

CONVERT_SUCCESS_SCHEMA = {
    'job_id': str,
    'status': str,
    'status_url': str,
    'created_at': str,
}

STATUS_SUCCESS_SCHEMA = {
    'job_id': str,
    'status': str,
    'progress': int,
    'filename': str,
    'from_format': str,
    'to_format': str,
    'engine': str,
    'created_at': str,
}

FORMATS_SCHEMA = {
    'input_formats': list,
    'output_formats': list,
    'conversions': list,
}

EXTRACT_METADATA_SUCCESS_SCHEMA = {
    'job_id': str,
    'status': str,
    'message': str,
}


class TestConversionContracts:

    def test_convert_success_shape(self, client, mock_redis, mock_celery, mock_disk_space, api_headers):
        mock_redis.hset = Mock()
        mock_redis.hgetall = Mock(return_value={})
        mock_redis.zadd = Mock()
        mock_celery.send_task = Mock()

        data = {
            'file': (io.BytesIO(b"# Test"), 'test.md'),
            'to_format': 'docx',
            'engine': 'pandoc',
        }
        resp = client.post('/api/v1/convert', data=data,
                           content_type='multipart/form-data', headers=api_headers)
        assert resp.status_code == 202
        assert_matches_schema(resp.get_json(), CONVERT_SUCCESS_SCHEMA)

    def test_convert_missing_file_error_shape(self, client, mock_redis, mock_celery, mock_disk_space, api_headers):
        resp = client.post('/api/v1/convert', data={'to_format': 'docx'},
                           content_type='multipart/form-data', headers=api_headers)
        assert resp.status_code == 400
        assert_matches_schema(resp.get_json(), ERROR_SCHEMA)

    def test_convert_missing_format_error_shape(self, client, mock_redis, mock_celery, mock_disk_space, api_headers):
        data = {'file': (io.BytesIO(b"# Test"), 'test.md')}
        resp = client.post('/api/v1/convert', data=data,
                           content_type='multipart/form-data', headers=api_headers)
        assert resp.status_code == 400
        assert_matches_schema(resp.get_json(), ERROR_SCHEMA)

    def test_convert_invalid_format_error_shape(self, client, mock_redis, mock_celery, mock_disk_space, api_headers):
        data = {
            'file': (io.BytesIO(b"# Test"), 'test.md'),
            'to_format': 'nosuchformat',
        }
        resp = client.post('/api/v1/convert', data=data,
                           content_type='multipart/form-data', headers=api_headers)
        assert resp.status_code == 422
        assert_matches_schema(resp.get_json(), ERROR_SCHEMA)

    def test_convert_invalid_engine_error_shape(self, client, mock_redis, mock_celery, mock_disk_space, api_headers):
        data = {
            'file': (io.BytesIO(b"# Test"), 'test.md'),
            'to_format': 'docx',
            'engine': 'nosuchengine',
        }
        resp = client.post('/api/v1/convert', data=data,
                           content_type='multipart/form-data', headers=api_headers)
        assert resp.status_code == 422
        assert_matches_schema(resp.get_json(), ERROR_SCHEMA)

    def test_convert_disk_full_error_shape(self, client, mock_redis, mock_celery, api_headers):
        with patch('web.app.check_disk_space', return_value=False):
            data = {
                'file': (io.BytesIO(b"# Test"), 'test.md'),
                'to_format': 'docx',
            }
            resp = client.post('/api/v1/convert', data=data,
                               content_type='multipart/form-data', headers=api_headers)
            assert resp.status_code == 507
            assert_matches_schema(resp.get_json(), ERROR_SCHEMA)

    def test_status_success_shape(self, client, mock_redis):
        job_id = str(uuid.uuid4())
        mock_redis.hgetall.return_value = {
            'status': 'PENDING',
            'progress': '0',
            'filename': 'test.md',
            'from': 'markdown',
            'to': 'docx',
            'engine': 'pandoc',
            'created_at': str(time.time()),
        }
        resp = client.get(f'/api/v1/status/{job_id}')
        assert resp.status_code == 200
        assert_matches_schema(resp.get_json(), STATUS_SUCCESS_SCHEMA)

    def test_status_not_found_error_shape(self, client, mock_redis):
        mock_redis.hgetall.return_value = {}
        job_id = str(uuid.uuid4())
        resp = client.get(f'/api/v1/status/{job_id}')
        assert resp.status_code == 404
        assert_matches_schema(resp.get_json(), ERROR_SCHEMA)

    def test_formats_success_shape(self, client):
        resp = client.get('/api/v1/formats')
        assert resp.status_code == 200
        data = resp.get_json()
        assert_matches_schema(data, FORMATS_SCHEMA)
        # Verify list items have expected structure
        assert len(data['input_formats']) > 0
        assert 'key' in data['input_formats'][0]
        assert len(data['output_formats']) > 0
        assert 'key' in data['output_formats'][0]

    def test_extract_metadata_not_found_error_shape(self, client, mock_redis, api_headers):
        mock_redis.hgetall.return_value = {}
        job_id = str(uuid.uuid4())
        resp = client.post(f'/api/v1/jobs/{job_id}/extract-metadata', headers=api_headers)
        assert resp.status_code == 404
        assert_matches_schema(resp.get_json(), ERROR_SCHEMA)

    def test_extract_metadata_not_success_state_error_shape(self, client, mock_redis, api_headers):
        job_id = str(uuid.uuid4())
        mock_redis.hgetall.return_value = {
            'status': 'PENDING',
            'created_at': str(time.time()),
        }
        resp = client.post(f'/api/v1/jobs/{job_id}/extract-metadata', headers=api_headers)
        assert resp.status_code == 409
        assert_matches_schema(resp.get_json(), ERROR_SCHEMA)

    def test_download_not_found_error_shape(self, client, mock_redis, api_headers):
        mock_redis.hgetall.return_value = {}
        job_id = str(uuid.uuid4())
        resp = client.get(f'/api/v1/download/{job_id}', headers=api_headers)
        assert resp.status_code == 404
        assert_matches_schema(resp.get_json(), ERROR_SCHEMA)

    def test_download_not_completed_error_shape(self, client, mock_redis, api_headers):
        job_id = str(uuid.uuid4())
        mock_redis.hgetall.return_value = {
            'status': 'PENDING',
            'created_at': str(time.time()),
        }
        resp = client.get(f'/api/v1/download/{job_id}', headers=api_headers)
        assert resp.status_code == 404
        assert_matches_schema(resp.get_json(), ERROR_SCHEMA)


# ============================================================================
# Capture endpoint contracts
# ============================================================================

CAPTURE_SESSION_SCHEMA = {
    'session_id': str,
    'job_id': str,
    'status': str,
    'max_pages': int,
}

CAPTURE_PAGE_SCHEMA = {
    'status': str,
    'page_count': int,
}

CAPTURE_FINISH_SCHEMA = {
    'job_id': str,
    'status': str,
    'status_url': str,
}

CAPTURE_STATUS_SCHEMA = {
    'session_id': str,
    'status': str,
    'page_count': int,
    'title': str,
    'to_format': str,
}


class TestCaptureContracts:

    def test_create_session_success_shape(self, client, mock_redis):
        mock_redis.hset = Mock()
        mock_redis.lpush = Mock()
        mock_redis.ltrim = Mock()
        mock_redis.expire = Mock()

        resp = client.post('/api/v1/capture/sessions',
                           json={'title': 'Test', 'to_format': 'markdown'})
        assert resp.status_code == 201
        assert_matches_schema(resp.get_json(), CAPTURE_SESSION_SCHEMA)

    def test_add_page_success_shape(self, client, mock_redis):
        session_id = str(uuid.uuid4())
        mock_redis.hgetall.return_value = _session_meta()
        mock_redis.rpush = Mock(return_value=1)
        mock_redis.sismember = Mock(return_value=0)

        resp = client.post(f'/api/v1/capture/sessions/{session_id}/pages',
                           json={'text': '# Test', 'page_hint': 1})
        assert resp.status_code == 200
        assert_matches_schema(resp.get_json(), CAPTURE_PAGE_SCHEMA)

    def test_add_page_not_found_error_shape(self, client, mock_redis):
        mock_redis.hgetall.return_value = {}
        session_id = str(uuid.uuid4())
        resp = client.post(f'/api/v1/capture/sessions/{session_id}/pages',
                           json={'text': 'test'})
        assert resp.status_code == 404
        assert_matches_schema(resp.get_json(), ERROR_SCHEMA)

    def test_add_page_inactive_error_shape(self, client, mock_redis):
        session_id = str(uuid.uuid4())
        mock_redis.hgetall.return_value = _session_meta(status='assembling')
        resp = client.post(f'/api/v1/capture/sessions/{session_id}/pages',
                           json={'text': 'test'})
        assert resp.status_code == 409
        assert_matches_schema(resp.get_json(), ERROR_SCHEMA)

    def test_finish_success_shape(self, client, mock_redis, mock_celery):
        session_id = str(uuid.uuid4())
        mock_redis.hgetall.return_value = _session_meta(page_count='3')
        mock_celery.send_task = Mock()

        resp = client.post(f'/api/v1/capture/sessions/{session_id}/finish')
        assert resp.status_code == 202
        assert_matches_schema(resp.get_json(), CAPTURE_FINISH_SCHEMA)

    def test_finish_not_found_error_shape(self, client, mock_redis):
        mock_redis.hgetall.return_value = {}
        session_id = str(uuid.uuid4())
        resp = client.post(f'/api/v1/capture/sessions/{session_id}/finish')
        assert resp.status_code == 404
        assert_matches_schema(resp.get_json(), ERROR_SCHEMA)

    def test_finish_no_pages_error_shape(self, client, mock_redis):
        session_id = str(uuid.uuid4())
        mock_redis.hgetall.return_value = _session_meta(page_count='0')
        resp = client.post(f'/api/v1/capture/sessions/{session_id}/finish')
        assert resp.status_code == 422
        assert_matches_schema(resp.get_json(), ERROR_SCHEMA)

    def test_finish_already_finished_error_shape(self, client, mock_redis):
        session_id = str(uuid.uuid4())
        mock_redis.hgetall.return_value = _session_meta(status='assembling', page_count='5')
        resp = client.post(f'/api/v1/capture/sessions/{session_id}/finish')
        assert resp.status_code == 409
        assert_matches_schema(resp.get_json(), ERROR_SCHEMA)

    def test_session_status_success_shape(self, client, mock_redis):
        session_id = str(uuid.uuid4())
        mock_redis.hgetall.return_value = _session_meta()

        resp = client.get(f'/api/v1/capture/sessions/{session_id}/status')
        assert resp.status_code == 200
        assert_matches_schema(resp.get_json(), CAPTURE_STATUS_SCHEMA)

    def test_session_status_not_found_error_shape(self, client, mock_redis):
        mock_redis.hgetall.return_value = {}
        session_id = str(uuid.uuid4())
        resp = client.get(f'/api/v1/capture/sessions/{session_id}/status')
        assert resp.status_code == 404
        assert_matches_schema(resp.get_json(), ERROR_SCHEMA)


# ============================================================================
# Webhook endpoint contracts
# ============================================================================

WEBHOOK_REGISTER_SCHEMA = {
    'job_id': str,
    'webhook_url': str,
    'registered': bool,
}

WEBHOOK_GET_SCHEMA = {
    'job_id': str,
    'webhook_url': str,
}


class TestWebhookContracts:

    def test_register_success_shape(self, client, mock_redis, api_headers):
        job_id = str(uuid.uuid4())
        mock_redis.hgetall.return_value = {
            'status': 'PENDING',
            'created_at': str(time.time()),
        }
        mock_redis.hset = Mock()

        resp = client.post('/api/v1/webhooks',
                           json={'job_id': job_id, 'webhook_url': 'https://example.com/hook'},
                           headers=api_headers)
        assert resp.status_code == 201
        assert_matches_schema(resp.get_json(), WEBHOOK_REGISTER_SCHEMA)

    def test_register_missing_job_id_error_shape(self, client, mock_redis, api_headers):
        resp = client.post('/api/v1/webhooks',
                           json={'webhook_url': 'https://example.com/hook'},
                           headers=api_headers)
        assert resp.status_code == 400
        assert_matches_schema(resp.get_json(), ERROR_SCHEMA)

    def test_register_job_not_found_error_shape(self, client, mock_redis, api_headers):
        mock_redis.hgetall.return_value = {}
        job_id = str(uuid.uuid4())
        resp = client.post('/api/v1/webhooks',
                           json={'job_id': job_id, 'webhook_url': 'https://example.com/hook'},
                           headers=api_headers)
        assert resp.status_code == 404
        assert_matches_schema(resp.get_json(), ERROR_SCHEMA)

    def test_get_webhook_success_shape(self, client, mock_redis, api_headers):
        job_id = str(uuid.uuid4())
        mock_redis.hgetall.return_value = {
            'status': 'PENDING',
            'created_at': str(time.time()),
            'webhook_url': 'https://example.com/hook',
        }

        resp = client.get(f'/api/v1/webhooks/{job_id}', headers=api_headers)
        assert resp.status_code == 200
        assert_matches_schema(resp.get_json(), WEBHOOK_GET_SCHEMA)

    def test_get_webhook_not_found_error_shape(self, client, mock_redis, api_headers):
        mock_redis.hgetall.return_value = {}
        job_id = str(uuid.uuid4())
        resp = client.get(f'/api/v1/webhooks/{job_id}', headers=api_headers)
        assert resp.status_code == 404
        assert_matches_schema(resp.get_json(), ERROR_SCHEMA)

    def test_get_webhook_not_registered_error_shape(self, client, mock_redis, api_headers):
        job_id = str(uuid.uuid4())
        mock_redis.hgetall.return_value = {
            'status': 'PENDING',
            'created_at': str(time.time()),
            # no webhook_url
        }
        resp = client.get(f'/api/v1/webhooks/{job_id}', headers=api_headers)
        assert resp.status_code == 404
        assert_matches_schema(resp.get_json(), ERROR_SCHEMA)


# ============================================================================
# Auth endpoint contracts
# ============================================================================

CREATE_KEY_SCHEMA = {
    'api_key': str,
    'created_at': str,
    'label': str,
}

REVOKE_KEY_SCHEMA = {
    'revoked': bool,
}

DLQ_SCHEMA = {
    'count': int,
    'total': int,
    'entries': list,
}


class TestAuthContracts:

    def test_create_key_success_shape(self, client, mock_redis, admin_headers):
        mock_redis.hset = Mock()

        resp = client.post('/api/v1/auth/keys',
                           json={'label': 'test-key'},
                           headers=admin_headers)
        assert resp.status_code == 201
        assert_matches_schema(resp.get_json(), CREATE_KEY_SCHEMA)

    def test_create_key_no_auth_error_shape(self, client, mock_redis):
        resp = client.post('/api/v1/auth/keys', json={})
        assert resp.status_code == 401
        assert_matches_schema(resp.get_json(), ERROR_SCHEMA)

    def test_revoke_key_success_shape(self, client, mock_redis, admin_headers):
        mock_redis.delete.return_value = 1
        resp = client.delete('/api/v1/auth/keys/dk_testkey', headers=admin_headers)
        assert resp.status_code == 200
        assert_matches_schema(resp.get_json(), REVOKE_KEY_SCHEMA)

    def test_revoke_key_not_found_error_shape(self, client, mock_redis, admin_headers):
        mock_redis.delete.return_value = 0
        resp = client.delete('/api/v1/auth/keys/dk_nonexistent', headers=admin_headers)
        assert resp.status_code == 404
        assert_matches_schema(resp.get_json(), ERROR_SCHEMA)

    def test_dlq_success_shape(self, client, mock_redis, admin_headers):
        mock_redis.lrange.return_value = []
        mock_redis.llen.return_value = 0
        resp = client.get('/api/v1/admin/dlq', headers=admin_headers)
        assert resp.status_code == 200
        assert_matches_schema(resp.get_json(), DLQ_SCHEMA)

    def test_dlq_no_auth_error_shape(self, client, mock_redis):
        resp = client.get('/api/v1/admin/dlq')
        assert resp.status_code == 401
        assert_matches_schema(resp.get_json(), ERROR_SCHEMA)


# ============================================================================
# Health endpoint contracts
# ============================================================================

READYZ_SUCCESS_SCHEMA = {
    'status': str,
    'redis': str,
    'timestamp': (int, float),
}

HEALTH_DETAILED_SCHEMA = {
    'status': str,
    'timestamp': (int, float),
    'components': dict,
}

SERVICE_STATUS_SCHEMA = {
    'disk_space': str,
}


class TestHealthContracts:

    def test_healthz_returns_ok(self, client):
        resp = client.get('/healthz')
        assert resp.status_code == 200
        assert resp.data == b'OK'

    def test_readyz_success_shape(self, client, mock_redis):
        mock_redis.ping.return_value = True
        resp = client.get('/readyz')
        assert resp.status_code == 200
        assert_matches_schema(resp.get_json(), READYZ_SUCCESS_SCHEMA)

    def test_readyz_failure_error_shape(self, client, mock_redis):
        mock_redis.ping.side_effect = ConnectionError("Redis down")
        resp = client.get('/readyz')
        assert resp.status_code == 503
        data = resp.get_json()
        assert_matches_schema(data, {'status': str, 'error': str, 'timestamp': (int, float)})

    def test_health_detailed_success_shape(self, client, mock_redis):
        mock_redis.ping.return_value = True
        mock_redis.get.return_value = None
        mock_redis.hgetall.return_value = {}
        resp = client.get('/api/health')
        assert resp.status_code == 200
        data = resp.get_json()
        assert_matches_schema(data, HEALTH_DETAILED_SCHEMA)
        assert isinstance(data['components'], dict)

    def test_service_status_success_shape(self, client, mock_redis):
        mock_redis.get.return_value = None
        mock_redis.hgetall.return_value = {}
        with patch('web.app.check_disk_space', return_value=True):
            resp = client.get('/api/status/services')
        assert resp.status_code == 200
        assert_matches_schema(resp.get_json(), SERVICE_STATUS_SCHEMA)
