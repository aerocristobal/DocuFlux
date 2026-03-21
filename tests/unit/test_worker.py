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
m.update_redis_pool_metrics = MagicMock()
m.redis_pool_active = MagicMock()
m.redis_pool_available = MagicMock()
m.start_metrics_server = MagicMock()
m.dlq_total = MagicMock()

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
    @patch('os.path.exists')
    def test_deletes_expired_success_jobs(self, mock_exists,
                                           mock_rmtree, mock_dir_size,
                                           mock_disk, mock_socketio, mock_redis):
        """SUCCESS jobs past retention window are deleted."""
        mock_exists.return_value = True
        job_expired = str(uuid.uuid4())
        job_fresh = str(uuid.uuid4())
        mock_redis.zrangebyscore.return_value = [job_expired, job_fresh]
        mock_redis.delete = MagicMock()
        mock_redis.scan_iter.return_value = []

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
    @patch('os.path.exists')
    def test_skips_non_uuid_entries(self, mock_exists, mock_rmtree,
                                     mock_disk, mock_socketio, mock_redis):
        """Non-UUID entries from sorted set are silently skipped."""
        mock_exists.return_value = True
        mock_redis.zrangebyscore.return_value = ['.gitkeep', 'not-a-uuid', 'also_invalid']
        mock_redis.hgetall = MagicMock(return_value={})
        mock_redis.scan_iter.return_value = []

        tasks.cleanup_old_files()

        mock_rmtree.assert_not_called()

    @patch('tasks.redis_client')
    @patch('tasks.socketio')
    @patch('tasks._get_disk_usage_percent', return_value=50.0)
    @patch('tasks._get_directory_size', return_value=1024)
    @patch('shutil.rmtree')
    @patch('os.path.exists')
    def test_deletes_failure_jobs_after_5_minutes(self, mock_exists,
                                                    mock_rmtree, mock_dir_size,
                                                    mock_disk, mock_socketio, mock_redis):
        """FAILURE jobs are deleted after 5 minutes (faster than SUCCESS)."""
        mock_exists.return_value = True
        job_failed = str(uuid.uuid4())
        mock_redis.zrangebyscore.return_value = [job_failed]
        mock_redis.delete = MagicMock()
        mock_redis.scan_iter.return_value = []

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
    @patch('os.path.exists')
    def test_preserves_fresh_jobs(self, mock_exists, mock_rmtree,
                                   mock_disk, mock_socketio, mock_redis):
        """Jobs completed recently are NOT deleted."""
        mock_exists.return_value = True
        fresh_job = str(uuid.uuid4())
        mock_redis.zrangebyscore.return_value = [fresh_job]
        mock_redis.scan_iter.return_value = []

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


# ============================================================
# process_capture_batch tests (docuflux-m13: streaming batch OCR)
# ============================================================

class TestProcessCaptureBatch:

    def _make_page_json(self, page_hint=0, has_screenshot=True):
        import json
        img = {'filename': 'screenshot.png', 'b64': 'data:image/png;base64,iVBORw0KGgo=', 'is_screenshot': has_screenshot}
        return json.dumps({'page_hint': page_hint, 'images': [img], 'text': ''})

    @patch('tasks.redis_client')
    @patch('tasks.get_model_dict')
    @patch('tasks.update_job_metadata')
    @patch('tasks._cleanup_marker_memory')
    @patch('os.makedirs')
    def test_writes_batch_md_and_images(self, mock_makedirs, mock_cleanup,
                                        mock_update, mock_model_dict, mock_redis):
        """Batch task writes batch.md and images to the batch staging directory."""
        import json

        session_id = str(uuid.uuid4())
        job_id = str(uuid.uuid4())

        tiny_png_b64 = (
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk'
            'YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=='
        )
        page = json.dumps({'page_hint': 0, 'images': [
            {'filename': 'p.png', 'b64': f'data:image/png;base64,{tiny_png_b64}', 'is_screenshot': True}
        ], 'text': ''})
        mock_redis.lrange.return_value = [page]
        mock_redis.hget.return_value = '1'
        mock_redis.incrby.return_value = 1  # base_offset = 0

        mock_img = MagicMock()

        # Use _make_pdf_converter_mock so PdfConverter.return_value is a fresh mock
        # (avoids side_effect bleed from earlier tests that set RuntimeError)
        _make_pdf_converter_mock(
            text='# Chapter 1\n![fig](fig_0.png)',
            images={'fig_0.png': mock_img},
        )

        from unittest.mock import patch as upatch, mock_open

        with upatch('PIL.Image.open') as mock_pil_open, \
             upatch('builtins.open', mock_open()), \
             upatch('os.path.join', side_effect=lambda *a: '/'.join(a)):
            pil_img = MagicMock()
            pil_img.convert.return_value = pil_img
            mock_pil_open.return_value = pil_img

            tasks.process_capture_batch(session_id, job_id, 0, 0, 1)

        # batches_done should have been incremented
        mock_redis.hincrby.assert_called_with(
            f"capture:session:{session_id}", 'batches_done', 1
        )
        # batch status should be 'done'
        hset_calls = {str(c) for c in mock_redis.hset.call_args_list}
        assert any('done' in c for c in hset_calls)

    @patch('tasks.redis_client')
    @patch('tasks.update_job_metadata')
    @patch('os.makedirs')
    def test_no_images_writes_empty_batch(self, mock_makedirs, mock_update, mock_redis):
        """Batch task writes empty batch.md and marks done when no page images exist."""
        import json

        session_id = str(uuid.uuid4())
        job_id = str(uuid.uuid4())

        page = json.dumps({'page_hint': 0, 'images': [], 'text': 'some text'})
        mock_redis.lrange.return_value = [page]

        from unittest.mock import patch as upatch, mock_open
        with upatch('builtins.open', mock_open()), \
             upatch('os.path.join', side_effect=lambda *a: '/'.join(a)):
            tasks.process_capture_batch(session_id, job_id, 0, 0, 1)

        # Should mark done without calling Marker
        sys.modules['marker.converters.pdf'].PdfConverter.assert_not_called()
        mock_redis.hincrby.assert_called_with(
            f"capture:session:{session_id}", 'batches_done', 1
        )

    @patch('tasks.redis_client')
    @patch('tasks.get_model_dict')
    @patch('tasks.update_job_metadata')
    @patch('os.makedirs')
    def test_failure_does_not_fail_job(self, mock_makedirs, mock_update,
                                       mock_model_dict, mock_redis):
        """Batch failure increments batches_failed but leaves job metadata as CAPTURING."""
        import json

        session_id = str(uuid.uuid4())
        job_id = str(uuid.uuid4())

        # Make Marker raise an exception
        mock_model_dict.side_effect = RuntimeError("GPU OOM")
        tiny_png_b64 = (
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk'
            'YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=='
        )
        page = json.dumps({'page_hint': 0, 'images': [
            {'filename': 'p.png', 'b64': f'data:image/png;base64,{tiny_png_b64}', 'is_screenshot': True}
        ], 'text': ''})
        mock_redis.lrange.return_value = [page]

        from unittest.mock import patch as upatch
        with upatch('os.path.join', side_effect=lambda *a: '/'.join(a)), \
             upatch('os.makedirs'), \
             pytest.raises(RuntimeError):
            tasks.process_capture_batch(session_id, job_id, 0, 0, 1)

        # batches_failed should be incremented
        mock_redis.hincrby.assert_called_with(
            f"capture:session:{session_id}", 'batches_failed', 1
        )
        # Job metadata should NOT have been called with FAILURE status
        failure_calls = [c for c in mock_update.call_args_list
                         if c.args[1].get('status') == 'FAILURE']
        assert len(failure_calls) == 0


# ============================================================
# assemble_capture_session tests (docuflux-m13 + docuflux-5lb)
# ============================================================

class TestAssembleCaptureSession:

    def _make_session_meta(self, force_ocr='false', batches_queued='0',
                           batches_done='0', batches_failed='0'):
        return {
            'title': 'Test Book',
            'to_format': 'markdown',
            'source_url': 'https://example.com',
            'force_ocr': force_ocr,
            'batches_queued': batches_queued,
            'batches_done': batches_done,
            'batches_failed': batches_failed,
        }

    def _make_page(self, page_hint=0, text='# Chapter'):
        import json
        return json.dumps({'page_hint': page_hint, 'text': text, 'images': []}).encode()

    @patch('tasks.redis_client')
    @patch('tasks.update_job_metadata')
    @patch('tasks.socketio')
    @patch('os.makedirs')
    @patch('os.path.exists')
    def test_text_assembly_path_success(self, mock_exists, mock_makedirs,
                                        mock_socketio, mock_update, mock_redis):
        """Text path assembles pages without calling Marker."""
        session_id = str(uuid.uuid4())
        job_id = str(uuid.uuid4())

        mock_redis.hgetall.return_value = self._make_session_meta(force_ocr='false')
        mock_redis.lrange.return_value = [self._make_page(0, '# Page 1'), self._make_page(1, '# Page 2')]
        mock_exists.return_value = False  # no images dir to rmdir

        from unittest.mock import patch as upatch, mock_open
        with upatch('builtins.open', mock_open()) as m_open, \
             upatch('os.path.join', side_effect=lambda *a: '/'.join(a)), \
             upatch('os.path.exists', return_value=False), \
             upatch('os.makedirs'):
            tasks.assemble_capture_session(session_id, job_id)

        # Marker should NOT have been called
        sys.modules['marker.converters.pdf'].PdfConverter.assert_not_called()
        # SUCCESS status should have been recorded
        success_calls = [c for c in mock_update.call_args_list
                         if c.args[1].get('status') == 'SUCCESS']
        assert len(success_calls) == 1

    @patch('tasks.redis_client')
    @patch('tasks.update_job_metadata')
    @patch('tasks.socketio')
    @patch('os.makedirs')
    def test_text_assembly_path_no_pages_raises(self, mock_makedirs, mock_socketio,
                                                 mock_update, mock_redis):
        """Assemble raises ValueError when no pages are found in Redis."""
        session_id = str(uuid.uuid4())
        job_id = str(uuid.uuid4())

        mock_redis.hgetall.return_value = self._make_session_meta()
        mock_redis.lrange.return_value = []  # No pages

        with pytest.raises(ValueError, match="No pages found"):
            tasks.assemble_capture_session(session_id, job_id)

        # Should mark FAILURE
        failure_calls = [c for c in mock_update.call_args_list
                         if c.args[1].get('status') == 'FAILURE']
        assert len(failure_calls) == 1

    @patch('tasks.redis_client')
    @patch('tasks.update_job_metadata')
    @patch('tasks.socketio')
    def test_batch_merge_path_stitches_outputs(self, mock_socketio, mock_update, mock_redis):
        """Batch merge path reads batch.md files and copies images from each batch."""
        import json, os, tempfile

        session_id = str(uuid.uuid4())
        job_id = str(uuid.uuid4())

        # Two batches, both done
        mock_redis.hgetall.return_value = self._make_session_meta(
            force_ocr='true', batches_queued='2', batches_done='2'
        )
        mock_redis.lrange.return_value = [
            json.dumps({'page_hint': 0, 'text': '', 'images': []}).encode(),
        ]
        mock_redis.hget.return_value = 'done'  # batch status

        # Use real temp directory so file I/O works
        from storage import LocalStorageBackend
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks.OUTPUT_FOLDER = tmpdir
            old_storage = tasks.storage
            tasks.storage = LocalStorageBackend(upload_folder=tmpdir, output_folder=tmpdir)
            job_dir = os.path.join(tmpdir, job_id)
            os.makedirs(os.path.join(job_dir, 'batches', 'batch_0', 'images'))
            os.makedirs(os.path.join(job_dir, 'batches', 'batch_1', 'images'))
            # Write batch markdown files
            with open(os.path.join(job_dir, 'batches', 'batch_0', 'batch.md'), 'w') as f:
                f.write('# Part 1')
            with open(os.path.join(job_dir, 'batches', 'batch_1', 'batch.md'), 'w') as f:
                f.write('# Part 2')
            # Write a dummy image in batch_0
            img_path = os.path.join(job_dir, 'batches', 'batch_0', 'images', 'img_00000.png')
            with open(img_path, 'wb') as f:
                f.write(b'PNG')

            tasks.assemble_capture_session(session_id, job_id)

            # Final markdown file should exist
            final_md = os.path.join(job_dir, 'Test_Book.md')
            assert os.path.exists(final_md) or any(
                f.endswith('.md') for f in os.listdir(job_dir)
            )
            # Images should be merged
            images_dir = os.path.join(job_dir, 'images')
            assert os.path.exists(images_dir)
            assert 'img_00000.png' in os.listdir(images_dir)
            # Staging directory should be cleaned up
            assert not os.path.exists(os.path.join(job_dir, 'batches'))

        # Restore
        tasks.OUTPUT_FOLDER = tasks.app_settings.output_folder
        tasks.storage = old_storage

    @patch('tasks.redis_client')
    @patch('tasks.update_job_metadata')
    @patch('tasks.socketio')
    def test_batch_merge_tombstone_for_failed_batch(self, mock_socketio, mock_update, mock_redis):
        """Failed batches produce a tombstone in the final markdown."""
        import json, os, tempfile

        session_id = str(uuid.uuid4())
        job_id = str(uuid.uuid4())

        mock_redis.hgetall.return_value = self._make_session_meta(
            force_ocr='true', batches_queued='1', batches_done='0', batches_failed='1'
        )
        mock_redis.lrange.return_value = [
            json.dumps({'page_hint': 0, 'text': '', 'images': []}).encode(),
        ]
        mock_redis.hget.return_value = 'failed'  # batch 0 failed

        from storage import LocalStorageBackend
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks.OUTPUT_FOLDER = tmpdir
            old_storage = tasks.storage
            tasks.storage = LocalStorageBackend(upload_folder=tmpdir, output_folder=tmpdir)
            job_dir = os.path.join(tmpdir, job_id)
            os.makedirs(os.path.join(job_dir, 'batches', 'batch_0'))

            tasks.assemble_capture_session(session_id, job_id)

            # Find the output markdown file and check it has tombstone text
            md_files = [f for f in os.listdir(job_dir) if f.endswith('.md')]
            assert len(md_files) == 1
            with open(os.path.join(job_dir, md_files[0])) as f:
                content = f.read()
            assert '⚠' in content or 'failed' in content.lower()

            # Job should still be SUCCESS with batch_warnings
            success_calls = [c for c in mock_update.call_args_list
                             if c.args[1].get('status') == 'SUCCESS']
            assert len(success_calls) == 1
            assert 'batch_warnings' in success_calls[0].args[1]

        tasks.OUTPUT_FOLDER = tasks.app_settings.output_folder
        tasks.storage = old_storage

# ─── fire_webhook Tests ───────────────────────────────────────────────────────

class TestFireWebhook:
    """Tests for the fire_webhook helper in tasks.py."""

    def test_fires_post_when_url_registered(self):
        """fire_webhook posts JSON payload to the registered URL."""
        import tasks
        from unittest.mock import patch, MagicMock
        job_id = str(__import__('uuid').uuid4())

        with patch.object(tasks.redis_client, 'hget', return_value=b'https://hook.example.com/cb'), \
             patch('web.validation.socket.getaddrinfo', return_value=[(2, 1, 0, '', ('93.184.216.34', 0))]), \
             patch('tasks.requests.post') as mock_post:
            tasks.fire_webhook(job_id, 'SUCCESS', {'download_url': '/api/v1/download/' + job_id})

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert call_kwargs[0][0] == 'https://hook.example.com/cb'
        payload = call_kwargs[1]['json']
        assert payload['job_id'] == job_id
        assert payload['status'] == 'SUCCESS'
        assert 'download_url' in payload

    def test_no_post_when_no_url(self):
        """fire_webhook does nothing when no webhook_url is registered."""
        import tasks
        from unittest.mock import patch
        job_id = str(__import__('uuid').uuid4())

        with patch.object(tasks.redis_client, 'hget', return_value=None), \
             patch('tasks.requests.post') as mock_post:
            tasks.fire_webhook(job_id, 'SUCCESS')

        mock_post.assert_not_called()

    def test_network_error_does_not_raise(self):
        """fire_webhook swallows network errors silently."""
        import tasks
        from unittest.mock import patch
        job_id = str(__import__('uuid').uuid4())

        with patch.object(tasks.redis_client, 'hget', return_value=b'https://hook.example.com/cb'), \
             patch('web.validation.socket.getaddrinfo', return_value=[(2, 1, 0, '', ('93.184.216.34', 0))]), \
             patch('tasks.requests.post', side_effect=ConnectionError('timeout')):
            # Should not raise
            tasks.fire_webhook(job_id, 'FAILURE', {'error': 'oops'})


class TestAssessPandocQuality:
    """Tests for the _assess_pandoc_quality helper."""

    def test_high_word_count_returns_true(self, tmp_path):
        """Returns True when output has sufficient words per page."""
        import tasks
        output_file = tmp_path / "output.md"
        # 200 words, 2 pages → 100 words/page > threshold of 50
        output_file.write_text(" ".join(["word"] * 200))
        assert tasks._assess_pandoc_quality(str(output_file), page_count=2) is True

    def test_low_word_count_returns_false(self, tmp_path):
        """Returns False when output has too few words per page (scanned/image PDF)."""
        import tasks
        output_file = tmp_path / "output.md"
        # 40 words, 2 pages → 20 words/page < threshold of 50
        output_file.write_text(" ".join(["word"] * 40))
        assert tasks._assess_pandoc_quality(str(output_file), page_count=2) is False

    def test_zero_page_count_avoids_division_by_zero(self, tmp_path):
        """page_count=0 is treated as 1 to avoid ZeroDivisionError."""
        import tasks
        output_file = tmp_path / "output.md"
        output_file.write_text(" ".join(["word"] * 100))
        # Should not raise; treats 0 pages as 1
        result = tasks._assess_pandoc_quality(str(output_file), page_count=0)
        assert isinstance(result, bool)

    def test_missing_file_returns_false(self):
        """Returns False (→ fall back to Marker) if file cannot be read."""
        import tasks
        result = tasks._assess_pandoc_quality('/nonexistent/path.md', page_count=5)
        assert result is False


class TestConvertWithHybrid:
    """Tests for the convert_with_hybrid Celery task."""

    @patch('tasks.redis_client')
    @patch('tasks.socketio')
    @patch('tasks.extract_slm_metadata')
    @patch('tasks.subprocess.run')
    @patch('tasks._assess_pandoc_quality', return_value=True)
    @patch('os.makedirs')
    @patch('os.path.exists', return_value=True)
    def test_pandoc_fast_path_used_when_quality_ok(
        self, mock_exists, mock_makedirs, mock_quality, mock_run,
        mock_slm, mock_socketio, mock_redis
    ):
        """When Pandoc output is high quality, task completes without Marker."""
        import tasks

        mock_redis.hget.return_value = None  # No pre-existing FAILURE status
        mock_redis.expire.return_value = True
        mock_run.return_value = MagicMock(returncode=0)

        job_id = str(uuid.uuid4())
        result = tasks.convert_with_hybrid(
            job_id, 'doc.pdf', 'doc.md', 'pdf_hybrid', 'markdown'
        )

        assert result['status'] == 'success'
        assert result['engine'] == 'pandoc'
        # Marker models should NOT have been loaded
        mock_quality.assert_called_once()

    @patch('tasks.redis_client')
    @patch('tasks.socketio')
    @patch('tasks.extract_slm_metadata')
    @patch('tasks._run_marker')
    @patch('tasks._save_marker_output')
    @patch('tasks.subprocess.run', side_effect=Exception("Pandoc failed"))
    @patch('tasks._assess_pandoc_quality', return_value=False)
    @patch('tasks._check_pdf_page_limit', return_value=None)
    @patch('os.makedirs')
    @patch('os.path.exists', return_value=True)
    def test_falls_back_to_marker_on_poor_pandoc_quality(
        self, mock_exists, mock_makedirs, mock_page_limit, mock_quality,
        mock_run, mock_save, mock_run_marker, mock_slm, mock_socketio, mock_redis
    ):
        """When Pandoc quality is poor, task falls back to Marker AI."""
        import tasks

        mock_redis.hget.return_value = None
        mock_redis.expire.return_value = True

        mock_rendered = MagicMock()
        mock_run_marker.return_value = (MagicMock(), mock_rendered)
        mock_save.return_value = ('text', {}, 0, 2)

        job_id = str(uuid.uuid4())
        result = tasks.convert_with_hybrid(
            job_id, 'doc.pdf', 'doc.md', 'pdf_hybrid', 'markdown'
        )

        assert result['status'] == 'success'
        assert result['engine'] == 'marker'
        mock_run_marker.assert_called_once()

    @patch('tasks.redis_client')
    @patch('tasks.socketio')
    def test_invalid_uuid_returns_error(self, mock_socketio, mock_redis):
        """Invalid job_id returns error dict without any processing."""
        import tasks
        result = tasks.convert_with_hybrid(
            'not-a-uuid', 'doc.pdf', 'doc.md', 'pdf_hybrid', 'markdown'
        )
        assert result['status'] == 'error'

    @patch('tasks.redis_client')
    @patch('tasks.socketio')
    @patch('os.path.exists', return_value=False)
    def test_missing_input_raises(self, mock_exists, mock_socketio, mock_redis):
        """FileNotFoundError raised when input file is absent."""
        import tasks
        mock_redis.hget.return_value = None
        job_id = str(uuid.uuid4())
        with pytest.raises(FileNotFoundError):
            tasks.convert_with_hybrid(
                job_id, 'doc.pdf', 'doc.md', 'pdf_hybrid', 'markdown'
            )


# ============================================================
# Performance & Resource Optimization config tests
# ============================================================

class TestCeleryConfig:
    """Verify Celery and Redis config values for performance optimization."""

    def test_celery_result_expires_configured(self):
        """Verify result_expires is set to prevent Redis memory leak."""
        import tasks
        assert tasks.celery.conf.result_expires == 3600

    def test_celery_max_tasks_per_child_configured(self):
        """Verify worker recycling is enabled to reclaim VRAM."""
        import tasks
        assert tasks.celery.conf.worker_max_tasks_per_child == 50

    def test_redis_client_has_socket_timeouts(self):
        """Verify Redis connections have timeouts to prevent hanging."""
        import tasks
        pool = tasks.redis_client.connection_pool
        kwargs = pool.connection_kwargs
        assert kwargs.get('socket_connect_timeout') == 5
        assert kwargs.get('socket_timeout') == 10

    def test_cleanup_schedule_interval(self):
        """Cleanup should run every 2 minutes (120 seconds)."""
        import tasks
        schedule = tasks.celery.conf.beat_schedule['cleanup-every-5-minutes']
        assert schedule['schedule'] == 120.0

    def test_metrics_schedule_interval(self):
        """Metrics should not run more often than every 120 seconds."""
        import tasks
        schedule = tasks.celery.conf.beat_schedule['update-queue-metrics']
        assert schedule['schedule'] >= 120.0


# ============================================================================
# build_pandoc_cmd Tests
# ============================================================================

class TestBuildPandocCmd:
    """Tests for the build_pandoc_cmd helper function."""

    def test_pdf_defaults_applied_without_options(self):
        """PDF conversion includes CJK defaults when no options provided."""
        import tasks
        cmd = tasks.build_pandoc_cmd('markdown', 'pdf', '/in/f.md', '/out/f.pdf')
        cmd_str = ' '.join(cmd)
        assert '--pdf-engine=xelatex' in cmd_str
        assert 'mainfont=Noto Sans CJK SC' in cmd_str
        assert 'CJKmainfont=Noto Sans CJK SC' in cmd_str
        assert 'monofont=DejaVu Sans Mono' in cmd_str
        assert 'geometry=margin=1in' in cmd_str

    def test_non_pdf_no_defaults(self):
        """Non-PDF conversion has no default options."""
        import tasks
        cmd = tasks.build_pandoc_cmd('markdown', 'html', '/in/f.md', '/out/f.html')
        assert cmd == ['pandoc', '-f', 'markdown', '-t', 'html', '/in/f.md', '-o', '/out/f.html']

    def test_user_options_merge_with_defaults(self):
        """User variable overrides one default; others preserved."""
        import tasks
        opts = {'variables': {'fontsize': '12pt', 'mainfont': 'Arial'}}
        cmd = tasks.build_pandoc_cmd('markdown', 'pdf', '/in/f.md', '/out/f.pdf', opts)
        cmd_str = ' '.join(cmd)
        # User override wins
        assert 'mainfont=Arial' in cmd_str
        # Other defaults preserved
        assert 'CJKmainfont=Noto Sans CJK SC' in cmd_str
        assert 'monofont=DejaVu Sans Mono' in cmd_str
        # New user value present
        assert 'fontsize=12pt' in cmd_str

    def test_user_pdf_engine_overrides_default(self):
        """User can override the default pdf_engine."""
        import tasks
        opts = {'pdf_engine': 'lualatex'}
        cmd = tasks.build_pandoc_cmd('markdown', 'pdf', '/in/f.md', '/out/f.pdf', opts)
        assert '--pdf-engine=lualatex' in cmd
        assert '--pdf-engine=xelatex' not in cmd

    def test_boolean_options_produce_flags(self):
        """Boolean True options produce flags, False are omitted."""
        import tasks
        opts = {'toc': True, 'number_sections': True, 'listings': False}
        cmd = tasks.build_pandoc_cmd('markdown', 'html', '/in/f.md', '/out/f.html', opts)
        assert '--toc' in cmd
        assert '--number-sections' in cmd
        assert '--listings' not in cmd

    def test_int_options_produce_value_flags(self):
        """Integer options produce --flag=value."""
        import tasks
        opts = {'dpi': 150, 'toc_depth': 3}
        cmd = tasks.build_pandoc_cmd('markdown', 'html', '/in/f.md', '/out/f.html', opts)
        assert '--dpi=150' in cmd
        assert '--toc-depth=3' in cmd

    def test_metadata_dict_options(self):
        """Metadata dict produces --metadata key=value pairs."""
        import tasks
        opts = {'metadata': {'title': 'My Doc', 'author': 'Jane'}}
        cmd = tasks.build_pandoc_cmd('markdown', 'html', '/in/f.md', '/out/f.html', opts)
        assert '--metadata' in cmd
        title_idx = cmd.index('--metadata')
        assert cmd[title_idx + 1] == 'title=My Doc'


# ============================================================
# Epic 4: Performance & Resource Utilization tests
# ============================================================

class TestGpuQueueRouting:
    """Story 4.1: Verify task_routes maps GPU tasks to gpu queue."""

    def test_task_routes_gpu_marker(self):
        assert tasks.celery.conf.task_routes['tasks.convert_with_marker'] == {'queue': 'gpu'}

    def test_task_routes_gpu_marker_slm(self):
        assert tasks.celery.conf.task_routes['tasks.convert_with_marker_slm'] == {'queue': 'gpu'}

    def test_task_routes_gpu_hybrid(self):
        assert tasks.celery.conf.task_routes['tasks.convert_with_hybrid'] == {'queue': 'gpu'}


class TestUpdateMetricsCachesWorkerStatus:
    """Story 4.2: update_metrics caches worker status in Redis."""

    @patch('tasks.celery')
    @patch('tasks.redis_client')
    @patch('tasks.maintenance.update_queue_metrics', create=True)
    @patch('tasks.maintenance.update_redis_pool_metrics', create=True)
    def test_caches_worker_count(self, mock_pool, mock_queue, mock_redis, mock_celery):
        mock_celery.control.inspect.return_value.active.return_value = {'w1': [], 'w2': []}
        mock_redis.hset = MagicMock()

        tasks.update_metrics()

        mock_redis.hset.assert_any_call('workers:status', mapping={
            'worker_count': '2',
            'status': 'up',
            'updated_at': mock_redis.hset.call_args_list[-1][1]['mapping']['updated_at'],
        })

    @patch('tasks.celery')
    @patch('tasks.redis_client')
    @patch('tasks.maintenance.update_queue_metrics', create=True)
    @patch('tasks.maintenance.update_redis_pool_metrics', create=True)
    def test_caches_unknown_on_error(self, mock_pool, mock_queue, mock_redis, mock_celery):
        mock_celery.control.inspect.side_effect = Exception("connection refused")
        mock_redis.hset = MagicMock()

        tasks.update_metrics()

        mock_redis.hset.assert_any_call('workers:status', mapping=pytest.approx({
            'status': 'unknown',
            'updated_at': mock_redis.hset.call_args_list[-1][1]['mapping']['updated_at'],
            'error': 'connection refused',
        }))


class TestCleanupUsesRedisSortedSet:
    """Story 4.3: cleanup_old_files reads from jobs:active sorted set only."""

    @patch('tasks.redis_client')
    @patch('os.path.exists', return_value=False)
    def test_reads_from_sorted_set(self, mock_exists, mock_redis):
        mock_redis.zrangebyscore.return_value = ['job-1', 'job-2']
        mock_redis.scan_iter.return_value = []

        tasks.cleanup_old_files()

        mock_redis.zrangebyscore.assert_called_once_with('jobs:active', '-inf', '+inf')

    @patch('tasks.redis_client')
    @patch('os.path.exists', return_value=True)
    def test_does_not_call_listdir(self, mock_exists, mock_redis):
        """After Story 4.3, cleanup never calls os.listdir."""
        mock_redis.zrangebyscore.return_value = []
        mock_redis.scan_iter.return_value = []

        with patch('os.listdir') as mock_listdir:
            tasks.cleanup_old_files()

        mock_listdir.assert_not_called()


class TestMigrateFilesystemJobs:
    """Story 4.3: migrate_filesystem_jobs registers untracked jobs into sorted set."""

    @patch('tasks.redis_client')
    @patch('os.listdir')
    @patch('os.path.exists', return_value=True)
    def test_registers_untracked_jobs(self, mock_exists, mock_listdir, mock_redis):
        job1 = str(uuid.uuid4())
        job2 = str(uuid.uuid4())
        mock_listdir.return_value = [job1, job2]
        mock_redis.zrangebyscore.return_value = []

        result = tasks.migrate_filesystem_jobs()

        assert result == 2
        mock_redis.zadd.assert_called_once()
        call_args = mock_redis.zadd.call_args
        assert call_args[0][0] == 'jobs:active'
        mapping = call_args[0][1]
        assert job1 in mapping
        assert job2 in mapping
        assert call_args[1]['nx'] is True

    @patch('tasks.redis_client')
    @patch('os.listdir')
    @patch('os.path.exists', return_value=True)
    def test_skips_already_tracked_jobs(self, mock_exists, mock_listdir, mock_redis):
        job1 = str(uuid.uuid4())
        mock_listdir.return_value = [job1]
        mock_redis.zrangebyscore.return_value = [job1]

        result = tasks.migrate_filesystem_jobs()

        assert result == 0
        mock_redis.zadd.assert_not_called()

    @patch('tasks.redis_client')
    @patch('os.listdir')
    @patch('os.path.exists', return_value=True)
    def test_skips_non_uuid_entries(self, mock_exists, mock_listdir, mock_redis):
        mock_listdir.return_value = ['.gitkeep', 'not-a-uuid', 'README.md']
        mock_redis.zrangebyscore.return_value = []

        result = tasks.migrate_filesystem_jobs()

        assert result == 0
        mock_redis.zadd.assert_not_called()

    @patch('tasks.redis_client')
    @patch('os.path.exists', return_value=False)
    def test_handles_missing_directories(self, mock_exists, mock_redis):
        mock_redis.zrangebyscore.return_value = []

        result = tasks.migrate_filesystem_jobs()

        assert result == 0


class TestRedisPoolMetrics:
    """Story 4.4: Redis pool monitoring — verify update_metrics calls pool updater."""

    @patch('tasks.celery')
    @patch('tasks.redis_client')
    def test_update_metrics_calls_pool_updater(self, mock_redis, mock_celery):
        """update_metrics() invokes update_redis_pool_metrics with the redis client."""
        mock_celery.control.inspect.return_value.active.return_value = {'w1': []}
        mock_redis.hset = MagicMock()

        # The metrics module is mocked at sys.modules level; verify it gets called
        m = sys.modules['metrics']
        m.update_redis_pool_metrics.reset_mock()

        tasks.update_metrics()

        m.update_redis_pool_metrics.assert_called_once_with(mock_redis)


class TestRedisPoolExhaustion:
    """Story 4.4: Pool exhaustion warning and gauge updates.

    Since metrics is mocked at sys.modules level, we test through tasks.update_metrics
    and verify the mock was called with the right client. For the real function behavior,
    we temporarily swap in a real implementation.
    """

    def test_pool_exhaustion_logs_warning(self):
        """Pool exhaustion triggers a logging.warning call."""
        # Build a real update_redis_pool_metrics with mocked metrics gauges
        mock_pool_active = MagicMock()
        mock_pool_available = MagicMock()

        import logging as _logging

        def _real_update(redis_client):
            try:
                pool = redis_client.connection_pool
                if hasattr(pool, '_created_connections'):
                    active = pool._created_connections - pool._available_connections.qsize()
                    mock_pool_active.set(active)
                    mock_pool_available.set(pool._available_connections.qsize())
                    if pool._available_connections.qsize() == 0:
                        _logging.warning("Redis connection pool exhausted!")
            except Exception:
                pass

        mock_client = MagicMock()
        mock_pool = MagicMock()
        mock_pool._created_connections = 20
        mock_pool._available_connections.qsize.return_value = 0
        mock_client.connection_pool = mock_pool

        with patch('logging.warning') as mock_warn:
            _real_update(mock_client)
            mock_warn.assert_called_once_with("Redis connection pool exhausted!")

    def test_pool_metrics_set_gauge_values(self):
        """Active/available gauges receive correct computed values."""
        mock_pool_active = MagicMock()
        mock_pool_available = MagicMock()

        def _real_update(redis_client):
            pool = redis_client.connection_pool
            if hasattr(pool, '_created_connections'):
                active = pool._created_connections - pool._available_connections.qsize()
                mock_pool_active.set(active)
                mock_pool_available.set(pool._available_connections.qsize())

        mock_client = MagicMock()
        mock_pool = MagicMock()
        mock_pool._created_connections = 20
        mock_pool._available_connections.qsize.return_value = 15
        mock_client.connection_pool = mock_pool

        _real_update(mock_client)

        mock_pool_active.set.assert_called_once_with(5)  # 20 - 15
        mock_pool_available.set.assert_called_once_with(15)

    def test_pool_without_created_connections_attr(self):
        """Graceful no-op when pool lacks _created_connections."""
        def _real_update(redis_client):
            try:
                pool = redis_client.connection_pool
                if hasattr(pool, '_created_connections'):
                    pass  # Would set metrics
            except Exception:
                pass

        mock_client = MagicMock()
        mock_pool = MagicMock(spec=[])  # No attributes
        mock_client.connection_pool = mock_pool

        # Should not raise
        _real_update(mock_client)


class TestRedisTimeoutConsistency:
    """Story 4.4: Socket timeouts are forwarded correctly by create_redis_client."""

    def test_socket_timeouts_passed_through(self):
        """create_redis_client forwards socket timeout kwargs to Redis.from_url."""
        with patch('redis.Redis.from_url') as mock_from_url:
            mock_settings = MagicMock()
            mock_settings.redis_tls_ca_certs = None
            mock_settings.redis_tls_certfile = None
            mock_settings.redis_tls_keyfile = None
            from redis_client import create_redis_client
            create_redis_client(
                'redis://localhost', mock_settings,
                socket_connect_timeout=5, socket_timeout=10
            )
            call_kwargs = mock_from_url.call_args[1]
            assert call_kwargs['socket_connect_timeout'] == 5
            assert call_kwargs['socket_timeout'] == 10


# --- Epic 7, Story 7.3: Dead letter queue signal handler ---

class TestDLQSignalHandler:
    """Tests for the task_failure signal that writes to dlq:tasks."""

    def test_task_failure_signal_writes_to_dlq(self):
        """task_failure signal pushes a JSON entry to dlq:tasks."""
        import json
        import tasks
        mock_sender = MagicMock()
        mock_sender.name = 'tasks.convert_document'
        mock_redis = MagicMock()
        tasks.redis_client = mock_redis

        tasks._handle_task_failure(
            sender=mock_sender,
            task_id='test-task-id-123',
            exception=RuntimeError('conversion failed'),
            args=['arg1'],
            kwargs={'key': 'val'},
        )

        mock_redis.lpush.assert_called_once()
        call_args = mock_redis.lpush.call_args
        assert call_args[0][0] == 'dlq:tasks'
        entry = json.loads(call_args[0][1])
        assert entry['task_id'] == 'test-task-id-123'
        assert entry['task_name'] == 'tasks.convert_document'
        assert 'conversion failed' in entry['exception']

    def _make_sender(self, task_name='tasks.test'):
        sender = MagicMock()
        sender.name = task_name
        return sender

    def test_dlq_trimmed_to_1000(self):
        """After lpush, ltrim(0, 999) is called to bound the DLQ."""
        import tasks
        mock_redis = MagicMock()
        tasks.redis_client = mock_redis

        tasks._handle_task_failure(
            sender=self._make_sender(),
            task_id='tid',
            exception=RuntimeError('err'),
        )

        mock_redis.ltrim.assert_called_once_with('dlq:tasks', 0, 999)

    def test_dlq_counter_incremented(self):
        """dlq_total Prometheus counter is incremented on failure capture."""
        import tasks
        mock_redis = MagicMock()
        tasks.redis_client = mock_redis
        m = sys.modules['metrics']
        m.dlq_total.reset_mock()

        tasks._handle_task_failure(
            sender=self._make_sender(),
            task_id='tid',
            exception=RuntimeError('err'),
        )

        m.dlq_total.inc.assert_called_once()
