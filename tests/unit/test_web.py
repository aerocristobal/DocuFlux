import pytest
import json
import io
import uuid
from datetime import datetime, timezone, timedelta
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

    response = client.get('/api/jobs')
    assert response.status_code == 200
    assert len(response.json) == 1
    assert response.json[0]['status'] == 'SUCCESS'
    assert response.json[0]['download_url'] == f'/download/{valid_job_id}'

@patch('app.celery')
@patch('app.redis_client')
def test_cancel_job(mock_redis, mock_celery, client, valid_job_id):
    mock_pipe = MagicMock()
    mock_redis.pipeline.return_value = mock_pipe
    mock_pipe.execute.return_value = [1, {'status': 'REVOKED'}]

    response = client.post(f'/api/cancel/{valid_job_id}')
    assert response.status_code == 200
    mock_celery.control.revoke.assert_called_with(valid_job_id, terminate=True)
    mock_redis.pipeline.assert_called()

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
    mock_redis.pipeline.assert_called()
