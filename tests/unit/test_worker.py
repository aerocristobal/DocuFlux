import pytest
from unittest.mock import patch, MagicMock, mock_open
import os
import time
import requests
import uuid
import subprocess

# Fixture to provide tasks module
@pytest.fixture
def tasks():
    import tasks
    return tasks

@patch('tasks.redis_client')
@patch('subprocess.run')
@patch('os.makedirs')
@patch('os.path.exists')
def test_convert_document_success(mock_exists, mock_makedirs, mock_run, mock_redis, tasks):
    mock_exists.return_value = True
    mock_run.return_value = MagicMock(returncode=0, stdout="done", stderr="")
    
    job_id = str(uuid.uuid4())
    result = tasks.convert_document(
        job_id=job_id,
        input_filename='test.md',
        output_filename='test.html',
        from_format='markdown',
        to_format='html'
    )
    
    assert result['status'] == 'success'
    mock_run.assert_called_once()
    # Check pandoc command args
    args = mock_run.call_args[0][0]
    assert 'pandoc' in args
    assert '-f' in args
    assert 'markdown' in args
    
    mock_redis.hset.assert_called() 

@patch('tasks.redis_client')
@patch('os.path.exists')
def test_convert_document_missing_file(mock_exists, mock_redis, tasks):
    mock_exists.return_value = False
    
    job_id = str(uuid.uuid4())
    with pytest.raises(FileNotFoundError):
        tasks.convert_document(
            job_id=job_id,
            input_filename='missing.md',
            output_filename='test.html',
            from_format='markdown',
            to_format='html'
        )
    
    # Check that hset was called with status FAILURE
    mock_redis.hset.assert_called()
    call_args = mock_redis.hset.call_args
    assert call_args[0][0] == f'job:{job_id}'
    assert call_args[1]['mapping']['status'] == 'FAILURE'

@patch('tasks.redis_client')
@patch('subprocess.run')
@patch('shutil.copy2')
@patch('shutil.rmtree')
@patch('os.walk')
@patch('os.makedirs')
@patch('os.path.exists')
def test_convert_with_marker_success(mock_exists, mock_makedirs, mock_walk, mock_rmtree, mock_copy2, mock_run, mock_redis, tasks):
    mock_exists.return_value = True
    
    # Mock subprocess success
    mock_run.return_value = MagicMock(returncode=0)
    
    # Mock os.walk to find md file in subdirectory
    # yield (root, dirs, files)
    mock_walk.return_value = [
        ('/tmp/marker_temp', ['subdir'], []),
        ('/tmp/marker_temp/subdir', [], ['output.md'])
    ]
    
    # Mock open for log file creation (context manager)
    with patch('builtins.open', mock_open()) as m_open:
        mock_self = MagicMock()
        mock_self.request.retries = 0
        
        job_id = str(uuid.uuid4())
        # Signature: self, job_id, input_filename, output_filename, from_format, to_format
        result = tasks.convert_with_marker.run(
            mock_self,
            job_id,
            'test.pdf',
            'test.md',
            'pdf_marker',
            'markdown'
        )
        
        assert result['status'] == 'success'
        mock_run.assert_called_once()
        # Check command contains marker_single
        args = mock_run.call_args[0][0]
        assert 'marker_single' in args
        
        mock_copy2.assert_called()

@patch('tasks.redis_client')
@patch('subprocess.run')
@patch('shutil.rmtree')
@patch('os.makedirs')
@patch('os.path.exists')
def test_convert_with_marker_error(mock_exists, mock_makedirs, mock_rmtree, mock_run, mock_redis, tasks):
    mock_exists.return_value = True
    
    # Mock subprocess failure
    mock_run.side_effect = subprocess.CalledProcessError(1, ['marker_single'])
    
    # Mock open for log file reading
    with patch('builtins.open', mock_open(read_data="Error details log content")) as m_open:
        mock_self = MagicMock()
        mock_self.request.retries = 0
        
        job_id = str(uuid.uuid4())
        with pytest.raises(Exception, match="marker_single failed"):
            tasks.convert_with_marker.run(
                mock_self,
                job_id,
                'test.pdf',
                'test.md',
                'pdf_marker',
                'markdown'
            )

@patch('tasks.redis_client')
@patch('shutil.rmtree')
@patch('os.listdir')
@patch('os.path.exists')
def test_cleanup_old_files(mock_exists, mock_listdir, mock_rmtree, mock_redis, tasks):
    mock_exists.return_value = True
    job_old = str(uuid.uuid4())
    job_fresh = str(uuid.uuid4())
    mock_listdir.return_value = [job_old, job_fresh, 'not-a-uuid']
    
    # Mock Redis returning metadata
    def get_meta(key):
        if job_old in key:
            return {'status': 'SUCCESS', 'completed_at': str(1000000 - 4000)} # > 1h
        return {'status': 'SUCCESS', 'completed_at': str(1000000 - 100)}
    
    mock_redis.hgetall.side_effect = lambda k: get_meta(k) if 'job:' in k else {}
    
    # Mock time
    with patch('time.time') as mock_time:
        mock_time.return_value = 1000000
        
        tasks.cleanup_old_files()
        
        # Should delete job_old but not job_fresh and not 'not-a-uuid' (skipped by is_valid_uuid)
        assert mock_rmtree.call_count >= 1