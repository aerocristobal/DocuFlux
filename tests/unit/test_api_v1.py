"""
Unit tests for REST API v1 endpoints (Issue #6: External Integration)

Tests the /api/v1/* endpoints for external integration.
"""

import pytest
import json
import io
from unittest.mock import Mock, patch, MagicMock
import time


@pytest.fixture
def client():
    """Create test client for Flask app."""
    from web.app import app, limiter
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False  # Disable CSRF for testing
    # Disable rate limiting to prevent 429 errors from accumulated test requests
    original_enabled = limiter.enabled
    limiter.enabled = False
    with app.test_client() as client:
        yield client
    limiter.enabled = original_enabled


@pytest.fixture
def mock_redis():
    """Mock Redis client."""
    with patch('web.app.redis_client') as mock:
        yield mock


@pytest.fixture
def mock_celery():
    """Mock Celery client."""
    with patch('web.app.celery') as mock:
        yield mock


@pytest.fixture
def mock_disk_space():
    """Mock disk space check."""
    with patch('web.app.check_disk_space', return_value=True) as mock:
        yield mock


# ============================================================================
# POST /api/v1/convert Tests
# ============================================================================

def test_api_v1_convert_success_pandoc(client, mock_redis, mock_celery, mock_disk_space):
    """Test successful job submission with Pandoc engine"""
    # Mock Redis operations
    mock_redis.hset = Mock()
    mock_redis.hgetall = Mock(return_value={})

    # Mock Celery task dispatch
    mock_celery.send_task = Mock()

    # Create test file
    data = {
        'file': (io.BytesIO(b"# Test Markdown"), 'test.md'),
        'to_format': 'docx',
        'engine': 'pandoc'
    }

    response = client.post('/api/v1/convert', data=data, content_type='multipart/form-data')

    assert response.status_code == 202
    json_data = response.get_json()
    assert 'job_id' in json_data
    assert 'status_url' in json_data
    assert json_data['status'] == 'queued'
    assert 'created_at' in json_data
    assert json_data['status_url'].startswith('/api/v1/status/')

    # Verify Celery task was dispatched
    mock_celery.send_task.assert_called_once()
    call_args = mock_celery.send_task.call_args
    assert call_args[1]['args'][3] == 'markdown'  # from_format
    assert call_args[1]['args'][4] == 'docx'  # to_format


def test_api_v1_convert_success_marker(client, mock_redis, mock_celery, mock_disk_space):
    """Test successful job submission with Marker engine"""
    # Mock Redis operations
    mock_redis.hset = Mock()
    mock_redis.hgetall = Mock(return_value={})

    # Mock Celery task dispatch
    mock_celery.send_task = Mock()

    # Create test PDF file
    data = {
        'file': (io.BytesIO(b"PDF content"), 'test.pdf'),
        'to_format': 'markdown',
        'engine': 'marker',
        'force_ocr': 'true',
        'use_llm': 'false'
    }

    response = client.post('/api/v1/convert', data=data, content_type='multipart/form-data')

    assert response.status_code == 202
    json_data = response.get_json()
    assert json_data['status'] == 'queued'

    # Verify Marker task was dispatched
    mock_celery.send_task.assert_called_once()
    call_args = mock_celery.send_task.call_args
    assert call_args[0][0] == 'tasks.convert_with_marker'
    assert call_args[1]['args'][3] == 'pdf_marker'  # from_format

    # Verify options were passed
    options = call_args[1]['args'][5]
    assert options['force_ocr'] == True
    assert options['use_llm'] == False


def test_api_v1_convert_auto_detect_format(client, mock_redis, mock_celery, mock_disk_space):
    """Test auto-detection of input format from file extension"""
    mock_redis.hset = Mock()
    mock_redis.hgetall = Mock(return_value={})
    mock_celery.send_task = Mock()

    data = {
        'file': (io.BytesIO(b"# Test"), 'test.md'),
        'to_format': 'pdf'
        # from_format not provided - should auto-detect
    }

    response = client.post('/api/v1/convert', data=data, content_type='multipart/form-data')

    assert response.status_code == 202
    json_data = response.get_json()
    assert 'job_id' in json_data


def test_api_v1_convert_missing_file(client, mock_disk_space):
    """Test missing file error"""
    response = client.post('/api/v1/convert', data={'to_format': 'markdown'})

    assert response.status_code == 400
    json_data = response.get_json()
    assert 'error' in json_data
    assert 'file' in json_data['error'].lower()


def test_api_v1_convert_missing_to_format(client, mock_disk_space):
    """Test missing to_format error"""
    data = {
        'file': (io.BytesIO(b"test"), 'test.md')
    }

    response = client.post('/api/v1/convert', data=data, content_type='multipart/form-data')

    assert response.status_code == 400
    json_data = response.get_json()
    assert 'error' in json_data
    assert 'to_format' in json_data['error']


def test_api_v1_convert_invalid_to_format(client, mock_disk_space):
    """Test invalid to_format error"""
    data = {
        'file': (io.BytesIO(b"test"), 'test.md'),
        'to_format': 'invalid_format'
    }

    response = client.post('/api/v1/convert', data=data, content_type='multipart/form-data')

    assert response.status_code == 422
    json_data = response.get_json()
    assert 'error' in json_data
    assert 'invalid_format' in json_data['error']


def test_api_v1_convert_invalid_engine(client, mock_disk_space):
    """Test invalid engine error"""
    data = {
        'file': (io.BytesIO(b"test"), 'test.md'),
        'to_format': 'pdf',
        'engine': 'invalid_engine'
    }

    response = client.post('/api/v1/convert', data=data, content_type='multipart/form-data')

    assert response.status_code == 422
    json_data = response.get_json()
    assert 'error' in json_data
    assert 'engine' in json_data['error'].lower()


def test_api_v1_convert_disk_full(client):
    """Test disk full error"""
    with patch('web.app.check_disk_space', return_value=False):
        data = {
            'file': (io.BytesIO(b"test"), 'test.md'),
            'to_format': 'pdf'
        }

        response = client.post('/api/v1/convert', data=data, content_type='multipart/form-data')

        assert response.status_code == 507
        json_data = response.get_json()
        assert 'error' in json_data
        assert 'storage' in json_data['error'].lower()


def test_api_v1_convert_cannot_detect_format(client, mock_disk_space):
    """Test error when format cannot be auto-detected"""
    data = {
        'file': (io.BytesIO(b"test"), 'test.xyz'),  # Unknown extension
        'to_format': 'pdf'
    }

    response = client.post('/api/v1/convert', data=data, content_type='multipart/form-data')

    assert response.status_code == 422
    json_data = response.get_json()
    assert 'error' in json_data
    assert 'auto-detect' in json_data['error']


# ============================================================================
# GET /api/v1/status/{job_id} Tests
# ============================================================================

def test_api_v1_status_pending(client, mock_redis):
    """Test status for pending job"""
    job_id = '550e8400-e29b-41d4-a716-446655440000'

    # Mock Redis metadata
    mock_redis.hgetall = Mock(return_value={
        'status': 'PENDING',
        'filename': 'test.pdf',
        'from': 'pdf',
        'to': 'markdown',
        'engine': 'pandoc',
        'created_at': str(time.time()),
        'progress': '0'
    })

    response = client.get(f'/api/v1/status/{job_id}')

    assert response.status_code == 200
    json_data = response.get_json()
    assert json_data['job_id'] == job_id
    assert json_data['status'] == 'pending'
    assert json_data['progress'] == 0
    assert json_data['filename'] == 'test.pdf'
    assert json_data['from_format'] == 'pdf'
    assert json_data['to_format'] == 'markdown'


def test_api_v1_status_processing(client, mock_redis):
    """Test status for processing job"""
    job_id = '550e8400-e29b-41d4-a716-446655440000'
    created_time = time.time() - 60
    started_time = time.time() - 30

    # Mock Redis metadata
    mock_redis.hgetall = Mock(return_value={
        'status': 'PROCESSING',
        'filename': 'test.pdf',
        'from': 'pdf_marker',
        'to': 'markdown',
        'engine': 'marker',
        'created_at': str(created_time),
        'started_at': str(started_time),
        'progress': '45'
    })

    response = client.get(f'/api/v1/status/{job_id}')

    assert response.status_code == 200
    json_data = response.get_json()
    assert json_data['status'] == 'processing'
    assert json_data['progress'] == 45
    assert 'started_at' in json_data


def test_api_v1_status_success(client, mock_redis):
    """Test status for successful job"""
    job_id = '550e8400-e29b-41d4-a716-446655440000'
    created_time = time.time() - 120
    completed_time = time.time() - 10

    # Mock Redis metadata
    mock_redis.hgetall = Mock(return_value={
        'status': 'SUCCESS',
        'filename': 'test.pdf',
        'from': 'pdf',
        'to': 'markdown',
        'engine': 'pandoc',
        'created_at': str(created_time),
        'completed_at': str(completed_time),
        'progress': '100'
    })

    # Mock output directory - exists=True for output dir, False for images subdir
    def exists_side_effect(path):
        return 'images' not in path

    with patch('os.path.exists', side_effect=exists_side_effect):
        with patch('os.listdir', return_value=['test.md']):
            response = client.get(f'/api/v1/status/{job_id}')

    assert response.status_code == 200
    json_data = response.get_json()
    assert json_data['status'] == 'success'
    assert json_data['progress'] == 100
    assert 'download_url' in json_data
    assert json_data['download_url'] == f'/api/v1/download/{job_id}'
    assert json_data['is_multifile'] == False
    assert json_data['file_count'] == 1


def test_api_v1_status_failed(client, mock_redis):
    """Test status for failed job"""
    job_id = '550e8400-e29b-41d4-a716-446655440000'
    created_time = time.time() - 60
    completed_time = time.time() - 5

    # Mock Redis metadata
    mock_redis.hgetall = Mock(return_value={
        'status': 'FAILURE',
        'filename': 'test.pdf',
        'from': 'pdf',
        'to': 'markdown',
        'engine': 'pandoc',
        'created_at': str(created_time),
        'completed_at': str(completed_time),
        'progress': '0',
        'error': 'Conversion failed: Invalid PDF'
    })

    response = client.get(f'/api/v1/status/{job_id}')

    assert response.status_code == 200
    json_data = response.get_json()
    assert json_data['status'] == 'failure'
    assert 'error' in json_data
    assert 'Invalid PDF' in json_data['error']


def test_api_v1_status_not_found(client, mock_redis):
    """Test status for non-existent job"""
    job_id = '550e8400-e29b-41d4-a716-446655440000'

    # Mock Redis returning None
    mock_redis.hgetall = Mock(return_value=None)

    response = client.get(f'/api/v1/status/{job_id}')

    assert response.status_code == 404
    json_data = response.get_json()
    assert 'error' in json_data
    assert 'not found' in json_data['error'].lower()


def test_api_v1_status_invalid_uuid(client):
    """Test status with invalid UUID format"""
    response = client.get('/api/v1/status/not-a-uuid')

    assert response.status_code == 400
    json_data = response.get_json()
    assert 'error' in json_data
    assert 'invalid' in json_data['error'].lower()


# ============================================================================
# GET /api/v1/download/{job_id} Tests
# ============================================================================

def test_api_v1_download_success_single_file(client, mock_redis):
    """Test downloading a single converted file"""
    job_id = '550e8400-e29b-41d4-a716-446655440000'

    # Mock Redis metadata
    mock_pipe = Mock()
    mock_redis.pipeline.return_value = mock_pipe
    mock_pipe.execute.return_value = [1, {}]
    mock_redis.hgetall = Mock(return_value={
        'status': 'SUCCESS',
        'filename': 'test.pdf',
        'encrypted': 'false'
    })

    # Mock file system - exists True for dirs but False for images subdir
    def exists_side_effect(path):
        return 'images' not in path

    with patch('os.path.exists', side_effect=exists_side_effect):
        with patch('os.path.isfile', return_value=True):
            with patch('os.listdir', return_value=['test.md']):
                with patch('web.app.send_from_directory') as mock_send:
                    mock_send.return_value = 'file_content'

                    response = client.get(f'/api/v1/download/{job_id}')

                    # Verify send_from_directory was called
                    mock_send.assert_called_once()


def test_api_v1_download_not_found(client, mock_redis):
    """Test download for non-existent job"""
    job_id = '550e8400-e29b-41d4-a716-446655440000'

    # Mock Redis returning None
    mock_redis.hgetall = Mock(return_value=None)

    response = client.get(f'/api/v1/download/{job_id}')

    assert response.status_code == 404
    json_data = response.get_json()
    assert 'error' in json_data


def test_api_v1_download_not_completed(client, mock_redis):
    """Test download for job that's not completed yet"""
    job_id = '550e8400-e29b-41d4-a716-446655440000'

    # Mock Redis metadata with PENDING status
    mock_redis.hgetall = Mock(return_value={
        'status': 'PENDING',
        'filename': 'test.pdf'
    })

    response = client.get(f'/api/v1/download/{job_id}')

    assert response.status_code == 404
    json_data = response.get_json()
    assert 'not completed' in json_data['error'].lower()


def test_api_v1_download_files_expired(client, mock_redis):
    """Test download when files have been deleted"""
    job_id = '550e8400-e29b-41d4-a716-446655440000'

    # Mock Redis metadata
    mock_redis.hgetall = Mock(return_value={
        'status': 'SUCCESS',
        'filename': 'test.pdf'
    })

    # Mock directory doesn't exist
    with patch('os.path.exists', return_value=False):
        response = client.get(f'/api/v1/download/{job_id}')

        assert response.status_code == 410
        json_data = response.get_json()
        assert 'expired' in json_data['error'].lower()


def test_api_v1_download_invalid_uuid(client):
    """Test download with invalid UUID"""
    response = client.get('/api/v1/download/invalid-id')

    assert response.status_code == 400
    json_data = response.get_json()
    assert 'invalid' in json_data['error'].lower()


# ============================================================================
# GET /api/v1/formats Tests
# ============================================================================

def test_api_v1_formats(client):
    """Test formats list endpoint"""
    response = client.get('/api/v1/formats')

    assert response.status_code == 200
    json_data = response.get_json()

    # Verify structure
    assert 'input_formats' in json_data
    assert 'output_formats' in json_data
    assert 'conversions' in json_data

    # Verify input formats
    assert len(json_data['input_formats']) > 0
    input_format = json_data['input_formats'][0]
    assert 'name' in input_format
    assert 'key' in input_format
    assert 'extension' in input_format
    assert 'mime_types' in input_format
    assert 'supports_marker' in input_format
    assert 'supports_pandoc' in input_format

    # Verify output formats
    assert len(json_data['output_formats']) > 0
    output_format = json_data['output_formats'][0]
    assert 'name' in output_format
    assert 'key' in output_format
    assert 'extension' in output_format

    # Verify conversions
    assert len(json_data['conversions']) > 0
    conversion = json_data['conversions'][0]
    assert 'from' in conversion
    assert 'to' in conversion
    assert 'engines' in conversion
    assert 'recommended_engine' in conversion

    # Verify pdf_marker format supports Marker (pdf is output-only)
    pdf_marker_format = next((f for f in json_data['input_formats'] if f['key'] == 'pdf_marker'), None)
    assert pdf_marker_format is not None
    assert pdf_marker_format['supports_marker'] == True


# ============================================================================
# CSRF Exemption Tests
# ============================================================================

def test_api_v1_endpoints_csrf_exempt(client, mock_redis, mock_celery, mock_disk_space):
    """Test that API v1 endpoints work without CSRF token"""
    # Enable CSRF for this test
    from web.app import app
    app.config['WTF_CSRF_ENABLED'] = True

    mock_redis.hset = Mock()
    mock_redis.hgetall = Mock(return_value={})
    mock_celery.send_task = Mock()

    # API endpoint should work without CSRF token
    data = {
        'file': (io.BytesIO(b"test"), 'test.md'),
        'to_format': 'pdf'
    }

    response = client.post('/api/v1/convert', data=data, content_type='multipart/form-data')

    # Should succeed (202), not fail with 400 CSRF error
    assert response.status_code == 202

    # Restore CSRF disabled state
    app.config['WTF_CSRF_ENABLED'] = False
