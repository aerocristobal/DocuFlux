"""
Worker task tests - Epic 31.1: Updated to mock PdfConverter API correctly.

Previously tests mocked subprocess.run for Marker conversion, but the actual
implementation uses PdfConverter class imported inside the function body.

Key insight: PdfConverter is imported locally inside convert_with_marker():
    from marker.converters.pdf import PdfConverter
    converter = PdfConverter(artifact_dict=artifacts, config=options)

So we configure sys.modules['marker.converters.pdf'].PdfConverter directly,
rather than patching a non-existent tasks.PdfConverter attribute.
"""
import pytest
import sys
import uuid
import time
from unittest.mock import patch, MagicMock, mock_open

# ============================================================
# Mock all heavy dependencies before tasks.py imports them.
# These are local imports inside function bodies, so we mock
# at sys.modules level to intercept 'from X import Y' calls.
# ============================================================

_mock_modules = {
    'encryption': MagicMock(),
    'key_manager': MagicMock(),
    'secrets_manager': MagicMock(),
    'metrics': MagicMock(),
    'warmup': MagicMock(),
    'PIL': MagicMock(),
    'PIL.Image': MagicMock(),
    'flask_socketio': MagicMock(),
    'torch': MagicMock(),
    'marker': MagicMock(),
    'marker.models': MagicMock(),
    'marker.converters': MagicMock(),
    'marker.converters.pdf': MagicMock(),
    'marker.output': MagicMock(),
    'prometheus_client': MagicMock(),
}

for module_name, mock_mod in _mock_modules.items():
    sys.modules[module_name] = mock_mod

# secrets_manager: validate_secrets_at_startup returns empty dict
sys.modules['secrets_manager'].validate_secrets_at_startup.return_value = {}

# metrics: all counters/gauges need label chaining support
_c = MagicMock()
_c.labels.return_value = _c
_g = MagicMock()
_g.labels.return_value = _g

m = sys.modules['metrics']
m.conversion_total = _c
m.conversion_duration_seconds = _c
m.conversion_failures_total = _c
m.worker_tasks_active = _g
m.worker_info = MagicMock()
m.update_queue_metrics = MagicMock()
m.start_metrics_server = MagicMock()

# encryption/key_manager
sys.modules['encryption'].EncryptionService = MagicMock()
sys.modules['key_manager'].create_key_manager = MagicMock()

# warmup
sys.modules['warmup'].get_slm_model = MagicMock(return_value=None)

# torch.cuda needs to be callable
_torch = sys.modules['torch']
_torch.cuda.is_available.return_value = True
_torch.cuda.empty_cache = MagicMock()
_torch.cuda.memory_allocated.return_value = 0
_torch.cuda.memory_reserved.return_value = 0

# Now import tasks (all heavy deps are mocked)
import tasks


# ============================================================
# Helpers to configure marker mocks per-test
# ============================================================

def _make_pdf_converter_mock(text="# Output", images=None):
    """
    Configure sys.modules['marker.converters.pdf'].PdfConverter to return
    a mock converter that produces the given text and images.

    Returns the mock converter instance for assertion purposes.
    """
    if images is None:
        images = {}

    mock_rendered = MagicMock()
    mock_rendered.metadata = {'pages': 5, 'language': 'en'}

    mock_converter = MagicMock()
    mock_converter.return_value = mock_rendered  # converter(input_path) -> rendered

    # PdfConverter class instantiation: PdfConverter(...) -> mock_converter
    sys.modules['marker.converters.pdf'].PdfConverter.return_value = mock_converter

    # text_from_rendered(rendered) -> (text, {}, images)
    sys.modules['marker.output'].text_from_rendered.return_value = (text, {}, images)

    return mock_converter, mock_rendered


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(autouse=True)
def reset_marker_mocks():
    """Reset marker module mocks between tests to prevent interference."""
    _c.reset_mock()
    _g.reset_mock()
    sys.modules['marker.converters.pdf'].PdfConverter.reset_mock()
    sys.modules['marker.output'].text_from_rendered.reset_mock()
    sys.modules['marker.models'].create_model_dict.reset_mock()
    _torch.cuda.empty_cache.reset_mock()
    yield


@pytest.fixture
def sample_job_id():
    return str(uuid.uuid4())


# ============================================================
# convert_document tests (Pandoc - correctly uses subprocess)
# ============================================================

class TestConvertDocument:

    @patch('tasks.redis_client')
    @patch('tasks.socketio')
    @patch('subprocess.run')
    @patch('os.makedirs')
    @patch('os.path.exists')
    def test_success(self, mock_exists, mock_makedirs, mock_run,
                     mock_socketio, mock_redis, sample_job_id):
        """Pandoc conversion returns success and calls subprocess with pandoc."""
        mock_exists.return_value = True
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        mock_redis.hset = MagicMock()
        mock_redis.hgetall = MagicMock(return_value={})

        result = tasks.convert_document(
            job_id=sample_job_id,
            input_filename='test.md',
            output_filename='test.html',
            from_format='markdown',
            to_format='html'
        )

        assert result['status'] == 'success'
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert 'pandoc' in args
        assert '-f' in args
        assert 'markdown' in args

    @patch('tasks.redis_client')
    @patch('tasks.socketio')
    @patch('os.path.exists')
    def test_missing_input_file(self, mock_exists, mock_socketio,
                                mock_redis, sample_job_id):
        """Missing input file raises FileNotFoundError."""
        mock_exists.return_value = False
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        mock_pipe.execute.return_value = [1, {}]

        with pytest.raises(FileNotFoundError):
            tasks.convert_document(
                job_id=sample_job_id,
                input_filename='missing.md',
                output_filename='test.html',
                from_format='markdown',
                to_format='html'
            )

        mock_redis.hset.assert_called()

    @patch('tasks.redis_client')
    @patch('tasks.socketio')
    @patch('subprocess.run')
    @patch('os.makedirs')
    @patch('os.path.exists')
    def test_invalid_uuid_returns_error(self, mock_exists, mock_makedirs,
                                        mock_run, mock_socketio, mock_redis):
        """Invalid job_id returns error dict without subprocess call."""
        result = tasks.convert_document(
            job_id='not-a-uuid',
            input_filename='test.md',
            output_filename='test.html',
            from_format='markdown',
            to_format='html'
        )

        assert result['status'] == 'error'
        mock_run.assert_not_called()

    @patch('tasks.redis_client')
    @patch('tasks.socketio')
    @patch('subprocess.run')
    @patch('os.makedirs')
    @patch('os.path.exists')
    def test_pdf_output_uses_xelatex(self, mock_exists, mock_makedirs,
                                      mock_run, mock_socketio, mock_redis,
                                      sample_job_id):
        """PDF conversion includes XeLaTeX engine flag for CJK support."""
        mock_exists.return_value = True
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        mock_redis.hset = MagicMock()
        mock_redis.hgetall = MagicMock(return_value={})

        tasks.convert_document(
            job_id=sample_job_id,
            input_filename='test.md',
            output_filename='test.pdf',
            from_format='markdown',
            to_format='pdf'
        )

        args = mock_run.call_args[0][0]
        assert '--pdf-engine=xelatex' in args

    @patch('tasks.redis_client')
    @patch('tasks.socketio')
    @patch('subprocess.run')
    @patch('os.makedirs')
    @patch('os.path.exists')
    def test_pandoc_error_records_failure(self, mock_exists, mock_makedirs,
                                          mock_run, mock_socketio, mock_redis,
                                          sample_job_id):
        """Pandoc CalledProcessError records FAILURE and re-raises."""
        import subprocess
        mock_exists.return_value = True
        mock_run.side_effect = subprocess.CalledProcessError(
            1, ['pandoc'], stderr="pandoc: unknown format")
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        mock_pipe.execute.return_value = [1, {}]

        with pytest.raises(Exception, match="Pandoc failed"):
            tasks.convert_document(
                job_id=sample_job_id,
                input_filename='test.md',
                output_filename='test.html',
                from_format='markdown',
                to_format='html'
            )

        mock_redis.hset.assert_called()


# ============================================================
# convert_with_marker tests (Marker AI - uses PdfConverter API)
# ============================================================

class TestConvertWithMarker:

    @patch('tasks.redis_client')
    @patch('tasks.socketio')
    @patch('tasks.extract_slm_metadata')
    @patch('tasks.get_model_dict')
    @patch('os.makedirs')
    @patch('os.path.exists')
    @patch('builtins.open', new_callable=mock_open)
    def test_success_with_images(self, mock_file, mock_exists, mock_makedirs,
                                  mock_get_models, mock_slm,
                                  mock_socketio, mock_redis, sample_job_id):
        """PdfConverter is used (not subprocess), images are saved to images/ dir."""
        mock_exists.return_value = True
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        mock_pipe.execute.return_value = [1, {}]
        mock_get_models.return_value = {'layout': 'fake'}
        mock_slm.delay = MagicMock()

        mock_image = MagicMock()
        mock_converter, _ = _make_pdf_converter_mock(
            text="# Doc\n\n(page1.png)",
            images={'page1.png': mock_image}
        )

        result = tasks.convert_with_marker.run(
            sample_job_id, 'test.pdf', 'test.md', 'pdf', 'markdown'
        )

        assert result['status'] == 'success'
        # PdfConverter was instantiated (not subprocess)
        assert sys.modules['marker.converters.pdf'].PdfConverter.called
        # converter(input_path) was called
        assert mock_converter.called
        # text_from_rendered was called with the rendered result
        assert sys.modules['marker.output'].text_from_rendered.called
        # Image was saved
        mock_image.save.assert_called_once()

    @patch('tasks.redis_client')
    @patch('tasks.socketio')
    @patch('tasks.extract_slm_metadata')
    @patch('tasks.get_model_dict')
    @patch('os.makedirs')
    @patch('os.path.exists')
    @patch('builtins.open', new_callable=mock_open)
    def test_success_no_images(self, mock_file, mock_exists, mock_makedirs,
                                mock_get_models, mock_slm,
                                mock_socketio, mock_redis, sample_job_id):
        """Text-only PDF conversion succeeds with no images saved."""
        mock_exists.return_value = True
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        mock_pipe.execute.return_value = [1, {}]
        mock_get_models.return_value = {}
        mock_slm.delay = MagicMock()

        _make_pdf_converter_mock(text="# Plain Text", images={})

        result = tasks.convert_with_marker.run(
            sample_job_id, 'test.pdf', 'test.md', 'pdf', 'markdown'
        )

        assert result['status'] == 'success'

    @patch('tasks.redis_client')
    @patch('tasks.socketio')
    @patch('tasks.get_model_dict')
    @patch('os.makedirs')
    @patch('os.path.exists')
    def test_pdf_converter_exception_records_failure(self, mock_exists,
                                                      mock_makedirs,
                                                      mock_get_models,
                                                      mock_socketio, mock_redis,
                                                      sample_job_id):
        """PdfConverter RuntimeError sets FAILURE status and re-raises."""
        mock_exists.return_value = True
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        mock_pipe.execute.return_value = [1, {}]
        mock_get_models.return_value = {}

        # Make the PdfConverter instance raise when called
        mock_converter = MagicMock()
        mock_converter.side_effect = RuntimeError("CUDA out of memory")
        sys.modules['marker.converters.pdf'].PdfConverter.return_value = mock_converter

        with pytest.raises(RuntimeError, match="CUDA out of memory"):
            tasks.convert_with_marker.run(
                sample_job_id, 'test.pdf', 'test.md', 'pdf', 'markdown'
            )

        # FAILURE metadata must be recorded
        mock_redis.hset.assert_called()

    @patch('tasks.redis_client')
    @patch('tasks.socketio')
    @patch('os.path.exists')
    def test_missing_input_file(self, mock_exists, mock_socketio,
                                mock_redis, sample_job_id):
        """Missing PDF file raises FileNotFoundError."""
        mock_exists.return_value = False
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        mock_pipe.execute.return_value = [1, {}]

        with pytest.raises(FileNotFoundError):
            tasks.convert_with_marker.run(
                sample_job_id, 'test.pdf', 'test.md', 'pdf', 'markdown'
            )

    @patch('tasks.redis_client')
    @patch('tasks.socketio')
    @patch('os.path.exists')
    def test_invalid_uuid_returns_error(self, mock_exists, mock_socketio, mock_redis):
        """Invalid job_id returns error dict without conversion."""
        result = tasks.convert_with_marker.run(
            'not-a-valid-uuid', 'test.pdf', 'test.md', 'pdf', 'markdown'
        )

        assert result['status'] == 'error'

    @patch('tasks.redis_client')
    @patch('tasks.socketio')
    @patch('tasks.extract_slm_metadata')
    @patch('tasks.get_model_dict')
    @patch('os.makedirs')
    @patch('os.path.exists')
    @patch('builtins.open', new_callable=mock_open)
    def test_passes_options_to_pdf_converter(self, mock_file, mock_exists,
                                              mock_makedirs, mock_get_models,
                                              mock_slm, mock_socketio, mock_redis,
                                              sample_job_id):
        """Custom options are forwarded to PdfConverter constructor."""
        mock_exists.return_value = True
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        mock_pipe.execute.return_value = [1, {}]
        mock_get_models.return_value = {}
        mock_slm.delay = MagicMock()

        _make_pdf_converter_mock()

        options = {'page_range': '1-5', 'extract_images': True}

        tasks.convert_with_marker.run(
            sample_job_id, 'test.pdf', 'test.md', 'pdf', 'markdown',
            options=options
        )

        # PdfConverter was called with config=options
        call_kwargs = sys.modules['marker.converters.pdf'].PdfConverter.call_args
        assert call_kwargs is not None
        assert call_kwargs[1].get('config') == options


# ============================================================
# Lazy model loading tests (Epic 21.4)
# ============================================================

class TestLazyModelLoading:

    def setup_method(self):
        """Reset model_dict before each test."""
        self._original = tasks.model_dict
        tasks.model_dict = None

    def teardown_method(self):
        """Restore model_dict after each test."""
        tasks.model_dict = self._original

    def test_first_call_loads_models(self):
        """get_model_dict calls create_model_dict on first invocation."""
        mock_artifacts = {'layout': 'model', 'recognition': 'model'}
        sys.modules['marker.models'].create_model_dict.return_value = mock_artifacts

        result = tasks.get_model_dict()

        assert result == mock_artifacts
        sys.modules['marker.models'].create_model_dict.assert_called_once()

    def test_subsequent_calls_use_cache(self):
        """get_model_dict returns cached dict without reloading models."""
        mock_artifacts = {'cached': True}
        sys.modules['marker.models'].create_model_dict.return_value = mock_artifacts

        result1 = tasks.get_model_dict()
        result2 = tasks.get_model_dict()

        assert result1 is result2
        # create_model_dict should only be called once
        assert sys.modules['marker.models'].create_model_dict.call_count == 1

    def test_preloaded_cache_not_reloaded(self):
        """If model_dict already set, create_model_dict is never called."""
        tasks.model_dict = {'preloaded': True}

        result = tasks.get_model_dict()

        assert result == {'preloaded': True}
        sys.modules['marker.models'].create_model_dict.assert_not_called()


# ============================================================
# Cleanup tests
# ============================================================

class TestCleanupOldFiles:

    @patch('tasks.redis_client')
    @patch('tasks.socketio')
    @patch('tasks._get_disk_usage_percent', return_value=50.0)
    @patch('tasks._get_directory_size', return_value=1024)
    @patch('shutil.rmtree')
    @patch('os.listdir')
    @patch('os.path.exists')
    def test_deletes_expired_success_jobs(self, mock_exists, mock_listdir,
                                           mock_rmtree, mock_dir_size,
                                           mock_disk, mock_socketio, mock_redis):
        """SUCCESS jobs past retention window are deleted."""
        mock_exists.return_value = True
        job_expired = str(uuid.uuid4())
        job_fresh = str(uuid.uuid4())
        mock_listdir.return_value = [job_expired, job_fresh]
        mock_redis.delete = MagicMock()

        now = time.time()

        def get_meta(key):
            if job_expired in key:
                return {'status': 'SUCCESS', 'completed_at': str(now - 7200)}  # 2h ago
            return {'status': 'SUCCESS', 'completed_at': str(now - 300)}  # 5m ago

        mock_redis.hgetall.side_effect = lambda k: get_meta(k) if 'job:' in k else {}

        with patch('time.time', return_value=now):
            tasks.cleanup_old_files()

        # At least the expired job should be deleted
        assert mock_rmtree.call_count >= 1

    @patch('tasks.redis_client')
    @patch('tasks.socketio')
    @patch('tasks._get_disk_usage_percent', return_value=50.0)
    @patch('shutil.rmtree')
    @patch('os.listdir')
    @patch('os.path.exists')
    def test_skips_non_uuid_entries(self, mock_exists, mock_listdir, mock_rmtree,
                                     mock_disk, mock_socketio, mock_redis):
        """Directories with non-UUID names are silently skipped."""
        mock_exists.return_value = True
        mock_listdir.return_value = ['.gitkeep', 'not-a-uuid', 'also_invalid']
        mock_redis.hgetall = MagicMock(return_value={})

        tasks.cleanup_old_files()

        mock_rmtree.assert_not_called()

    @patch('tasks.redis_client')
    @patch('tasks.socketio')
    @patch('tasks._get_disk_usage_percent', return_value=50.0)
    @patch('tasks._get_directory_size', return_value=1024)
    @patch('shutil.rmtree')
    @patch('os.listdir')
    @patch('os.path.exists')
    def test_deletes_failure_jobs_after_5_minutes(self, mock_exists, mock_listdir,
                                                    mock_rmtree, mock_dir_size,
                                                    mock_disk, mock_socketio, mock_redis):
        """FAILURE jobs are deleted after 5 minutes (faster than SUCCESS)."""
        mock_exists.return_value = True
        job_failed = str(uuid.uuid4())
        mock_listdir.return_value = [job_failed]
        mock_redis.delete = MagicMock()

        now = time.time()
        mock_redis.hgetall.return_value = {
            'status': 'FAILURE',
            'completed_at': str(now - 360)  # 6 minutes ago > 5min threshold
        }

        with patch('time.time', return_value=now):
            tasks.cleanup_old_files()

        mock_rmtree.assert_called()

    @patch('tasks.redis_client')
    @patch('tasks.socketio')
    @patch('tasks._get_disk_usage_percent', return_value=50.0)
    @patch('shutil.rmtree')
    @patch('os.listdir')
    @patch('os.path.exists')
    def test_preserves_fresh_jobs(self, mock_exists, mock_listdir, mock_rmtree,
                                   mock_disk, mock_socketio, mock_redis):
        """Jobs completed recently are NOT deleted."""
        mock_exists.return_value = True
        fresh_job = str(uuid.uuid4())
        mock_listdir.return_value = [fresh_job]

        now = time.time()
        mock_redis.hgetall.return_value = {
            'status': 'SUCCESS',
            'completed_at': str(now - 60)  # Only 1 minute ago
        }

        with patch('time.time', return_value=now):
            tasks.cleanup_old_files()

        mock_rmtree.assert_not_called()


# ============================================================
# GPU memory cleanup tests (Epic 21.4)
# ============================================================

class TestGPUMemoryCleanup:

    @patch('tasks.redis_client')
    @patch('tasks.socketio')
    @patch('tasks.extract_slm_metadata')
    @patch('tasks.get_model_dict')
    @patch('os.makedirs')
    @patch('os.path.exists')
    @patch('builtins.open', new_callable=mock_open)
    def test_cuda_empty_cache_called_after_success(self, mock_file, mock_exists,
                                                    mock_makedirs, mock_get_models,
                                                    mock_slm, mock_socketio,
                                                    mock_redis, sample_job_id):
        """torch.cuda.empty_cache() is called after successful Marker conversion."""
        mock_exists.return_value = True
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        mock_pipe.execute.return_value = [1, {}]
        mock_get_models.return_value = {}
        mock_slm.delay = MagicMock()

        _make_pdf_converter_mock()

        tasks.convert_with_marker.run(
            sample_job_id, 'test.pdf', 'test.md', 'pdf', 'markdown'
        )

        _torch.cuda.empty_cache.assert_called()

    @patch('tasks.redis_client')
    @patch('tasks.socketio')
    @patch('tasks.get_model_dict')
    @patch('os.makedirs')
    @patch('os.path.exists')
    def test_cuda_empty_cache_called_after_failure(self, mock_exists,
                                                    mock_makedirs, mock_get_models,
                                                    mock_socketio, mock_redis,
                                                    sample_job_id):
        """torch.cuda.empty_cache() is called even when Marker conversion fails."""
        mock_exists.return_value = True
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        mock_pipe.execute.return_value = [1, {}]
        mock_get_models.return_value = {}

        mock_converter = MagicMock()
        mock_converter.side_effect = RuntimeError("Conversion error")
        sys.modules['marker.converters.pdf'].PdfConverter.return_value = mock_converter

        with pytest.raises(RuntimeError):
            tasks.convert_with_marker.run(
                sample_job_id, 'test.pdf', 'test.md', 'pdf', 'markdown'
            )

        _torch.cuda.empty_cache.assert_called()


# ============================================================
# Utility function tests - improves branch coverage
# ============================================================

class TestGetJobMetadata:

    @patch('tasks.redis_client')
    def test_returns_none_for_empty_metadata(self, mock_redis):
        """Returns None when Redis hash is empty."""
        mock_redis.hgetall.return_value = {}
        result = tasks.get_job_metadata(str(uuid.uuid4()))
        assert result is None

    @patch('tasks.redis_client')
    def test_returns_none_on_exception(self, mock_redis):
        """Returns None when Redis raises an exception."""
        mock_redis.hgetall.side_effect = Exception("connection refused")
        result = tasks.get_job_metadata(str(uuid.uuid4()))
        assert result is None

    @patch('tasks.redis_client')
    def test_decodes_bytes_keys_and_values(self, mock_redis):
        """Byte keys/values are decoded to strings."""
        mock_redis.hgetall.return_value = {
            b'status': b'SUCCESS',
            'filename': 'test.md',
        }
        result = tasks.get_job_metadata(str(uuid.uuid4()))
        assert result['status'] == 'SUCCESS'
        assert result['filename'] == 'test.md'


class TestDiskUtilities:

    @patch('shutil.disk_usage')
    def test_get_disk_usage_percent(self, mock_usage):
        """Returns correct percentage from disk_usage."""
        mock_usage.return_value = (1000, 400, 600)
        pct = tasks._get_disk_usage_percent('/tmp')
        assert pct == 40.0

    @patch('shutil.disk_usage')
    def test_get_disk_usage_percent_error(self, mock_usage):
        """Returns 0.0 on exception."""
        mock_usage.side_effect = OSError("no such path")
        pct = tasks._get_disk_usage_percent('/nonexistent')
        assert pct == 0.0

    def test_get_directory_size_empty(self, tmp_path):
        """Returns 0 for empty directory."""
        size = tasks._get_directory_size(str(tmp_path))
        assert size == 0

    def test_get_directory_size_with_files(self, tmp_path):
        """Returns sum of file sizes."""
        (tmp_path / 'a.txt').write_bytes(b'hello')
        (tmp_path / 'b.txt').write_bytes(b'world!')
        size = tasks._get_directory_size(str(tmp_path))
        assert size == 11

    @patch('os.walk')
    def test_get_directory_size_error(self, mock_walk):
        """Returns 0 and logs on exception."""
        mock_walk.side_effect = PermissionError("no access")
        size = tasks._get_directory_size('/restricted')
        assert size == 0


class TestJobRetentionDecision:

    def test_downloaded_job_expires(self):
        """Jobs with downloaded_at beyond window are marked for deletion."""
        now = time.time()
        meta = {'status': 'SUCCESS', 'completed_at': str(now - 1200),
                'downloaded_at': str(now - 700)}
        should_delete, reason, priority = tasks._job_retention_decision(
            str(uuid.uuid4()), meta, now, '/up', '/out',
            300, 600, 3600, 3600, False
        )
        assert should_delete is True

    def test_orphan_job_expires(self):
        """Jobs with no Redis metadata but old files are marked for deletion."""
        now = time.time()
        job_id = str(uuid.uuid4())
        with patch('os.path.exists', return_value=True), \
             patch('os.path.getmtime', return_value=now - 7200):
            should_delete, reason, priority = tasks._job_retention_decision(
                job_id, None, now, '/up', '/out',
                300, 600, 3600, 3600, False
            )
        assert should_delete is True

    def test_stale_processing_job_expires(self):
        """PROCESSING jobs with no completed_at older than 2h are marked for deletion."""
        now = time.time()
        meta = {'status': 'PROCESSING', 'started_at': str(now - 7201)}
        should_delete, reason, priority = tasks._job_retention_decision(
            str(uuid.uuid4()), meta, now, '/up', '/out',
            300, 600, 3600, 3600, False
        )
        assert should_delete is True
        assert 'Stale' in reason

    def test_emergency_cleanup_forces_deletion(self):
        """Emergency cleanup marks all jobs for deletion regardless of age."""
        now = time.time()
        meta = {'status': 'SUCCESS', 'completed_at': str(now - 60)}
        should_delete, reason, priority = tasks._job_retention_decision(
            str(uuid.uuid4()), meta, now, '/up', '/out',
            300, 600, 3600, 3600, True
        )
        assert should_delete is True
        assert priority == 15
        assert 'EMERGENCY' in reason


class TestUpdateMetrics:

    @patch('tasks.redis_client')
    def test_update_metrics_success(self, mock_redis):
        """update_metrics calls update_queue_metrics without error."""
        tasks.update_metrics()
        sys.modules['metrics'].update_queue_metrics.assert_called()

    @patch('tasks.redis_client')
    def test_update_metrics_handles_exception(self, mock_redis):
        """update_metrics swallows exceptions gracefully."""
        sys.modules['metrics'].update_queue_metrics.side_effect = Exception("redis down")
        tasks.update_metrics()  # should not raise
        sys.modules['metrics'].update_queue_metrics.side_effect = None
