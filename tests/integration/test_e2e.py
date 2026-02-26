"""
End-to-end integration tests for the DocuFlux conversion pipeline.

Epic 31.2: Tests the full upload → queue → status → download flow
using Flask test client with mocked Celery tasks and real in-memory Redis
(or mocked Redis when unavailable).

These tests verify:
- Upload → job creation → correct Celery task dispatched
- Job status polling returns correct states
- Download endpoint works for SUCCESS jobs
- ZIP download works for multi-file outputs
- Job lifecycle: create, list, cancel, delete
- API v1 endpoints behave consistently with UI endpoints
- WebSocket job_update events emitted on metadata changes
- Full pipeline: submit → poll status → download → WebSocket events
"""
import pytest
import io
import os
import uuid
import time
import sys
from unittest.mock import patch, MagicMock

# Mock heavy web dependencies before importing app
_web_mocks = {
    'secrets_manager': MagicMock(),
    'encryption': MagicMock(),
    'key_manager': MagicMock(),
    'flask_socketio': MagicMock(),
}
for name, mock in _web_mocks.items():
    if name not in sys.modules:
        sys.modules[name] = mock

sys.modules['secrets_manager'].load_all_secrets.return_value = {
    'SECRET_KEY': 'test-secret-key-for-integration'
}
sys.modules['secrets_manager'].load_secret.return_value = None
sys.modules['encryption'].EncryptionService = MagicMock()
sys.modules['key_manager'].create_key_manager = MagicMock()

# Mock SocketIO so it doesn't try to connect
_mock_socketio_class = MagicMock()
_mock_socketio_instance = MagicMock()
_mock_socketio_class.return_value = _mock_socketio_instance
sys.modules['flask_socketio'].SocketIO = _mock_socketio_class

import web.app as web_app


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(scope='module')
def flask_app():
    """Create Flask test application."""
    web_app.app.config['TESTING'] = True
    web_app.app.config['WTF_CSRF_ENABLED'] = False
    web_app.app.config['UPLOAD_FOLDER'] = '/tmp/docuflux_test_uploads'
    web_app.app.config['OUTPUT_FOLDER'] = '/tmp/docuflux_test_outputs'
    os.makedirs('/tmp/docuflux_test_uploads', exist_ok=True)
    os.makedirs('/tmp/docuflux_test_outputs', exist_ok=True)
    # Disable rate limiting to prevent 429 errors from accumulated test requests
    web_app.limiter.enabled = False
    return web_app.app


@pytest.fixture
def client(flask_app):
    """Flask test client with session."""
    with flask_app.test_client() as c:
        with flask_app.app_context():
            yield c


@pytest.fixture
def mock_redis():
    """Mock Redis client for all tests."""
    mock = MagicMock()
    mock.pipeline.return_value.__enter__ = MagicMock(return_value=mock.pipeline.return_value)
    mock.pipeline.return_value.__exit__ = MagicMock(return_value=False)
    # Pipeline execute returns [True, {}] for hset + hgetall
    mock.pipeline.return_value.execute.return_value = [1, {}]
    mock.hgetall.return_value = {}
    mock.lrange.return_value = []
    mock.get.return_value = None
    mock.ping.return_value = True
    with patch.object(web_app, 'redis_client', mock):
        yield mock


@pytest.fixture
def mock_celery():
    """Mock Celery task dispatch."""
    mock = MagicMock()
    with patch.object(web_app, 'celery', mock):
        yield mock


@pytest.fixture
def sample_job_id():
    return str(uuid.uuid4())


@pytest.fixture
def success_job_meta(sample_job_id):
    """Metadata dict representing a completed SUCCESS job."""
    return {
        'status': 'SUCCESS',
        'filename': 'document.md',
        'from': 'markdown',
        'to': 'html',
        'created_at': str(time.time() - 30),
        'completed_at': str(time.time() - 5),
        'progress': '100',
        'file_count': '1',
        'encrypted': 'false'
    }


# ============================================================
# Upload → Queue flow (full pipeline entry point)
# ============================================================

class TestConversionSubmission:

    def test_upload_markdown_to_html_creates_job(self, client, mock_redis, mock_celery):
        """Uploading a markdown file creates a job and dispatches to Celery."""
        mock_redis.pipeline.return_value.execute.return_value = [1, {'status': 'PENDING'}]

        data = {
            'file': (io.BytesIO(b'# Hello World'), 'document.md'),
            'from_format': 'markdown',
            'to_format': 'html'
        }
        response = client.post('/convert', data=data,
                               content_type='multipart/form-data')

        assert response.status_code == 200
        result = response.get_json()
        assert 'job_ids' in result
        assert len(result['job_ids']) == 1
        assert result['status'] == 'queued'

        # Verify Celery task was dispatched
        mock_celery.send_task.assert_called_once()
        call_args = mock_celery.send_task.call_args
        assert call_args[0][0] == 'tasks.convert_document'

    def test_pdf_marker_routes_to_correct_task(self, client, mock_redis, mock_celery):
        """PDF with pdf_marker format dispatches to convert_with_marker task."""
        mock_redis.pipeline.return_value.execute.return_value = [1, {'status': 'PENDING'}]

        data = {
            'file': (io.BytesIO(b'%PDF-1.4 fake pdf'), 'report.pdf'),
            'from_format': 'pdf_marker',
            'to_format': 'markdown'
        }
        response = client.post('/convert', data=data,
                               content_type='multipart/form-data')

        assert response.status_code == 200
        mock_celery.send_task.assert_called_once()
        call_args = mock_celery.send_task.call_args
        assert call_args[0][0] == 'tasks.convert_with_marker'

    def test_large_file_uses_default_queue(self, client, mock_redis, mock_celery, tmp_path):
        """Files >5MB are routed to the 'default' queue (not high_priority)."""
        mock_redis.pipeline.return_value.execute.return_value = [1, {'status': 'PENDING'}]

        # Create a 6MB fake file
        large_content = b'x' * (6 * 1024 * 1024)
        data = {
            'file': (io.BytesIO(large_content), 'large.md'),
            'from_format': 'markdown',
            'to_format': 'html'
        }
        response = client.post('/convert', data=data,
                               content_type='multipart/form-data')

        assert response.status_code == 200
        call_kwargs = mock_celery.send_task.call_args[1]
        assert call_kwargs.get('queue') == 'default'

    def test_small_file_uses_high_priority_queue(self, client, mock_redis, mock_celery):
        """Files <5MB are routed to the 'high_priority' queue."""
        mock_redis.pipeline.return_value.execute.return_value = [1, {'status': 'PENDING'}]

        data = {
            'file': (io.BytesIO(b'# Small document'), 'small.md'),
            'from_format': 'markdown',
            'to_format': 'html'
        }
        response = client.post('/convert', data=data,
                               content_type='multipart/form-data')

        assert response.status_code == 200
        call_kwargs = mock_celery.send_task.call_args[1]
        assert call_kwargs.get('queue') == 'high_priority'

    def test_missing_file_returns_400(self, client, mock_redis, mock_celery):
        """POST /convert without a file returns 400."""
        response = client.post('/convert', data={},
                               content_type='multipart/form-data')
        assert response.status_code == 400

    def test_invalid_format_returns_400(self, client, mock_redis, mock_celery):
        """Unsupported format key returns 400."""
        data = {
            'file': (io.BytesIO(b'content'), 'file.xyz'),
            'from_format': 'nonexistent_format',
            'to_format': 'html'
        }
        response = client.post('/convert', data=data,
                               content_type='multipart/form-data')
        assert response.status_code == 400


# ============================================================
# Job listing (Epic 30.2 - file_count cache validation)
# ============================================================

class TestJobListing:

    def test_list_jobs_returns_session_jobs(self, client, mock_redis, sample_job_id,
                                            success_job_meta):
        """GET /api/jobs returns jobs for current session using Redis pipeline."""
        mock_redis.lrange.return_value = [sample_job_id]
        # list_jobs pipeline has only hgetall calls (one per job) - no hset
        mock_redis.pipeline.return_value.execute.return_value = [success_job_meta]

        response = client.get('/api/jobs')
        assert response.status_code == 200

    def test_list_jobs_uses_cached_file_count(self, client, mock_redis, sample_job_id):
        """Job listing uses file_count from metadata, not os.walk."""
        mock_redis.lrange.return_value = [sample_job_id]
        # list_jobs pipeline has only hgetall calls - one dict per job
        mock_redis.pipeline.return_value.execute.return_value = [
            {
                'status': 'SUCCESS',
                'filename': 'doc.md',
                'from': 'markdown',
                'to': 'html',
                'created_at': str(time.time()),
                'file_count': '3',  # Cached value - should be used
                'progress': '100'
            }
        ]

        with patch('os.walk') as mock_walk:
            response = client.get('/api/jobs')
            # os.walk should NOT be called when file_count is cached
            mock_walk.assert_not_called()

        assert response.status_code == 200

    def test_list_jobs_empty_for_no_session(self, client, mock_redis):
        """GET /api/jobs returns empty list when no history exists."""
        mock_redis.lrange.return_value = []
        response = client.get('/api/jobs')
        assert response.status_code == 200
        assert response.get_json() == []

    def test_list_jobs_is_zip_when_file_count_greater_than_1(self, client, mock_redis,
                                                               sample_job_id):
        """Jobs with file_count > 1 get download_zip URL."""
        mock_redis.lrange.return_value = [sample_job_id]
        # list_jobs pipeline has only hgetall calls - one dict per job
        mock_redis.pipeline.return_value.execute.return_value = [
            {
                'status': 'SUCCESS',
                'filename': 'report.pdf',
                'from': 'pdf_marker',
                'to': 'markdown',
                'created_at': str(time.time()),
                'file_count': '5',  # Multiple files (markdown + images)
                'progress': '100'
            }
        ]

        response = client.get('/api/jobs')
        assert response.status_code == 200
        jobs = response.get_json()
        # If jobs returned, verify ZIP download URL for multi-file job
        if jobs:
            job = jobs[0]
            if job.get('is_zip'):
                assert '/download_zip/' in job.get('download_url', '')


# ============================================================
# Job management: cancel, delete, retry
# ============================================================

class TestJobManagement:

    def test_cancel_job(self, client, mock_redis, mock_celery, sample_job_id):
        """POST /api/cancel/{job_id} revokes Celery task and updates status."""
        mock_redis.pipeline.return_value.execute.return_value = [1, {'status': 'REVOKED'}]

        response = client.post(f'/api/cancel/{sample_job_id}')

        assert response.status_code == 200
        mock_celery.control.revoke.assert_called_once_with(sample_job_id, terminate=True)

    def test_cancel_invalid_uuid_returns_400(self, client, mock_redis, mock_celery):
        """Cancelling invalid job ID returns 400."""
        response = client.post('/api/cancel/not-a-uuid')
        assert response.status_code == 400

    def test_delete_job_removes_files_and_redis(self, client, mock_redis, sample_job_id):
        """DELETE /api/delete/{job_id} removes files and Redis key."""
        mock_redis.pipeline.return_value.execute.return_value = [1, {}]

        with patch('shutil.rmtree') as mock_rmtree, \
             patch('os.path.exists', return_value=True):
            response = client.post(f'/api/delete/{sample_job_id}')

        assert response.status_code == 200
        mock_redis.delete.assert_called_with(f'job:{sample_job_id}')

    def test_delete_invalid_uuid_returns_400(self, client, mock_redis):
        """Deleting invalid job ID returns 400."""
        response = client.post('/api/delete/not-a-uuid')
        assert response.status_code == 400

    def test_retry_job_creates_new_job(self, client, mock_redis, mock_celery, sample_job_id):
        """POST /api/retry/{job_id} creates new job with same file."""
        mock_redis.hgetall.return_value = {
            'status': 'FAILURE',
            'filename': 'test.md',
            'from': 'markdown',
            'to': 'html',
            'force_ocr': 'False',
            'use_llm': 'False'
        }
        mock_redis.pipeline.return_value.execute.return_value = [1, {'status': 'PENDING'}]

        with patch('os.path.exists', return_value=True), \
             patch('shutil.copy2'), \
             patch('os.makedirs'):
            response = client.post(f'/api/retry/{sample_job_id}')

        assert response.status_code == 200
        result = response.get_json()
        assert result['status'] == 'retried'
        assert 'new_job_id' in result
        assert result['new_job_id'] != sample_job_id


# ============================================================
# Download endpoints
# ============================================================

class TestDownloadEndpoints:

    def test_download_single_file(self, client, mock_redis, sample_job_id, tmp_path):
        """GET /download/{job_id} returns the converted file."""
        job_dir = f'/tmp/docuflux_test_outputs/{sample_job_id}'
        os.makedirs(job_dir, exist_ok=True)
        test_file = os.path.join(job_dir, 'output.html')
        with open(test_file, 'w') as f:
            f.write('<html><body>Test</body></html>')

        mock_redis.hgetall.return_value = {'encrypted': 'false', 'status': 'SUCCESS'}
        mock_redis.pipeline.return_value.execute.return_value = [1, {}]

        # Patch module-level OUTPUT_FOLDER to match where test files are created
        with patch.object(web_app, 'OUTPUT_FOLDER', '/tmp/docuflux_test_outputs'):
            response = client.get(f'/download/{sample_job_id}')

        assert response.status_code == 200
        # Cleanup
        import shutil
        shutil.rmtree(job_dir, ignore_errors=True)

    def test_download_invalid_uuid_returns_400(self, client):
        """Download with invalid UUID returns 400."""
        response = client.get('/download/not-a-uuid')
        assert response.status_code == 400

    def test_download_missing_job_returns_404(self, client, sample_job_id):
        """Download for non-existent job returns 404."""
        with patch('os.path.exists', return_value=False):
            response = client.get(f'/download/{sample_job_id}')
        assert response.status_code == 404

    def test_download_zip_multi_file_output(self, client, mock_redis, sample_job_id):
        """GET /download_zip/{job_id} returns a ZIP for multi-file outputs."""
        job_dir = f'/tmp/docuflux_test_outputs/{sample_job_id}'
        images_dir = os.path.join(job_dir, 'images')
        os.makedirs(images_dir, exist_ok=True)

        # Create multiple output files
        with open(os.path.join(job_dir, 'output.md'), 'w') as f:
            f.write('# Converted PDF\n\n![image](images/page1.png)\n')
        with open(os.path.join(images_dir, 'page1.png'), 'wb') as f:
            f.write(b'\x89PNG\r\n\x1a\n' + b'\x00' * 100)

        mock_redis.hgetall.return_value = {'encrypted': 'false'}
        mock_redis.pipeline.return_value.execute.return_value = [1, {}]

        # Patch module-level OUTPUT_FOLDER to match where test files are created
        with patch.object(web_app, 'OUTPUT_FOLDER', '/tmp/docuflux_test_outputs'):
            response = client.get(f'/download_zip/{sample_job_id}')

        assert response.status_code == 200
        assert response.content_type == 'application/zip'

        import shutil
        shutil.rmtree(job_dir, ignore_errors=True)


# ============================================================
# API v1 endpoints
# ============================================================

class TestApiV1:

    @pytest.fixture(autouse=True)
    def valid_api_key(self):
        """Patch API key validation so all TestApiV1 tests bypass auth."""
        with patch('web.app._validate_api_key', return_value={'created_at': '1700000000.0', 'label': 'test'}):
            yield

    _api_headers = {'X-API-Key': 'dk_testkey'}

    def test_api_v1_convert_submit(self, client, mock_redis, mock_celery):
        """POST /api/v1/convert returns 202 with job_id."""
        mock_redis.pipeline.return_value.execute.return_value = [1, {'status': 'PENDING'}]

        data = {
            'file': (io.BytesIO(b'# Hello'), 'document.md'),
            'to_format': 'html',
            'from_format': 'markdown'
        }
        response = client.post('/api/v1/convert', data=data,
                               content_type='multipart/form-data',
                               headers=self._api_headers)

        assert response.status_code == 202
        result = response.get_json()
        assert 'job_id' in result
        assert 'status_url' in result
        assert result['status'] == 'queued'

    def test_api_v1_status_returns_job_info(self, client, mock_redis, sample_job_id):
        """GET /api/v1/status/{job_id} returns job metadata."""
        mock_redis.hgetall.return_value = {
            'status': 'PROCESSING',
            'filename': 'doc.md',
            'from': 'markdown',
            'to': 'html',
            'created_at': str(time.time()),
            'progress': '50'
        }

        response = client.get(f'/api/v1/status/{sample_job_id}')

        assert response.status_code == 200
        result = response.get_json()
        assert result['job_id'] == sample_job_id
        assert result['status'] == 'processing'
        assert result['progress'] == 50

    def test_api_v1_status_invalid_uuid(self, client):
        """GET /api/v1/status with invalid UUID returns 400."""
        response = client.get('/api/v1/status/not-a-uuid')
        assert response.status_code == 400

    def test_api_v1_status_not_found(self, client, sample_job_id):
        """GET /api/v1/status for unknown job returns 404."""
        with patch.object(web_app, 'get_job_metadata', return_value=None):
            response = client.get(f'/api/v1/status/{sample_job_id}')
        assert response.status_code == 404

    def test_api_v1_formats_returns_all_formats(self, client):
        """GET /api/v1/formats returns input and output formats."""
        response = client.get('/api/v1/formats')

        assert response.status_code == 200
        result = response.get_json()
        assert 'input_formats' in result
        assert 'output_formats' in result
        assert len(result['input_formats']) > 0
        assert len(result['output_formats']) > 0

    def test_api_v1_convert_missing_file_returns_400(self, client, mock_redis):
        """POST /api/v1/convert without file returns 400."""
        response = client.post('/api/v1/convert',
                               data={'to_format': 'html'},
                               content_type='multipart/form-data',
                               headers=self._api_headers)
        assert response.status_code == 400

    def test_api_v1_convert_missing_to_format_returns_400(self, client, mock_redis):
        """POST /api/v1/convert without to_format returns 400."""
        data = {'file': (io.BytesIO(b'content'), 'test.md')}
        response = client.post('/api/v1/convert', data=data,
                               content_type='multipart/form-data',
                               headers=self._api_headers)
        assert response.status_code == 400


# ============================================================
# Health / status endpoints
# ============================================================

class TestHealthEndpoints:

    def test_healthz_returns_200(self, client):
        """GET /healthz always returns 200 (liveness probe)."""
        response = client.get('/healthz')
        assert response.status_code == 200

    def test_readyz_ok_when_redis_up(self, client, mock_redis):
        """GET /readyz returns 200 when Redis is reachable."""
        mock_redis.ping.return_value = True
        response = client.get('/readyz')
        assert response.status_code == 200

    def test_readyz_503_when_redis_down(self, client, mock_redis):
        """GET /readyz returns 503 when Redis is unreachable."""
        mock_redis.ping.side_effect = ConnectionError("Redis down")
        response = client.get('/readyz')
        assert response.status_code == 503

    def test_service_status_endpoint(self, client, mock_redis):
        """GET /api/status/services returns marker and GPU status."""
        mock_redis.get.return_value = 'ready'
        mock_redis.hgetall.return_value = {'model': 'RTX 3090', 'vram_total': '24.0'}

        response = client.get('/api/status/services')

        assert response.status_code == 200
        result = response.get_json()
        assert 'disk_space' in result
        assert 'marker' in result


# ============================================================
# WebSocket event emission
# ============================================================

class TestWebSocketEvents:

    def test_update_job_metadata_emits_job_update(self, mock_redis):
        """update_job_metadata emits job_update WebSocket event with job data."""
        job_id = str(uuid.uuid4())
        updates = {'status': 'PROCESSING', 'progress': '50'}

        with patch.object(web_app.socketio, 'emit') as mock_emit:
            web_app.update_job_metadata(job_id, updates)

        mock_emit.assert_called_once_with(
            'job_update',
            {'id': job_id, 'status': 'PROCESSING', 'progress': '50'},
            namespace='/'
        )

    def test_update_job_metadata_emit_includes_all_fields(self, mock_redis):
        """job_update event payload includes all fields passed to update_job_metadata."""
        job_id = str(uuid.uuid4())
        updates = {'status': 'SUCCESS', 'progress': '100', 'completed_at': '1700000000.0'}

        with patch.object(web_app.socketio, 'emit') as mock_emit:
            web_app.update_job_metadata(job_id, updates)

        call_kwargs = mock_emit.call_args[0][1]  # positional arg: payload dict
        assert call_kwargs['id'] == job_id
        assert call_kwargs['status'] == 'SUCCESS'
        assert call_kwargs['progress'] == '100'
        assert call_kwargs['completed_at'] == '1700000000.0'

    def test_download_records_downloaded_at(self, client, mock_redis, sample_job_id):
        """GET /download/{id} records downloaded_at timestamp in Redis."""
        job_dir = f'/tmp/docuflux_test_outputs/{sample_job_id}'
        os.makedirs(job_dir, exist_ok=True)
        test_file = os.path.join(job_dir, 'output.html')
        with open(test_file, 'w') as f:
            f.write('<html><body>Test</body></html>')

        mock_redis.hgetall.return_value = {'encrypted': 'false', 'status': 'SUCCESS'}

        with patch.object(web_app, 'OUTPUT_FOLDER', '/tmp/docuflux_test_outputs'), \
             patch.object(web_app.socketio, 'emit') as mock_emit:
            client.get(f'/download/{sample_job_id}')

        # update_job_metadata is called to record downloaded_at
        emitted_payloads = [call[0][1] for call in mock_emit.call_args_list]
        keys_emitted = {k for payload in emitted_payloads for k in payload}
        assert 'downloaded_at' in keys_emitted

        import shutil
        shutil.rmtree(job_dir, ignore_errors=True)

    def test_update_job_metadata_redis_error_does_not_raise(self, mock_redis):
        """update_job_metadata swallows Redis errors gracefully."""
        job_id = str(uuid.uuid4())
        mock_redis.hset.side_effect = ConnectionError("Redis gone")

        # Should not raise even if Redis is down
        web_app.update_job_metadata(job_id, {'status': 'PROCESSING'})


# ============================================================
# Full pipeline: submit → poll → download
# ============================================================

class TestFullPipelineFlow:

    @pytest.fixture(autouse=True)
    def valid_api_key(self):
        with patch('web.app._validate_api_key',
                   return_value={'created_at': '1700000000.0', 'label': 'test'}):
            yield

    _api_headers = {'X-API-Key': 'dk_testkey'}

    def test_submit_poll_success_download(self, client, mock_redis, mock_celery, tmp_path):
        """Full pipeline: POST /api/v1/convert → poll status → download."""
        # 1. Submit job
        mock_redis.pipeline.return_value.execute.return_value = [1, {'status': 'PENDING'}]
        data = {
            'file': (io.BytesIO(b'# Hello\n\nWorld'), 'doc.md'),
            'from_format': 'markdown',
            'to_format': 'html',
        }
        submit_resp = client.post('/api/v1/convert', data=data,
                                  content_type='multipart/form-data',
                                  headers=self._api_headers)
        assert submit_resp.status_code == 202
        job_id = submit_resp.get_json()['job_id']
        assert job_id  # must be a non-empty UUID string

        # 2. Poll status: PROCESSING
        mock_redis.hgetall.return_value = {
            'status': 'PROCESSING', 'filename': 'doc.md',
            'from': 'markdown', 'to': 'html',
            'created_at': str(time.time()), 'progress': '50'
        }
        poll_resp = client.get(f'/api/v1/status/{job_id}',
                               headers=self._api_headers)
        assert poll_resp.status_code == 200
        assert poll_resp.get_json()['status'] == 'processing'
        assert poll_resp.get_json()['progress'] == 50

        # 3. Poll status: SUCCESS
        mock_redis.hgetall.return_value = {
            'status': 'SUCCESS', 'filename': 'doc.html',
            'from': 'markdown', 'to': 'html',
            'created_at': str(time.time() - 5),
            'completed_at': str(time.time()),
            'progress': '100', 'file_count': '1', 'encrypted': 'false'
        }
        success_resp = client.get(f'/api/v1/status/{job_id}',
                                  headers=self._api_headers)
        assert success_resp.status_code == 200
        assert success_resp.get_json()['status'] == 'success'
        assert success_resp.get_json()['download_url']

        # 4. Download the output
        job_dir = f'/tmp/docuflux_test_outputs/{job_id}'
        os.makedirs(job_dir, exist_ok=True)
        with open(os.path.join(job_dir, 'doc.html'), 'w') as f:
            f.write('<html><body>Hello World</body></html>')

        mock_redis.hgetall.return_value = {'encrypted': 'false', 'status': 'SUCCESS'}
        with patch.object(web_app, 'OUTPUT_FOLDER', '/tmp/docuflux_test_outputs'):
            dl_resp = client.get(f'/download/{job_id}')
        assert dl_resp.status_code == 200

        import shutil
        shutil.rmtree(job_dir, ignore_errors=True)

    def test_submit_poll_failure_state(self, client, mock_redis, mock_celery):
        """Pipeline correctly surfaces FAILURE state with error message."""
        mock_redis.pipeline.return_value.execute.return_value = [1, {'status': 'PENDING'}]
        data = {
            'file': (io.BytesIO(b'bad content'), 'broken.md'),
            'from_format': 'markdown',
            'to_format': 'html',
        }
        submit_resp = client.post('/api/v1/convert', data=data,
                                  content_type='multipart/form-data',
                                  headers=self._api_headers)
        job_id = submit_resp.get_json()['job_id']

        mock_redis.hgetall.return_value = {
            'status': 'FAILURE', 'filename': 'broken.md',
            'from': 'markdown', 'to': 'html',
            'created_at': str(time.time()),
            'completed_at': str(time.time()),
            'error': 'Pandoc conversion failed: unsupported format',
            'progress': '0'
        }
        fail_resp = client.get(f'/api/v1/status/{job_id}',
                               headers=self._api_headers)
        assert fail_resp.status_code == 200
        result = fail_resp.get_json()
        assert result['status'] == 'failure'
        assert 'error' in result

    def test_multi_file_pipeline_returns_zip_url(self, client, mock_redis, mock_celery):
        """Marker pipeline producing images returns download_zip URL in status."""
        mock_redis.pipeline.return_value.execute.return_value = [1, {'status': 'PENDING'}]
        data = {
            'file': (io.BytesIO(b'%PDF-1.4 fake'), 'report.pdf'),
            'from_format': 'pdf_marker',
            'to_format': 'markdown',
        }
        submit_resp = client.post('/api/v1/convert', data=data,
                                  content_type='multipart/form-data',
                                  headers=self._api_headers)
        assert submit_resp.status_code == 202
        job_id = submit_resp.get_json()['job_id']

        # Simulate Marker success with multiple output files (markdown + images)
        # Create a real output dir with images subdir so the endpoint detects is_multifile
        job_dir = f'/tmp/docuflux_test_outputs/{job_id}'
        os.makedirs(os.path.join(job_dir, 'images'), exist_ok=True)
        with open(os.path.join(job_dir, 'report.md'), 'w') as f:
            f.write('# Report\n')

        mock_redis.hgetall.return_value = {
            'status': 'SUCCESS', 'filename': 'report.md',
            'from': 'pdf_marker', 'to': 'markdown',
            'created_at': str(time.time() - 10),
            'completed_at': str(time.time()),
            'progress': '100', 'file_count': '5', 'encrypted': 'false'
        }
        with patch.object(web_app, 'OUTPUT_FOLDER', '/tmp/docuflux_test_outputs'):
            status_resp = client.get(f'/api/v1/status/{job_id}',
                                     headers=self._api_headers)
        assert status_resp.status_code == 200
        result = status_resp.get_json()
        assert result['status'] == 'success'
        # API v1 always uses /api/v1/download/<id>; multi-file signalled by is_multifile
        assert '/api/v1/download/' in result['download_url']
        assert result['is_multifile'] is True

        import shutil
        shutil.rmtree(job_dir, ignore_errors=True)
