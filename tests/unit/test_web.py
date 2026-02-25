import pytest
import io
import uuid
from unittest.mock import patch, MagicMock

@pytest.fixture
def valid_job_id():
    return str(uuid.uuid4())

def test_index(client):
    response = client.get('/')
    assert response.status_code == 200
    assert b"DocuFlux" in response.data or b"format" in response.data

@patch('app.redis_client')
def test_service_status(mock_redis, client):
    # Return bytes strings as real Redis would
    mock_redis.get.side_effect = lambda key: {
        'service:marker:status': 'ready',
        'service:marker:eta': 'done',
        'marker:gpu_status': 'available',
    }.get(key)
    mock_redis.hgetall.return_value = {}

    response = client.get('/api/status/services')
    assert response.status_code == 200
    data = response.json
    assert data['disk_space'] == 'ok'
    assert data['marker'] == 'ready'
    assert data['gpu_status'] == 'available'

def test_convert_no_file(client):
    response = client.post('/convert', data={})
    assert response.status_code == 400
    assert b"No file part" in response.data

def test_convert_no_selected_file(client):
    data = {'file': (io.BytesIO(b""), "")}
    response = client.post('/convert', data=data, content_type='multipart/form-data')
    assert response.status_code == 400
    assert b"No selected file" in response.data

def test_convert_missing_formats(client):
    data = {'file': (io.BytesIO(b"content"), "test.md")}
    response = client.post('/convert', data=data, content_type='multipart/form-data')
    assert response.status_code == 400
    assert b"Missing format selection" in response.data

def test_convert_invalid_extension(client):
    data = {
        'file': (io.BytesIO(b"content"), "test.txt"),
        'from_format': 'markdown',
        'to_format': 'html'
    }
    response = client.post('/convert', data=data, content_type='multipart/form-data')
    assert response.status_code == 400
    # Error message: "Extension .txt mismatch."
    assert b"mismatch" in response.data

@patch('os.path.getsize')
@patch('magic.Magic')
@patch('app.check_disk_space')
@patch('app.redis_client')
@patch('app.celery')
def test_convert_success(mock_celery, mock_redis, mock_disk, mock_magic, mock_getsize, client):
    mock_disk.return_value = True
    mock_getsize.return_value = 100  # Small file, goes to high_priority queue

    # Mock pipeline for update_job_metadata
    mock_pipe = MagicMock()
    mock_redis.pipeline.return_value = mock_pipe
    mock_pipe.execute.return_value = [1, {'status': 'PENDING'}]

    data = {
        'file': (io.BytesIO(b"# Hello"), "test.md"),
        'from_format': 'markdown',
        'to_format': 'html'
    }

    with patch('os.makedirs'), patch('werkzeug.datastructures.FileStorage.save'):
        response = client.post('/convert', data=data, content_type='multipart/form-data')

    assert response.status_code == 200
    assert 'job_ids' in response.json
    assert response.json['status'] == 'queued'

    mock_celery.send_task.assert_called_once()
    args, kwargs = mock_celery.send_task.call_args
    assert args[0] == 'tasks.convert_document'

@patch('app.check_disk_space')
def test_convert_disk_full(mock_disk, client):
    mock_disk.return_value = False
    response = client.post('/convert')
    assert response.status_code == 507
    assert b"Server storage is full" in response.data

@patch('app.redis_client')
def test_list_jobs_empty(mock_redis, client):
    mock_redis.lrange.return_value = []
    response = client.get('/api/jobs')
    assert response.status_code == 200
    assert response.json == []

@patch('app.redis_client')
def test_list_jobs_with_data(mock_redis, client, valid_job_id):
    # decode_responses=True means Redis returns strings, not bytes
    mock_redis.lrange.return_value = [valid_job_id]

    mock_pipe = MagicMock()
    mock_redis.pipeline.return_value = mock_pipe
    mock_pipe.execute.return_value = [{
        'status': 'SUCCESS',
        'filename': 'test.md',
        'from': 'markdown',
        'to': 'html',
        'created_at': '1700000000.0',
        'progress': '100',
        'file_count': '1',
    }]

    # /api/jobs requires session_id to be set
    with client.session_transaction() as sess:
        sess['session_id'] = 'test-session-id'

    response = client.get('/api/jobs')
    assert response.status_code == 200
    assert len(response.json) == 1
    assert response.json[0]['status'] == 'SUCCESS'
    assert response.json[0]['download_url'] == f'/download/{valid_job_id}'

@patch('app.celery')
@patch('app.redis_client')
def test_cancel_job(mock_redis, mock_celery, client, valid_job_id):
    response = client.post(f'/api/cancel/{valid_job_id}')
    assert response.status_code == 200
    mock_celery.control.revoke.assert_called_with(valid_job_id, terminate=True)
    mock_redis.expire.assert_called_with(f"job:{valid_job_id}", 600)

@patch('shutil.rmtree')
@patch('app.redis_client')
def test_delete_job(mock_redis, mock_rmtree, client, valid_job_id):
    response = client.post(f'/api/delete/{valid_job_id}')
    assert response.status_code == 200
    assert response.json['status'] == 'deleted'
    mock_redis.delete.assert_called_with(f'job:{valid_job_id}')

@patch('os.path.exists')
@patch('shutil.copy2')
@patch('app.redis_client')
@patch('app.celery')
def test_retry_job(mock_celery, mock_redis, mock_copy, mock_exists, client, valid_job_id):
    mock_exists.return_value = True

    # decode_responses=True means all values are strings
    mock_redis.hgetall.return_value = {
        'filename': 'test.md',
        'from': 'markdown',
        'to': 'html',
        'force_ocr': 'False',
        'use_llm': 'False',
    }
    mock_pipe = MagicMock()
    mock_redis.pipeline.return_value = mock_pipe
    mock_pipe.execute.return_value = [1, {'status': 'PENDING'}]

    with patch('os.makedirs'):
        response = client.post(f'/api/retry/{valid_job_id}')

    assert response.status_code == 200
    assert response.json['status'] == 'retried'
    assert 'new_job_id' in response.json

    mock_celery.send_task.assert_called()


# ============================================================
# Capture endpoint tests (docuflux-m13: streaming batch OCR)
# ============================================================

class TestCaptureCreateSession:

    @patch('app.os.makedirs')
    @patch('app.redis_client')
    def test_preallocates_job_id(self, mock_redis, mock_makedirs, client):
        """Session creation pre-allocates job_id and returns it in response."""
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        mock_pipe.execute.return_value = [1, {}]

        response = client.post('/api/v1/capture/sessions',
                               json={'title': 'Test Book', 'force_ocr': True})
        assert response.status_code == 201
        data = response.json
        assert 'session_id' in data
        assert 'job_id' in data
        assert data['job_id'] is not None
        assert data['status'] == 'active'

    @patch('app.os.makedirs')
    @patch('app.redis_client')
    def test_creates_batches_dir(self, mock_redis, mock_makedirs, client):
        """Session creation creates the batches staging directory."""
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        mock_pipe.execute.return_value = [1, {}]

        client.post('/api/v1/capture/sessions', json={'title': 'Test Book'})
        # makedirs should have been called with a path containing 'batches'
        calls = [str(c) for c in mock_makedirs.call_args_list]
        assert any('batches' in c for c in calls)

    @patch('app.os.makedirs')
    @patch('app.redis_client')
    def test_session_hash_has_batch_fields(self, mock_redis, mock_makedirs, client):
        """Session hash includes batch tracking fields at creation."""
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        mock_pipe.execute.return_value = [1, {}]

        client.post('/api/v1/capture/sessions', json={'title': 'Test Book'})
        hset_calls = mock_redis.hset.call_args_list
        # Find the call that sets session data
        session_mapping = None
        for call in hset_calls:
            args, kwargs = call
            if 'mapping' in kwargs and 'batches_queued' in kwargs['mapping']:
                session_mapping = kwargs['mapping']
                break
        assert session_mapping is not None
        assert session_mapping['batches_queued'] == '0'
        assert session_mapping['batches_done'] == '0'
        assert session_mapping['next_batch_start'] == '0'


class TestCaptureAddPage:

    def _make_session_meta(self, page_count=0, force_ocr=False, batches_queued=0,
                           next_batch_start=0, job_id=None):
        return {
            'status': 'active',
            'page_count': str(page_count),
            'force_ocr': str(force_ocr),
            'batches_queued': str(batches_queued),
            'next_batch_start': str(next_batch_start),
            'job_id': job_id or str(uuid.uuid4()),
        }

    @patch('app.celery')
    @patch('app.redis_client')
    def test_triggers_batch_at_threshold(self, mock_redis, mock_celery, client, app):
        """Adding the Nth page (= batch_size) dispatches a batch task."""
        import web.app as web_app
        batch_size = web_app.app_settings.capture_batch_size  # typically 50
        session_id = str(uuid.uuid4())
        job_id = str(uuid.uuid4())

        # Simulate being 1 page short of threshold
        mock_redis.hgetall.return_value = self._make_session_meta(
            page_count=batch_size - 1,
            force_ocr=True,
            next_batch_start=0,
            job_id=job_id,
        )

        response = client.post(
            f'/api/v1/capture/sessions/{session_id}/pages',
            json={'text': 'page content', 'images': []}
        )
        assert response.status_code == 200
        # A batch task should have been dispatched
        mock_celery.send_task.assert_called_once()
        args, kwargs = mock_celery.send_task.call_args
        assert args[0] == 'tasks.process_capture_batch'
        task_args = args[1] if len(args) > 1 else kwargs.get('args', [])
        assert task_args[0] == session_id
        assert task_args[1] == job_id

    @patch('app.celery')
    @patch('app.redis_client')
    def test_no_batch_below_threshold(self, mock_redis, mock_celery, client):
        """Adding pages below the batch threshold does not dispatch a batch task."""
        session_id = str(uuid.uuid4())
        mock_redis.hgetall.return_value = self._make_session_meta(
            page_count=5, force_ocr=True, next_batch_start=0
        )

        response = client.post(
            f'/api/v1/capture/sessions/{session_id}/pages',
            json={'text': 'page content'}
        )
        assert response.status_code == 200
        mock_celery.send_task.assert_not_called()

    @patch('app.celery')
    @patch('app.redis_client')
    def test_no_batch_for_text_sessions(self, mock_redis, mock_celery, client, app):
        """Batch dispatch does not occur for non-OCR (text) sessions even at threshold."""
        import web.app as web_app
        batch_size = web_app.app_settings.capture_batch_size
        session_id = str(uuid.uuid4())
        mock_redis.hgetall.return_value = self._make_session_meta(
            page_count=batch_size - 1,
            force_ocr=False,
            next_batch_start=0,
        )

        response = client.post(
            f'/api/v1/capture/sessions/{session_id}/pages',
            json={'text': 'page content'}
        )
        assert response.status_code == 200
        mock_celery.send_task.assert_not_called()


class TestCaptureFinishSession:

    @patch('app.celery')
    @patch('app.redis_client')
    def test_uses_preallocated_job_id(self, mock_redis, mock_celery, client):
        """Finish reads job_id from Redis, does not generate a new one."""
        session_id = str(uuid.uuid4())
        preallocated_job_id = str(uuid.uuid4())

        mock_redis.hgetall.return_value = {
            'status': 'active',
            'page_count': '10',
            'force_ocr': 'false',
            'next_batch_start': '0',
            'batches_queued': '0',
            'job_id': preallocated_job_id,
            'title': 'My Book',
            'to_format': 'markdown',
        }

        response = client.post(f'/api/v1/capture/sessions/{session_id}/finish', json={})
        assert response.status_code == 202
        assert response.json['job_id'] == preallocated_job_id

    @patch('app.celery')
    @patch('app.redis_client')
    def test_dispatches_remainder_batch_for_ocr_session(self, mock_redis, mock_celery, client):
        """Finish dispatches a remainder batch for unprocessed pages in OCR sessions."""
        session_id = str(uuid.uuid4())
        job_id = str(uuid.uuid4())

        mock_redis.hgetall.return_value = {
            'status': 'active',
            'page_count': '55',
            'force_ocr': 'true',
            'next_batch_start': '50',  # 5 pages not yet in a batch
            'batches_queued': '1',
            'job_id': job_id,
            'title': 'My Book',
            'to_format': 'markdown',
        }

        response = client.post(f'/api/v1/capture/sessions/{session_id}/finish', json={})
        assert response.status_code == 202

        # Should have dispatched: 1 remainder batch + 1 assemble task
        assert mock_celery.send_task.call_count == 2
        task_names = [c.args[0] for c in mock_celery.send_task.call_args_list]
        assert 'tasks.process_capture_batch' in task_names
        assert 'tasks.assemble_capture_session' in task_names

    @patch('app.celery')
    @patch('app.redis_client')
    def test_no_remainder_batch_when_all_pages_covered(self, mock_redis, mock_celery, client):
        """Finish skips remainder batch when all pages are already in batches."""
        session_id = str(uuid.uuid4())
        job_id = str(uuid.uuid4())

        mock_redis.hgetall.return_value = {
            'status': 'active',
            'page_count': '50',
            'force_ocr': 'true',
            'next_batch_start': '50',  # already covered
            'batches_queued': '1',
            'job_id': job_id,
            'title': 'My Book',
            'to_format': 'markdown',
        }

        response = client.post(f'/api/v1/capture/sessions/{session_id}/finish', json={})
        assert response.status_code == 202

        # Only assemble should be dispatched (no remainder batch)
        assert mock_celery.send_task.call_count == 1
        assert mock_celery.send_task.call_args.args[0] == 'tasks.assemble_capture_session'

    @patch('app.redis_client')
    def test_missing_job_id_returns_500(self, mock_redis, client):
        """Finish returns 500 if session is missing the pre-allocated job_id."""
        session_id = str(uuid.uuid4())
        mock_redis.hgetall.return_value = {
            'status': 'active',
            'page_count': '5',
            'force_ocr': 'false',
            # No job_id field
        }

        response = client.post(f'/api/v1/capture/sessions/{session_id}/finish', json={})
        assert response.status_code == 500
