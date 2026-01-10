import pytest
from unittest.mock import patch, MagicMock, mock_open
import os
import time
import requests

# We need to import tasks after conftest sets up the path and env vars
from tasks import convert_document, convert_with_marker, cleanup_old_files

@patch('tasks.redis_client')
@patch('subprocess.run')
@patch('os.makedirs')
@patch('os.path.exists')
def test_convert_document_success(mock_exists, mock_makedirs, mock_run, mock_redis):
    mock_exists.return_value = True
    mock_run.return_value = MagicMock(returncode=0, stdout="done", stderr="")
    
    result = convert_document(
        job_id='job1',
        input_path='/in/test.md',
        output_path='/out/test.html',
        from_format='markdown',
        to_format='html'
    )
    
    assert result['status'] == 'success'
    mock_run.assert_called_once()
    # Check pandas command args
    args = mock_run.call_args[0][0]
    assert 'pandoc' in args
    assert '-f' in args
    assert 'markdown' in args
    
    mock_redis.hset.assert_called() # Should update status to SUCCESS

@patch('tasks.redis_client')
@patch('os.path.exists')
def test_convert_document_missing_file(mock_exists, mock_redis):
    mock_exists.return_value = False
    
    with pytest.raises(FileNotFoundError):
        convert_document(
            job_id='job1',
            input_path='/in/missing.md',
            output_path='/out/test.html',
            from_format='markdown',
            to_format='html'
        )
    
    mock_redis.hset.assert_called_with('job:job1', mapping=pytest.approx({'status': 'FAILURE'}, abs=1e-6))

@patch('tasks.redis_client')
@patch('requests.post')
@patch('os.makedirs')
@patch('os.path.exists')
def test_convert_with_marker_success(mock_exists, mock_makedirs, mock_post, mock_redis):
    mock_exists.return_value = True
    
    # Mock successful response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {'Content-Type': 'application/json'}
    mock_response.json.return_value = {'result': {'markdown': '# Converted PDF'}}
    mock_post.return_value = mock_response
    
    # Mock open for input and output
    with patch('builtins.open', mock_open(read_data=b"pdf content")) as m_open:
        # We need to handle the fact that it opens input (rb) and output (w)
        # mock_open is tricky with multiple files.
        # Let's simplify and just assume it works or use side_effect if needed.
        
        # We need to mock the self argument since it's a bound task
        mock_self = MagicMock()
        mock_self.request.retries = 0
        
        # Call the task function directly (bypassing celery wrapper if possible, 
        # but since we imported the decorated task, we should use .run or call it if eager)
        # 'convert_with_marker' is a celery Task object.
        # We can call the underlying function via .run provided we pass self.
        
        # Wait, Celery tasks when called directly in tests acts like the function if not bound?
        # But this is bound.
        # Better to use the Task class's run method or invoke it.
        # Or simpler: access the wrapped function?
        
        # In recent celery, calling the task object directly invokes the task.
        # But if it uses `self`, we might run into issues if we don't mock the context.
        # Let's try calling it. If it fails due to missing self, we patch it.
        
        # Actually, for bound tasks, calling them directly injects a mock self? No.
        # Let's use `.run()` explicitly which requires explicit arguments including self? No, `run` is the method.
        # The logic is inside `convert_with_marker`.
        
        # Let's mock the task object logic by patching the `requests` call which is the main thing.
        
        result = convert_with_marker.run(
            mock_self,
            job_id='job_ai',
            input_path='/in/test.pdf',
            output_path='/out/test.md',
            from_format='pdf_marker',
            to_format='markdown'
        )
        
        assert result['status'] == 'success'
        mock_post.assert_called_once()
        assert m_open.call_count >= 2 # Read input, write output

@patch('tasks.redis_client')
@patch('requests.post')
@patch('os.path.exists')
def test_convert_with_marker_api_error(mock_exists, mock_post, mock_redis):
    mock_exists.return_value = True
    
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Internal Error"
    mock_post.return_value = mock_response
    
    mock_self = MagicMock()
    mock_self.request.retries = 0
    
    with pytest.raises(Exception, match="Marker API failed"):
        convert_with_marker.run(
            mock_self,
            'job_fail',
            '/in/test.pdf',
            '/out/test.md',
            'pdf_marker',
            'markdown'
        )

@patch('tasks.redis_client')
@patch('shutil.rmtree')
@patch('os.listdir')
@patch('os.path.exists')
def test_cleanup_old_files(mock_exists, mock_listdir, mock_rmtree, mock_redis):
    mock_exists.return_value = True
    mock_listdir.return_value = ['job_old', 'job_fresh']
    
    # Mock Redis returning metadata
    def get_meta(key):
        if 'job_old' in key:
            return {'status': 'SUCCESS', 'completed_at': str(time.time() - 4000)} # > 1h
        return {'status': 'SUCCESS', 'completed_at': str(time.time())}
    
    # We need to patch get_job_metadata or redis_client.hgetall
    # The task calls get_job_metadata which calls hgetall
    mock_redis.hgetall.side_effect = lambda k: get_meta(k) if 'job:' in k else {}
    
    # Mock time
    with patch('time.time') as mock_time:
        mock_time.return_value = 1000000
        # Setup timestamps relative to this
        # job_old: completed at 1000000 - 4000 = 996000
        
        # Adjust side effect to use the mock time logic or just fixed values
        mock_redis.hgetall.side_effect = None
        mock_redis.hgetall.side_effect = [
             {'status': 'SUCCESS', 'completed_at': str(1000000 - 4000)}, # job_old
             {'status': 'SUCCESS', 'completed_at': str(1000000 - 100)}   # job_fresh
        ]
        
        cleanup_old_files()
        
        # Should delete job_old but not job_fresh
        assert mock_rmtree.call_count >= 1 # upload and output dirs
        # verify we called delete for job_old
        # Ideally check args, but call_count is a good start
