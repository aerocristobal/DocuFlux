"""
Unit tests for REST API v1 endpoints (Issue #6: External Integration)

Tests the /api/v1/* endpoints for external integration.
"""

import pytest
import io
from unittest.mock import Mock, patch
import time


@pytest.fixture
def client():
    """Create test client for Flask app."""
    import os, tempfile
    import web.app as web_app_mod
    from storage import LocalStorageBackend
    from web.app import app, limiter
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False  # Disable CSRF for testing
    # Epic 5: Provide real storage with temp dirs
    _tmpdir = tempfile.mkdtemp(prefix='docuflux_api_test_')
    _upload = os.path.join(_tmpdir, 'uploads')
    _output = os.path.join(_tmpdir, 'outputs')
    os.makedirs(_upload, exist_ok=True)
    os.makedirs(_output, exist_ok=True)
    web_app_mod.storage = LocalStorageBackend(upload_folder=_upload, output_folder=_output)
    web_app_mod.UPLOAD_FOLDER = _upload
    web_app_mod.OUTPUT_FOLDER = _output
    app.config['UPLOAD_FOLDER'] = _upload
    app.config['OUTPUT_FOLDER'] = _output
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


@pytest.fixture
def api_headers():
    """Provide a valid API key header by patching _validate_api_key."""
    with patch('web.app._validate_api_key', return_value={'created_at': '1700000000.0', 'label': 'test'}):
        yield {'X-API-Key': 'dk_testkey'}


# ============================================================================
# POST /api/v1/convert Tests
# ============================================================================

def test_api_v1_convert_success_pandoc(client, mock_redis, mock_celery, mock_disk_space, api_headers):
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

    response = client.post('/api/v1/convert', data=data, content_type='multipart/form-data', headers=api_headers)

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


def test_api_v1_convert_success_marker(client, mock_redis, mock_celery, mock_disk_space, api_headers):
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

    response = client.post('/api/v1/convert', data=data, content_type='multipart/form-data', headers=api_headers)

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


def test_api_v1_convert_auto_detect_format(client, mock_redis, mock_celery, mock_disk_space, api_headers):
    """Test auto-detection of input format from file extension"""
    mock_redis.hset = Mock()
    mock_redis.hgetall = Mock(return_value={})
    mock_celery.send_task = Mock()

    data = {
        'file': (io.BytesIO(b"# Test"), 'test.md'),
        'to_format': 'pdf'
        # from_format not provided - should auto-detect
    }

    response = client.post('/api/v1/convert', data=data, content_type='multipart/form-data', headers=api_headers)

    assert response.status_code == 202
    json_data = response.get_json()
    assert 'job_id' in json_data


def test_api_v1_convert_missing_file(client, mock_disk_space, api_headers):
    """Test missing file error"""
    response = client.post('/api/v1/convert', data={'to_format': 'markdown'}, headers=api_headers)

    assert response.status_code == 400
    json_data = response.get_json()
    assert 'error' in json_data
    assert 'file' in json_data['error'].lower()


def test_api_v1_convert_missing_to_format(client, mock_disk_space, api_headers):
    """Test missing to_format error"""
    data = {
        'file': (io.BytesIO(b"test"), 'test.md')
    }

    response = client.post('/api/v1/convert', data=data, content_type='multipart/form-data', headers=api_headers)

    assert response.status_code == 400
    json_data = response.get_json()
    assert 'error' in json_data
    assert 'to_format' in json_data['error']


def test_api_v1_convert_invalid_to_format(client, mock_disk_space, api_headers):
    """Test invalid to_format error"""
    data = {
        'file': (io.BytesIO(b"test"), 'test.md'),
        'to_format': 'invalid_format'
    }

    response = client.post('/api/v1/convert', data=data, content_type='multipart/form-data', headers=api_headers)

    assert response.status_code == 422
    json_data = response.get_json()
    assert 'error' in json_data
    assert 'invalid_format' in json_data['error']


def test_api_v1_convert_invalid_engine(client, mock_disk_space, api_headers):
    """Test invalid engine error"""
    data = {
        'file': (io.BytesIO(b"test"), 'test.md'),
        'to_format': 'pdf',
        'engine': 'invalid_engine'
    }

    response = client.post('/api/v1/convert', data=data, content_type='multipart/form-data', headers=api_headers)

    assert response.status_code == 422
    json_data = response.get_json()
    assert 'error' in json_data
    assert 'engine' in json_data['error'].lower()


def test_api_v1_convert_disk_full(client, api_headers):
    """Test disk full error"""
    with patch('web.app.check_disk_space', return_value=False):
        data = {
            'file': (io.BytesIO(b"test"), 'test.md'),
            'to_format': 'pdf'
        }

        response = client.post('/api/v1/convert', data=data, content_type='multipart/form-data', headers=api_headers)

        assert response.status_code == 507
        json_data = response.get_json()
        assert 'error' in json_data
        assert 'storage' in json_data['error'].lower()


def test_api_v1_convert_cannot_detect_format(client, mock_disk_space, api_headers):
    """Test error when format cannot be auto-detected"""
    data = {
        'file': (io.BytesIO(b"test"), 'test.xyz'),  # Unknown extension
        'to_format': 'pdf'
    }

    response = client.post('/api/v1/convert', data=data, content_type='multipart/form-data', headers=api_headers)

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

    # Create actual output file in storage
    import web.app as _app
    _app.storage.save_file(job_id, 'test.md', b'# Converted', folder='output')

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

def test_api_v1_download_requires_api_key(client):
    """Test download returns 401 without API key"""
    job_id = '550e8400-e29b-41d4-a716-446655440000'
    response = client.get(f'/api/v1/download/{job_id}')
    assert response.status_code == 401


def test_api_v1_download_success_single_file(client, mock_redis, api_headers):
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

    # Create actual output file in storage
    import web.app as _app
    _app.storage.save_file(job_id, 'test.md', b'# Converted', folder='output')

    response = client.get(f'/api/v1/download/{job_id}', headers=api_headers)

    # Should serve the file (200 for local storage)
    assert response.status_code == 200


def test_api_v1_download_not_found(client, mock_redis, api_headers):
    """Test download for non-existent job"""
    job_id = '550e8400-e29b-41d4-a716-446655440000'

    # Mock Redis returning None
    mock_redis.hgetall = Mock(return_value=None)

    response = client.get(f'/api/v1/download/{job_id}', headers=api_headers)

    assert response.status_code == 404
    json_data = response.get_json()
    assert 'error' in json_data


def test_api_v1_download_not_completed(client, mock_redis, api_headers):
    """Test download for job that's not completed yet"""
    job_id = '550e8400-e29b-41d4-a716-446655440000'

    # Mock Redis metadata with PENDING status
    mock_redis.hgetall = Mock(return_value={
        'status': 'PENDING',
        'filename': 'test.pdf'
    })

    response = client.get(f'/api/v1/download/{job_id}', headers=api_headers)

    assert response.status_code == 404
    json_data = response.get_json()
    assert 'not completed' in json_data['error'].lower()


def test_api_v1_download_files_expired(client, mock_redis, api_headers):
    """Test download when files have been deleted"""
    job_id = '550e8400-e29b-41d4-a716-446655440000'

    # Mock Redis metadata
    mock_redis.hgetall = Mock(return_value={
        'status': 'SUCCESS',
        'filename': 'test.pdf'
    })

    # Mock directory doesn't exist
    with patch('os.path.exists', return_value=False):
        response = client.get(f'/api/v1/download/{job_id}', headers=api_headers)

        assert response.status_code == 410
        json_data = response.get_json()
        assert 'expired' in json_data['error'].lower()


def test_api_v1_download_invalid_uuid(client, api_headers):
    """Test download with invalid UUID"""
    response = client.get('/api/v1/download/invalid-id', headers=api_headers)

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

def test_api_v1_endpoints_csrf_exempt(client, mock_redis, mock_celery, mock_disk_space, api_headers):
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

    response = client.post('/api/v1/convert', data=data, content_type='multipart/form-data', headers=api_headers)

    # Should succeed (202), not fail with 400 CSRF error
    assert response.status_code == 202

    # Restore CSRF disabled state
    app.config['WTF_CSRF_ENABLED'] = False


# ============================================================================
# GET /api/v1/status — SLM metadata fields
# ============================================================================

class TestApiV1StatusSlm:
    def test_status_includes_slm_metadata_when_success(self, client, api_headers):
        """Status response includes slm_metadata when SLM extraction succeeded."""
        job_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        redis_data = {
            'status': 'SUCCESS',
            'filename': 'doc.pdf',
            'from': 'pdf_marker',
            'to': 'markdown',
            'created_at': '1700000000.0',
            'completed_at': '1700000060.0',
            'progress': '100',
            'slm_status': 'SUCCESS',
            'slm_title': 'AI Generated Title',
            'slm_tags': '["ai", "document"]',
            'slm_summary': 'A brief summary.',
        }
        with patch('web.app.get_job_metadata', return_value=redis_data), \
             patch('web.app.os.path.exists', return_value=False):
            r = client.get(f'/api/v1/status/{job_id}')
        assert r.status_code == 200
        data = r.get_json()
        assert 'slm_metadata' in data
        assert data['slm_metadata']['status'] == 'SUCCESS'
        assert data['slm_metadata']['title'] == 'AI Generated Title'
        assert data['slm_metadata']['tags'] == ['ai', 'document']
        assert data['slm_metadata']['summary'] == 'A brief summary.'

    def test_status_no_slm_field_when_not_extracted(self, client, api_headers):
        """Status response omits slm_metadata when SLM hasn't run."""
        job_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        redis_data = {
            'status': 'PENDING',
            'filename': 'doc.pdf',
            'from': 'pdf_marker',
            'to': 'markdown',
            'created_at': '1700000000.0',
            'progress': '0',
        }
        with patch('web.app.get_job_metadata', return_value=redis_data):
            r = client.get(f'/api/v1/status/{job_id}')
        assert r.status_code == 200
        assert 'slm_metadata' not in r.get_json()


# ============================================================================
# POST /api/v1/jobs/<job_id>/extract-metadata
# ============================================================================

class TestApiV1ExtractMetadata:
    def test_extract_metadata_queues_task(self, client, api_headers):
        """POST /extract-metadata dispatches SLM task and returns 202."""
        import uuid
        job_id = str(uuid.uuid4())
        redis_data = {'status': 'SUCCESS', 'filename': 'doc.pdf', 'from': 'pdf_marker',
                      'to': 'markdown', 'created_at': '1700000000.0'}

        # Create actual markdown file in storage
        import web.app as _app
        _app.storage.save_file(job_id, 'doc.md', b'# Converted doc', folder='output')

        with patch('web.app.get_job_metadata', return_value=redis_data), \
             patch('web.app.update_job_metadata') as mock_update, \
             patch('web.app.celery') as mock_celery:
            r = client.post(
                f'/api/v1/jobs/{job_id}/extract-metadata',
                headers=api_headers,
            )
        assert r.status_code == 202
        data = r.get_json()
        assert data['status'] == 'queued'
        mock_celery.send_task.assert_called_once()
        args = mock_celery.send_task.call_args
        assert args[0][0] == 'tasks.extract_slm_metadata'

    def test_extract_metadata_requires_api_key(self, client):
        """POST /extract-metadata returns 401 without API key."""
        import uuid
        job_id = str(uuid.uuid4())
        r = client.post(f'/api/v1/jobs/{job_id}/extract-metadata')
        assert r.status_code == 401

    def test_extract_metadata_409_if_not_success(self, client, api_headers):
        """POST /extract-metadata returns 409 if job is still processing."""
        import uuid
        job_id = str(uuid.uuid4())
        redis_data = {'status': 'PROCESSING', 'filename': 'doc.pdf',
                      'from': 'pdf_marker', 'to': 'markdown', 'created_at': '1700000000.0'}
        with patch('web.app.get_job_metadata', return_value=redis_data):
            r = client.post(
                f'/api/v1/jobs/{job_id}/extract-metadata',
                headers=api_headers,
            )
        assert r.status_code == 409

    def test_extract_metadata_404_for_unknown_job(self, client, api_headers):
        """POST /extract-metadata returns 404 if job not found."""
        import uuid
        job_id = str(uuid.uuid4())
        with patch('web.app.get_job_metadata', return_value=None):
            r = client.post(
                f'/api/v1/jobs/{job_id}/extract-metadata',
                headers=api_headers,
            )
        assert r.status_code == 404


# ============================================================================
# POST /api/v1/convert — pandoc_options Tests
# ============================================================================

class TestApiV1PandocOptions:
    """Tests for the pandoc_options parameter on /api/v1/convert."""

    def test_valid_pandoc_options_passed_to_celery(self, client, mock_redis, mock_celery, mock_disk_space, api_headers):
        """Valid pandoc_options are forwarded to Celery kwargs."""
        mock_redis.hset = Mock()
        mock_redis.hgetall = Mock(return_value={})
        mock_celery.send_task = Mock()

        import json
        opts = json.dumps({'toc': True, 'variables': {'fontsize': '11pt'}})
        data = {
            'file': (io.BytesIO(b"# Test"), 'test.md'),
            'to_format': 'pdf',
            'pandoc_options': opts,
        }

        r = client.post('/api/v1/convert', data=data, content_type='multipart/form-data', headers=api_headers)
        assert r.status_code == 202

        call_args = mock_celery.send_task.call_args
        assert call_args[1]['kwargs']['pandoc_options'] == {'toc': True, 'variables': {'fontsize': '11pt'}}

    def test_invalid_json_returns_400(self, client, mock_disk_space, api_headers):
        """Non-JSON pandoc_options returns 400."""
        data = {
            'file': (io.BytesIO(b"# Test"), 'test.md'),
            'to_format': 'pdf',
            'pandoc_options': 'not json{',
        }

        r = client.post('/api/v1/convert', data=data, content_type='multipart/form-data', headers=api_headers)
        assert r.status_code == 400
        assert 'JSON' in r.get_json()['error']

    def test_unknown_option_key_returns_422(self, client, mock_disk_space, api_headers):
        """Unknown option key returns 422 with details."""
        import json
        data = {
            'file': (io.BytesIO(b"# Test"), 'test.md'),
            'to_format': 'pdf',
            'pandoc_options': json.dumps({'lua_filter': '/etc/passwd'}),
        }

        r = client.post('/api/v1/convert', data=data, content_type='multipart/form-data', headers=api_headers)
        assert r.status_code == 422
        body = r.get_json()
        assert 'details' in body
        assert any('Unknown' in d for d in body['details'])

    def test_out_of_range_int_returns_422(self, client, mock_disk_space, api_headers):
        """Out-of-range integer returns 422."""
        import json
        data = {
            'file': (io.BytesIO(b"# Test"), 'test.md'),
            'to_format': 'pdf',
            'pandoc_options': json.dumps({'dpi': 9999}),
        }

        r = client.post('/api/v1/convert', data=data, content_type='multipart/form-data', headers=api_headers)
        assert r.status_code == 422
        assert any('between' in d for d in r.get_json()['details'])

    def test_pandoc_options_with_marker_engine_returns_422(self, client, mock_redis, mock_disk_space, api_headers):
        """pandoc_options with engine=marker returns 422."""
        import json
        mock_redis.hset = Mock()
        mock_redis.hgetall = Mock(return_value={})

        data = {
            'file': (io.BytesIO(b"PDF content"), 'test.pdf'),
            'to_format': 'markdown',
            'engine': 'marker',
            'pandoc_options': json.dumps({'toc': True}),
        }

        r = client.post('/api/v1/convert', data=data, content_type='multipart/form-data', headers=api_headers)
        assert r.status_code == 422
        assert 'pandoc' in r.get_json()['error'].lower()

    def test_shell_metacharacters_in_variable_returns_422(self, client, mock_disk_space, api_headers):
        """Shell metacharacters in variable values are rejected."""
        import json
        data = {
            'file': (io.BytesIO(b"# Test"), 'test.md'),
            'to_format': 'pdf',
            'pandoc_options': json.dumps({'variables': {'mainfont': 'foo;rm -rf /'}}),
        }

        r = client.post('/api/v1/convert', data=data, content_type='multipart/form-data', headers=api_headers)
        assert r.status_code == 422
        assert any('disallowed' in d for d in r.get_json()['details'])

    def test_no_pandoc_options_still_works(self, client, mock_redis, mock_celery, mock_disk_space, api_headers):
        """Conversion without pandoc_options still dispatches normally."""
        mock_redis.hset = Mock()
        mock_redis.hgetall = Mock(return_value={})
        mock_celery.send_task = Mock()

        data = {
            'file': (io.BytesIO(b"# Test"), 'test.md'),
            'to_format': 'pdf',
        }

        r = client.post('/api/v1/convert', data=data, content_type='multipart/form-data', headers=api_headers)
        assert r.status_code == 202
        # kwargs should be empty (no pandoc_options key)
        call_kwargs = mock_celery.send_task.call_args[1].get('kwargs', {})
        assert 'pandoc_options' not in call_kwargs


# ============================================================================
# Webhook Authentication Tests (Story 3.2)
# ============================================================================

class TestWebhookAuth:
    """Tests for webhook endpoint authentication."""

    def test_register_webhook_requires_api_key(self, client, mock_redis):
        """POST /api/v1/webhooks returns 401 without API key."""
        response = client.post('/api/v1/webhooks',
                               json={'job_id': '550e8400-e29b-41d4-a716-446655440000',
                                     'webhook_url': 'https://example.com/hook'})
        assert response.status_code == 401

    def test_get_webhook_requires_api_key(self, client, mock_redis):
        """GET /api/v1/webhooks/<id> returns 401 without API key."""
        response = client.get('/api/v1/webhooks/550e8400-e29b-41d4-a716-446655440000')
        assert response.status_code == 401

    def test_register_webhook_with_api_key(self, client, mock_redis, api_headers):
        """POST /api/v1/webhooks succeeds with valid API key."""
        job_id = '550e8400-e29b-41d4-a716-446655440000'
        mock_redis.hgetall = Mock(return_value={'status': 'PENDING'})
        mock_redis.hset = Mock()

        with patch('web.validation.socket.getaddrinfo',
                   return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, '', ('93.184.216.34', 0))]):
            response = client.post('/api/v1/webhooks',
                                   json={'job_id': job_id,
                                         'webhook_url': 'https://example.com/hook'},
                                   headers=api_headers)
        assert response.status_code == 201
        data = response.get_json()
        assert data['registered'] is True

    def test_get_webhook_with_api_key(self, client, mock_redis, api_headers):
        """GET /api/v1/webhooks/<id> succeeds with valid API key."""
        job_id = '550e8400-e29b-41d4-a716-446655440000'
        mock_redis.hgetall = Mock(return_value={
            'status': 'PENDING',
            'webhook_url': 'https://example.com/hook'
        })

        response = client.get(f'/api/v1/webhooks/{job_id}', headers=api_headers)
        assert response.status_code == 200
        data = response.get_json()
        assert data['webhook_url'] == 'https://example.com/hook'


# ============================================================================
# Webhook SSRF Validation Tests (Story 3.3)
# ============================================================================

import socket

class TestWebhookSSRF:
    """Tests for webhook URL SSRF validation."""

    def _register(self, client, api_headers, webhook_url, mock_redis, dns_ip='93.184.216.34'):
        """Helper to register a webhook with controlled DNS resolution."""
        job_id = '550e8400-e29b-41d4-a716-446655440000'
        mock_redis.hgetall = Mock(return_value={'status': 'PENDING'})
        mock_redis.hset = Mock()

        with patch('web.validation.socket.getaddrinfo',
                   return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, '', (dns_ip, 0))]):
            return client.post('/api/v1/webhooks',
                               json={'job_id': job_id, 'webhook_url': webhook_url},
                               headers=api_headers)

    def test_rejects_localhost(self, client, mock_redis, api_headers):
        """Webhook URL resolving to 127.0.0.1 is rejected."""
        r = self._register(client, api_headers, 'https://localhost/hook',
                           mock_redis, dns_ip='127.0.0.1')
        assert r.status_code == 400
        assert 'private' in r.get_json()['error'].lower()

    def test_rejects_private_10x(self, client, mock_redis, api_headers):
        """Webhook URL resolving to 10.x.x.x is rejected."""
        r = self._register(client, api_headers, 'https://internal.corp/hook',
                           mock_redis, dns_ip='10.0.0.1')
        assert r.status_code == 400
        assert 'private' in r.get_json()['error'].lower()

    def test_rejects_private_192_168(self, client, mock_redis, api_headers):
        """Webhook URL resolving to 192.168.x.x is rejected."""
        r = self._register(client, api_headers, 'https://router.local/hook',
                           mock_redis, dns_ip='192.168.1.1')
        assert r.status_code == 400
        assert 'private' in r.get_json()['error'].lower()

    def test_rejects_metadata_endpoint(self, client, mock_redis, api_headers):
        """Webhook URL resolving to cloud metadata IP is rejected."""
        r = self._register(client, api_headers, 'https://metadata.google/hook',
                           mock_redis, dns_ip='169.254.169.254')
        assert r.status_code == 400
        assert 'private' in r.get_json()['error'].lower() or 'reserved' in r.get_json()['error'].lower()

    def test_rejects_http_when_https_required(self, client, mock_redis, api_headers):
        """HTTP webhook URL rejected when WEBHOOK_REQUIRE_HTTPS is set."""
        from config import settings
        original = settings.webhook_require_https
        settings.webhook_require_https = True
        try:
            r = self._register(client, api_headers, 'http://example.com/hook', mock_redis)
            assert r.status_code == 400
            assert 'HTTPS' in r.get_json()['error']
        finally:
            settings.webhook_require_https = original

    def test_allowlist_blocks_unlisted_host(self, client, mock_redis, api_headers):
        """Webhook URL not on allowlist is rejected."""
        from config import settings
        original = settings.webhook_url_allowlist
        settings.webhook_url_allowlist = 'trusted.example.com,other.example.com'
        try:
            r = self._register(client, api_headers, 'https://evil.com/hook', mock_redis)
            assert r.status_code == 400
            assert 'allowlist' in r.get_json()['error']
        finally:
            settings.webhook_url_allowlist = original

    def test_blocklist_blocks_listed_host(self, client, mock_redis, api_headers):
        """Webhook URL on blocklist is rejected."""
        from config import settings
        original = settings.webhook_url_blocklist
        settings.webhook_url_blocklist = 'evil.com,bad-actor.io'
        try:
            r = self._register(client, api_headers, 'https://evil.com/hook', mock_redis)
            assert r.status_code == 400
            assert 'blocked' in r.get_json()['error']
        finally:
            settings.webhook_url_blocklist = original

    def test_accepts_valid_public_url(self, client, mock_redis, api_headers):
        """Valid public HTTPS URL is accepted."""
        r = self._register(client, api_headers, 'https://hooks.example.com/callback', mock_redis)
        assert r.status_code == 201
