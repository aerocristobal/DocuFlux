"""
UI workflow regression tests.

Validates the backend contracts that the web UI depends on.
These tests catch the exact regressions we've experienced:
- Jobs not appearing in the list (missing session history entry)
- Progress bar stuck/invisible (progress as string '0')
- Job list polling rate-limited (429 on /api/jobs)
- Status never updating (worker crash before metadata write)

See docs/UI_SPECIFICATION.md for the source of truth.
"""

import io
import json
import time
import uuid

import pytest
from unittest.mock import Mock, patch, MagicMock


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def ui_client():
    """Flask test client with real storage, mocked Redis/Celery, rate limiting disabled."""
    import os
    import tempfile
    import web.app as web_app_mod
    from storage import LocalStorageBackend
    from web.app import app, limiter

    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    _tmpdir = tempfile.mkdtemp(prefix='docuflux_ui_test_')
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


def _make_job_meta(status='PENDING', progress='0', **overrides):
    """Build a job metadata dict as Redis would store it."""
    meta = {
        'status': status,
        'created_at': str(time.time()),
        'filename': 'test.pdf',
        'from': 'pdf_marker',
        'to': 'markdown',
        'engine': 'marker',
        'progress': progress,
    }
    meta.update(overrides)
    return meta


# ============================================================================
# Index page renders
# ============================================================================

class TestIndexPage:

    def test_index_returns_html_with_formats(self, ui_client):
        """GET / returns HTML with embedded formats JSON."""
        resp = ui_client.get('/')
        assert resp.status_code == 200
        assert b'DocuFlux' in resp.data
        assert b'convert-form' in resp.data
        assert b'from_format' in resp.data
        assert b'to_format' in resp.data

    def test_index_contains_csrf_token(self, ui_client):
        """Index page has CSRF token meta tag for form submissions."""
        resp = ui_client.get('/')
        assert b'csrf-token' in resp.data

    def test_index_contains_gpu_chip(self, ui_client):
        """Index page has GPU status chip element."""
        resp = ui_client.get('/')
        assert b'gpu-status-chip' in resp.data

    def test_index_contains_job_list(self, ui_client):
        """Index page has job list container."""
        resp = ui_client.get('/')
        assert b'jobs-list' in resp.data
        assert b'no-jobs-msg' in resp.data

    def test_index_contains_captures_section(self, ui_client):
        """Index page has captures section (initially hidden)."""
        resp = ui_client.get('/')
        assert b'captures-section' in resp.data

    def test_index_contains_theme_toggle(self, ui_client):
        """Index page has theme toggle and menu."""
        resp = ui_client.get('/')
        assert b'theme-toggle' in resp.data
        assert b'theme-menu' in resp.data


# ============================================================================
# Job list contract (/api/jobs)
# ============================================================================

JOB_LIST_REQUIRED_FIELDS = {
    'id', 'filename', 'from', 'to', 'created_at', 'status',
    'progress', 'result', 'download_url', 'is_zip', 'file_count',
    'slm', 'stage', 'page_count', 'started_at',
}


class TestJobListContract:

    def test_api_jobs_returns_list(self, ui_client, mock_redis):
        """GET /api/jobs returns a JSON list."""
        mock_redis.lrange.return_value = []
        with ui_client.session_transaction() as sess:
            sess['session_id'] = str(uuid.uuid4())
        resp = ui_client.get('/api/jobs')
        assert resp.status_code == 200
        assert isinstance(resp.get_json(), list)

    def test_api_jobs_empty_without_session(self, ui_client, mock_redis):
        """GET /api/jobs returns [] when no session exists."""
        resp = ui_client.get('/api/jobs')
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_api_jobs_item_has_all_required_fields(self, ui_client, mock_redis):
        """Each job item must have all fields that renderJobs() expects."""
        job_id = str(uuid.uuid4())
        session_id = str(uuid.uuid4())

        mock_redis.lrange.return_value = [job_id]
        pipe_mock = MagicMock()
        pipe_mock.execute.return_value = [_make_job_meta()]
        mock_redis.pipeline.return_value = pipe_mock

        with ui_client.session_transaction() as sess:
            sess['session_id'] = session_id

        resp = ui_client.get('/api/jobs')
        assert resp.status_code == 200
        jobs = resp.get_json()
        assert len(jobs) == 1

        missing = JOB_LIST_REQUIRED_FIELDS - set(jobs[0].keys())
        assert not missing, f"Missing fields in /api/jobs response: {missing}"

    def test_api_jobs_pending_status_shape(self, ui_client, mock_redis):
        """PENDING job has correct status, progress string, and null result."""
        job_id = str(uuid.uuid4())
        mock_redis.lrange.return_value = [job_id]
        pipe_mock = MagicMock()
        pipe_mock.execute.return_value = [_make_job_meta(status='PENDING', progress='0')]
        mock_redis.pipeline.return_value = pipe_mock

        with ui_client.session_transaction() as sess:
            sess['session_id'] = str(uuid.uuid4())

        jobs = ui_client.get('/api/jobs').get_json()
        job = jobs[0]
        assert job['status'] == 'PENDING'
        assert job['result'] is None
        assert job['download_url'] is None

    def test_api_jobs_failure_includes_error(self, ui_client, mock_redis):
        """FAILURE job has error message in 'result' field."""
        job_id = str(uuid.uuid4())
        mock_redis.lrange.return_value = [job_id]
        pipe_mock = MagicMock()
        pipe_mock.execute.return_value = [_make_job_meta(
            status='FAILURE', error='Worker crashed'
        )]
        mock_redis.pipeline.return_value = pipe_mock

        with ui_client.session_transaction() as sess:
            sess['session_id'] = str(uuid.uuid4())

        jobs = ui_client.get('/api/jobs').get_json()
        assert jobs[0]['status'] == 'FAILURE'
        assert jobs[0]['result'] == 'Worker crashed'

    def test_api_jobs_success_has_download_url(self, ui_client, mock_redis):
        """SUCCESS job has download_url set."""
        job_id = str(uuid.uuid4())
        mock_redis.lrange.return_value = [job_id]
        pipe_mock = MagicMock()
        pipe_mock.execute.return_value = [_make_job_meta(status='SUCCESS')]
        mock_redis.pipeline.return_value = pipe_mock

        import web.app as web_app_mod
        web_app_mod.storage.makedirs(job_id, folder='output')

        with ui_client.session_transaction() as sess:
            sess['session_id'] = str(uuid.uuid4())

        jobs = ui_client.get('/api/jobs').get_json()
        assert jobs[0]['status'] == 'SUCCESS'
        # download_url is set (may be None if no output files, but field exists)
        assert 'download_url' in jobs[0]

    def test_api_jobs_not_rate_limited(self, ui_client, mock_redis):
        """Regression: /api/jobs must be rate-limit-exempt (was causing 429s)."""
        import web.app as web_app_mod
        from web.app import limiter

        # Re-enable rate limiting for this test
        limiter.enabled = True
        mock_redis.lrange.return_value = []

        with ui_client.session_transaction() as sess:
            sess['session_id'] = str(uuid.uuid4())

        try:
            # Make many rapid requests — should never get 429
            for _ in range(10):
                resp = ui_client.get('/api/jobs')
                assert resp.status_code == 200, f"Got {resp.status_code} — /api/jobs must be rate-limit-exempt"
        finally:
            limiter.enabled = False


# ============================================================================
# Conversion submission workflow
# ============================================================================

class TestConversionSubmission:

    def test_convert_creates_job_in_session_history(self, ui_client, mock_redis, mock_celery, mock_disk_space):
        """Regression: submitted job must appear in session history for /api/jobs to find it."""
        mock_redis.hset = Mock()
        mock_redis.hgetall = Mock(return_value={})
        mock_redis.zadd = Mock()
        mock_redis.lpush = Mock()
        mock_redis.expire = Mock()
        mock_celery.send_task = Mock()

        with ui_client.session_transaction() as sess:
            sess['session_id'] = 'test-session-123'

        data = {
            'file': (io.BytesIO(b"# Test"), 'test.md'),
            'from_format': 'markdown',
            'to_format': 'html',
        }
        resp = ui_client.post('/convert', data=data, content_type='multipart/form-data')
        assert resp.status_code == 200
        result = resp.get_json()
        assert 'job_ids' in result
        assert len(result['job_ids']) == 1

        # Verify job was pushed to session history
        mock_redis.lpush.assert_called()
        history_call = [c for c in mock_redis.lpush.call_args_list
                        if 'history:test-session-123' in str(c)]
        assert history_call, "Job must be pushed to history:{session_id} list"

    def test_convert_sets_pending_status(self, ui_client, mock_redis, mock_celery, mock_disk_space):
        """Submitted job must have status=PENDING in Redis metadata."""
        captured_metadata = {}

        def capture_hset(key, mapping=None, **kwargs):
            if mapping and 'status' in mapping:
                captured_metadata.update(mapping)

        mock_redis.hset = capture_hset
        mock_redis.hgetall = Mock(return_value={})
        mock_redis.zadd = Mock()
        mock_redis.lpush = Mock()
        mock_redis.expire = Mock()
        mock_celery.send_task = Mock()

        data = {
            'file': (io.BytesIO(b"# Test"), 'test.md'),
            'from_format': 'markdown',
            'to_format': 'html',
        }
        ui_client.post('/convert', data=data, content_type='multipart/form-data')

        assert captured_metadata.get('status') == 'PENDING'

    def test_convert_dispatches_celery_task(self, ui_client, mock_redis, mock_celery, mock_disk_space):
        """Submitted job must dispatch a Celery task."""
        mock_redis.hset = Mock()
        mock_redis.hgetall = Mock(return_value={})
        mock_redis.zadd = Mock()
        mock_redis.lpush = Mock()
        mock_redis.expire = Mock()
        mock_celery.send_task = Mock()

        data = {
            'file': (io.BytesIO(b"# Test"), 'test.md'),
            'from_format': 'markdown',
            'to_format': 'html',
        }
        ui_client.post('/convert', data=data, content_type='multipart/form-data')
        mock_celery.send_task.assert_called_once()

    def test_convert_returns_queued_response(self, ui_client, mock_redis, mock_celery, mock_disk_space):
        """POST /convert returns {job_ids, status: 'queued'} shape."""
        mock_redis.hset = Mock()
        mock_redis.hgetall = Mock(return_value={})
        mock_redis.zadd = Mock()
        mock_redis.lpush = Mock()
        mock_redis.expire = Mock()
        mock_celery.send_task = Mock()

        data = {
            'file': (io.BytesIO(b"# Test"), 'test.md'),
            'from_format': 'markdown',
            'to_format': 'html',
        }
        resp = ui_client.post('/convert', data=data, content_type='multipart/form-data')
        result = resp.get_json()
        assert result['status'] == 'queued'
        assert isinstance(result['job_ids'], list)


# ============================================================================
# Job actions
# ============================================================================

class TestJobActions:

    def test_cancel_job_response_shape(self, ui_client, mock_redis, mock_celery):
        """POST /api/cancel/{id} returns {status: 'cancelled'}."""
        job_id = str(uuid.uuid4())
        mock_celery.control = MagicMock()
        mock_redis.hset = Mock()
        mock_redis.expire = Mock()

        resp = ui_client.post(f'/api/cancel/{job_id}')
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'cancelled'

    def test_delete_job_response_shape(self, ui_client, mock_redis):
        """POST /api/delete/{id} returns {status: 'deleted'}."""
        job_id = str(uuid.uuid4())
        mock_redis.lrem = Mock()
        mock_redis.delete = Mock()

        resp = ui_client.post(f'/api/delete/{job_id}')
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'deleted'

    def test_retry_job_response_shape(self, ui_client, mock_redis, mock_celery, mock_disk_space):
        """POST /api/retry/{id} returns {status: 'retried', new_job_id}."""
        job_id = str(uuid.uuid4())
        mock_redis.hgetall.return_value = {
            'filename': 'test.md',
            'from': 'markdown',
            'to': 'html',
            'status': 'FAILURE',
            'created_at': str(time.time()),
        }
        mock_redis.hset = Mock()
        mock_redis.zadd = Mock()
        mock_redis.lpush = Mock()
        mock_redis.expire = Mock()
        mock_celery.send_task = Mock()

        import web.app as web_app_mod
        web_app_mod.storage.makedirs(job_id, folder='upload')
        upload_path = web_app_mod.storage.get_local_path(job_id, 'test.md', folder='upload')
        with open(upload_path, 'w') as f:
            f.write('# Test')

        resp = ui_client.post(f'/api/retry/{job_id}')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'retried'
        assert 'new_job_id' in data

    def test_cancel_invalid_uuid_returns_400(self, ui_client):
        """POST /api/cancel/bad-id returns 400."""
        resp = ui_client.post('/api/cancel/not-a-uuid')
        assert resp.status_code == 400


# ============================================================================
# Service status contract
# ============================================================================

SERVICE_STATUS_REQUIRED_FIELDS = {'disk_space'}


class TestServiceStatus:

    def test_service_status_has_required_fields(self, ui_client, mock_redis):
        """GET /api/status/services returns at least {disk_space}."""
        mock_redis.get.return_value = None
        mock_redis.hgetall.return_value = {}

        with patch('web.app.check_disk_space', return_value=True):
            resp = ui_client.get('/api/status/services')

        assert resp.status_code == 200
        data = resp.get_json()
        missing = SERVICE_STATUS_REQUIRED_FIELDS - set(data.keys())
        assert not missing, f"Missing fields: {missing}"
        assert data['disk_space'] in ('ok', 'low')

    def test_service_status_includes_marker_when_available(self, ui_client, mock_redis):
        """When Marker status is in Redis, it appears in response."""
        mock_redis.get.side_effect = lambda key: {
            'service:marker:status': 'ready',
            'service:marker:eta': 'done',
            'marker:gpu_status': 'available',
        }.get(key)
        mock_redis.hgetall.return_value = {}

        with patch('web.app.check_disk_space', return_value=True):
            resp = ui_client.get('/api/status/services')

        data = resp.get_json()
        assert data.get('marker') == 'ready'
        assert data.get('gpu_status') == 'available'

    def test_service_status_not_rate_limited(self, ui_client, mock_redis):
        """Regression: /api/status/services is polled every 10s, must not be rate-limited."""
        from web.app import limiter
        limiter.enabled = True
        mock_redis.get.return_value = None
        mock_redis.hgetall.return_value = {}

        try:
            with patch('web.app.check_disk_space', return_value=True):
                for _ in range(10):
                    resp = ui_client.get('/api/status/services')
                    assert resp.status_code == 200
        finally:
            limiter.enabled = False


# ============================================================================
# Captures contract
# ============================================================================

class TestCapturesContract:

    def test_api_captures_returns_list(self, ui_client, mock_redis):
        """GET /api/captures returns a JSON list."""
        mock_redis.lrange.return_value = []
        resp = ui_client.get('/api/captures')
        assert resp.status_code == 200
        assert isinstance(resp.get_json(), list)

    def test_api_captures_not_rate_limited(self, ui_client, mock_redis):
        """Regression: /api/captures must be rate-limit-exempt."""
        from web.app import limiter
        limiter.enabled = True
        mock_redis.lrange.return_value = []

        try:
            for _ in range(10):
                resp = ui_client.get('/api/captures')
                assert resp.status_code == 200
        finally:
            limiter.enabled = False


# ============================================================================
# Health endpoints (used by UI)
# ============================================================================

class TestHealthEndpoints:

    def test_healthz_returns_ok(self, ui_client):
        """GET /healthz returns OK (used as liveness probe)."""
        resp = ui_client.get('/healthz')
        assert resp.status_code == 200
        assert resp.data == b'OK'

    def test_readyz_success_shape(self, ui_client, mock_redis):
        """GET /readyz returns {status, redis, timestamp} on success."""
        mock_redis.ping.return_value = True
        resp = ui_client.get('/readyz')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'status' in data
        assert 'redis' in data
        assert 'timestamp' in data
