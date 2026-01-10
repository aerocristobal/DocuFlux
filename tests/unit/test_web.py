import pytest
import json
import io
from unittest.mock import patch, MagicMock

def test_index(client):
    response = client.get('/')
    assert response.status_code == 200
    assert b"Pandoc Web" in response.data or b"format" in response.data

def test_service_status(client):
    with patch('requests.get') as mock_get:
        mock_get.return_value.status_code = 200
        response = client.get('/api/status/services')
        assert response.status_code == 200
        data = response.json
        assert data['marker_api'] == 'available'
        assert data['disk_space'] == 'ok'

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
    # .txt is not .md
    assert b"does not match selected format" in response.data

@patch('magic.Magic')
@patch('app.check_disk_space')
@patch('app.redis_client')
@patch('app.celery')
def test_convert_success(mock_celery, mock_redis, mock_disk, mock_magic, client):
    mock_disk.return_value = True
    
    # Mock magic to return text/markdown
    mock_mime = MagicMock()
    mock_mime.from_buffer.return_value = "text/markdown"
    mock_magic.return_value = mock_mime

    data = {
        'file': (io.BytesIO(b"# Hello"), "test.md"),
        'from_format': 'markdown',
        'to_format': 'html'
    }
    
    # Need to mock os.makedirs and file.save to avoid writing to disk
    with patch('os.makedirs'), patch('werkzeug.datastructures.FileStorage.save'):
        response = client.post('/convert', data=data, content_type='multipart/form-data')
    
    assert response.status_code == 200
    assert 'job_id' in response.json
    assert response.json['status'] == 'queued'
    
    # Verify celery task was called
    mock_celery.send_task.assert_called_once()
    args = mock_celery.send_task.call_args
    assert args[0] == 'tasks.convert_document'

@patch('app.check_disk_space')
def test_convert_disk_full(mock_disk, client):
    mock_disk.return_value = False
    response = client.post('/convert')
    assert response.status_code == 507
    assert b"Server storage is full" in response.data

def test_list_jobs_empty(client):
    with client.session_transaction() as sess:
        sess['jobs'] = []
    response = client.get('/api/jobs')
    assert response.status_code == 200
    assert response.json == []

@patch('app.redis_client')
def test_list_jobs_with_data(mock_redis, client):
    # Setup session
    with client.session_transaction() as sess:
        sess['jobs'] = [{
            'id': 'job1',
            'filename': 'test.md',
            'from': 'markdown',
            'to': 'html',
            'created_at': '2024-01-01T12:00:00+00:00',
            'input_path': '/tmp/in',
            'output_path': '/tmp/out'
        }]
    
    # Setup Redis pipeline mock
    mock_pipeline = MagicMock()
    mock_redis.pipeline.return_value = mock_pipeline
    mock_pipeline.execute.return_value = [{
        'status': 'SUCCESS', 
        'filename': 'test.md'
    }]
    
    response = client.get('/api/jobs')
    assert response.status_code == 200
    assert len(response.json) == 1
    assert response.json[0]['status'] == 'SUCCESS'
    assert response.json[0]['download_url'] == '/download/job1'

@patch('app.celery')
@patch('app.redis_client')
def test_cancel_job(mock_redis, mock_celery, client):
    response = client.post('/api/cancel/job1')
    assert response.status_code == 200
    mock_celery.control.revoke.assert_called_with('job1', terminate=True)
    mock_redis.hset.assert_called()

@patch('shutil.rmtree')
@patch('app.redis_client')
def test_delete_job(mock_redis, mock_rmtree, client):
    with client.session_transaction() as sess:
        sess['jobs'] = [{'id': 'job1'}]
        
    response = client.post('/api/delete/job1')
    assert response.status_code == 200
    assert response.json['status'] == 'deleted'
    
    # Check session is cleared
    with client.session_transaction() as sess:
        assert len(sess['jobs']) == 0
        
    mock_redis.delete.assert_called_with('job:job1')

@patch('os.path.exists')
@patch('shutil.copy2')
@patch('app.redis_client')
@patch('app.celery')
def test_retry_job(mock_celery, mock_redis, mock_copy, mock_exists, client):
    mock_exists.return_value = True # input file exists
    
    with client.session_transaction() as sess:
        sess['jobs'] = [{
            'id': 'job1',
            'filename': 'test.md',
            'from': 'markdown',
            'to': 'html',
            'created_at': '2024-01-01T12:00:00+00:00',
            'input_path': '/app/data/uploads/job1/test.md',
            'output_path': '/app/data/outputs/job1/test.html'
        }]

    with patch('os.makedirs'):
        response = client.post('/api/retry/job1')
    
    assert response.status_code == 200
    assert response.json['status'] == 'retried'
    assert 'new_job_id' in response.json
    
    mock_celery.send_task.assert_called()
    mock_redis.hset.assert_called()
