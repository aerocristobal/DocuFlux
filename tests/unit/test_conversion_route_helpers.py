"""
Isolated unit tests for the validate/enqueue/respond helpers extracted from
web/routes/conversion.py's convert() and api_v1_convert() (Story 6.4a).

Each helper is exercised directly with crafted inputs — no full HTTP
request/response cycle — per the story's DoD ("each helper is called
directly with crafted inputs; can be tested without spinning up the full
request").
"""

import io
import json
import time
from unittest.mock import MagicMock, patch

import pytest
from werkzeug.datastructures import FileStorage

from web.routes.conversion import (
    _validate_convert_request,
    _validate_convert_file,
    _enqueue_convert_job,
    _respond_convert_success,
    _validate_v1_convert_params,
    _resolve_v1_convert_format,
    _enqueue_v1_convert_job,
    _respond_v1_convert_success,
)


@pytest.fixture(autouse=True)
def app_ctx():
    """All these helpers call jsonify(), which needs a Flask app context."""
    from web.app import app
    with app.test_request_context():
        yield


def _fs(content, filename):
    return FileStorage(stream=io.BytesIO(content), filename=filename)


# ── _validate_convert_request ───────────────────────────────────────────

class TestValidateConvertRequest:
    def test_no_files_selected(self):
        error, from_info, to_info = _validate_convert_request([], 'markdown', 'docx')
        assert error[1] == 400
        assert 'No selected file' in error[0].get_json()['error']
        assert from_info is None and to_info is None

    def test_all_files_empty_filename(self):
        empty = _fs(b'', '')
        error, _, _ = _validate_convert_request([empty], 'markdown', 'docx')
        assert error[1] == 400

    def test_missing_format_selection(self):
        f = _fs(b'# hi', 'test.md')
        error, _, _ = _validate_convert_request([f], '', 'docx')
        assert error[1] == 400
        assert 'Missing format selection' in error[0].get_json()['error']

    def test_invalid_format_key(self):
        f = _fs(b'# hi', 'test.md')
        error, _, _ = _validate_convert_request([f], 'not-a-real-format', 'docx')
        assert error[1] == 400
        assert 'Invalid format selection' in error[0].get_json()['error']

    def test_valid_request_returns_format_info(self):
        f = _fs(b'# hi', 'test.md')
        error, from_info, to_info = _validate_convert_request([f], 'markdown', 'docx')
        assert error is None
        assert from_info['key'] == 'markdown'
        assert to_info['key'] == 'docx'


# ── _validate_convert_file ──────────────────────────────────────────────

class TestValidateConvertFile:
    def test_extension_mismatch(self):
        from_info = {'key': 'markdown', 'extension': '.md'}
        f = _fs(b'# hi', 'test.txt')
        error = _validate_convert_file(f, from_info)
        assert error[1] == 400
        assert 'mismatch' in error[0].get_json()['error']

    def test_markdown_accepts_dot_markdown_extension(self):
        from_info = {'key': 'markdown', 'extension': '.md'}
        f = _fs(b'# hi', 'test.markdown')
        error = _validate_convert_file(f, from_info)
        assert error is None

    def test_content_mismatch_rejected(self):
        from_info = {'key': 'pdf', 'extension': '.pdf'}
        f = _fs(b'not actually a pdf', 'test.pdf')
        error = _validate_convert_file(f, from_info)
        assert error[1] == 400

    @patch('web.routes.conversion.validate_file_content_type', return_value=(True, None))
    def test_valid_pdf_passes(self, _mock_val):
        from_info = {'key': 'pdf', 'extension': '.pdf'}
        f = _fs(b'%PDF-1.4 test content', 'test.pdf')
        error = _validate_convert_file(f, from_info)
        assert error is None


# ── _enqueue_convert_job ─────────────────────────────────────────────────

class TestEnqueueConvertJob:
    @patch('web.routes.conversion._app_mod')
    def test_cpu_format_routes_to_convert_document(self, mock_app_mod, tmp_path):
        mock_app_mod.storage.get_local_path.return_value = str(tmp_path / 'upload1')
        mock_app_mod.storage.get_file_size.return_value = 100
        f = _fs(b'# hi', 'test.md')
        to_info = {'key': 'docx', 'extension': '.docx'}

        job_id = _enqueue_convert_job(f, 'markdown', 'docx', to_info, {})

        assert job_id
        args, kwargs = mock_app_mod.celery.send_task.call_args
        assert args[0] == 'tasks.convert_document'
        assert kwargs['queue'] == 'high_priority'  # 100 bytes < 5MB threshold

    @patch('web.routes.conversion._app_mod')
    def test_marker_format_routes_to_gpu_queue(self, mock_app_mod, tmp_path):
        mock_app_mod.storage.get_local_path.return_value = str(tmp_path / 'upload2')
        mock_app_mod.storage.get_file_size.return_value = 100
        f = _fs(b'%PDF-1.4', 'test.pdf')
        to_info = {'key': 'markdown', 'extension': '.md'}

        _enqueue_convert_job(f, 'pdf_marker', 'markdown', to_info, {'force_ocr': 'on'})

        args, kwargs = mock_app_mod.celery.send_task.call_args
        assert args[0] == 'tasks.convert_with_marker'
        assert kwargs['queue'] == 'gpu'
        assert kwargs['args'][-1] == {'force_ocr': True, 'use_llm': False}  # options dict appended

    @patch('web.routes.conversion._app_mod')
    def test_large_file_routes_to_default_queue(self, mock_app_mod, tmp_path):
        mock_app_mod.storage.get_local_path.return_value = str(tmp_path / 'upload3')
        mock_app_mod.storage.get_file_size.return_value = 10 * 1024 * 1024  # 10MB
        f = _fs(b'# hi', 'test.md')
        to_info = {'key': 'docx', 'extension': '.docx'}

        _enqueue_convert_job(f, 'markdown', 'docx', to_info, {})

        _, kwargs = mock_app_mod.celery.send_task.call_args
        assert kwargs['queue'] == 'default'

    @patch('web.routes.conversion._app_mod')
    def test_pdf_ocr_routes_to_convert_with_ocr(self, mock_app_mod, tmp_path):
        mock_app_mod.storage.get_local_path.return_value = str(tmp_path / 'upload4')
        mock_app_mod.storage.get_file_size.return_value = 100
        f = _fs(b'%PDF-1.4', 'test.pdf')
        to_info = {'key': 'markdown', 'extension': '.md'}

        _enqueue_convert_job(f, 'pdf_ocr', 'markdown', to_info, {})

        args, kwargs = mock_app_mod.celery.send_task.call_args
        assert args[0] == 'tasks.convert_with_ocr'
        assert kwargs['queue'] == 'high_priority'


# ── _respond_convert_success ────────────────────────────────────────────

def test_respond_convert_success(app_ctx):
    resp = _respond_convert_success(['job-1', 'job-2'])
    data = resp.get_json()
    assert data == {'job_ids': ['job-1', 'job-2'], 'status': 'queued'}


# ── _validate_v1_convert_params ─────────────────────────────────────────

class TestValidateV1ConvertParams:
    def test_missing_to_format(self):
        error, opts = _validate_v1_convert_params('', 'pandoc', None)
        assert error[1] == 400
        assert opts is None

    def test_unsupported_to_format(self):
        error, _ = _validate_v1_convert_params('not-a-format', 'pandoc', None)
        assert error[1] == 422

    def test_invalid_engine(self):
        error, _ = _validate_v1_convert_params('docx', 'not-an-engine', None)
        assert error[1] == 422
        assert 'Invalid engine' in error[0].get_json()['error']

    def test_malformed_json_pandoc_options(self):
        error, _ = _validate_v1_convert_params('docx', 'pandoc', 'not json{')
        assert error[1] == 400
        assert 'JSON' in error[0].get_json()['error']

    def test_non_dict_pandoc_options(self):
        error, _ = _validate_v1_convert_params('docx', 'pandoc', json.dumps([1, 2, 3]))
        assert error[1] == 400

    def test_pandoc_options_with_non_pandoc_engine(self):
        error, _ = _validate_v1_convert_params('markdown', 'marker', json.dumps({'toc': True}))
        assert error[1] == 422
        assert 'pandoc' in error[0].get_json()['error'].lower()

    def test_invalid_pandoc_option_contents(self):
        error, _ = _validate_v1_convert_params('pdf', 'pandoc', json.dumps({'lua_filter': '/etc/passwd'}))
        assert error[1] == 422
        assert any('Unknown' in d for d in error[0].get_json()['details'])

    def test_valid_request_returns_cleaned_options(self):
        error, opts = _validate_v1_convert_params('pdf', 'pandoc', json.dumps({'toc': True}))
        assert error is None
        assert opts == {'toc': True}

    def test_no_pandoc_options_is_fine(self):
        error, opts = _validate_v1_convert_params('pdf', 'pandoc', None)
        assert error is None
        assert opts is None

    def test_hybrid_engine_accepted(self):
        error, _ = _validate_v1_convert_params('pdf', 'hybrid', None)
        assert error is None

    def test_marker_slm_engine_accepted(self):
        error, _ = _validate_v1_convert_params('pdf', 'marker_slm', None)
        assert error is None

    def test_ocr_engine_accepted(self):
        error, _ = _validate_v1_convert_params('pdf', 'ocr', None)
        assert error is None


# ── _resolve_v1_convert_format ──────────────────────────────────────────

class TestResolveV1ConvertFormat:
    def test_explicit_from_format_passthrough_non_pdf(self):
        error, from_format, internal = _resolve_v1_convert_format('x.md', 'markdown', 'pandoc')
        assert error is None
        assert from_format == 'markdown'
        assert internal == 'markdown'

    def test_auto_detect_from_extension(self):
        error, from_format, internal = _resolve_v1_convert_format('doc.md', '', 'pandoc')
        assert error is None
        assert from_format == 'markdown'

    def test_undetectable_extension_errors(self):
        error, from_format, internal = _resolve_v1_convert_format('file.xyz123', '', 'pandoc')
        assert error[1] == 422
        assert from_format is None and internal is None

    def test_marker_engine_maps_pdf_to_pdf_marker(self):
        _, _, internal = _resolve_v1_convert_format('x.pdf', 'pdf', 'marker')
        assert internal == 'pdf_marker'

    def test_hybrid_engine_maps_pdf_to_pdf_hybrid(self):
        _, _, internal = _resolve_v1_convert_format('x.pdf', 'pdf', 'hybrid')
        assert internal == 'pdf_hybrid'

    def test_marker_slm_engine_maps_pdf_to_pdf_marker_slm(self):
        _, _, internal = _resolve_v1_convert_format('x.pdf', 'pdf', 'marker_slm')
        assert internal == 'pdf_marker_slm'

    def test_ocr_engine_maps_pdf_to_pdf_ocr(self):
        _, _, internal = _resolve_v1_convert_format('x.pdf', 'pdf', 'ocr')
        assert internal == 'pdf_ocr'

    def test_pandoc_engine_leaves_pdf_unmapped(self):
        _, _, internal = _resolve_v1_convert_format('x.pdf', 'pdf', 'pandoc')
        assert internal == 'pdf'


# ── _enqueue_v1_convert_job ──────────────────────────────────────────────

class TestEnqueueV1ConvertJob:
    @patch('web.routes.conversion._app_mod')
    def test_content_type_mismatch_short_circuits(self, mock_app_mod):
        f = _fs(b'not a pdf', 'test.pdf')
        error, job_id = _enqueue_v1_convert_job(
            f, 'pdf', 'markdown', 'pandoc', False, False, True, None, str(time.time())
        )
        assert error[1] == 422
        assert job_id is None
        mock_app_mod.celery.send_task.assert_not_called()

    @patch('web.routes.conversion._app_mod')
    def test_pandoc_path_forwards_pandoc_options_as_kwargs(self, mock_app_mod, tmp_path):
        mock_app_mod.storage.get_local_path.return_value = str(tmp_path / 'upload1')
        mock_app_mod.storage.get_file_size.return_value = 100
        f = _fs(b'# hi', 'test.md')

        error, job_id = _enqueue_v1_convert_job(
            f, 'markdown', 'docx', 'pandoc', False, False, True, {'toc': True}, str(time.time())
        )

        assert error is None
        assert job_id
        _, kwargs = mock_app_mod.celery.send_task.call_args
        assert kwargs['kwargs'] == {'pandoc_options': {'toc': True}}
        assert kwargs['queue'] == 'high_priority'

    @patch('web.routes.conversion.validate_file_content_type', return_value=(True, None))
    @patch('web.routes.conversion._app_mod')
    def test_marker_path_routes_to_gpu_queue_with_options(self, mock_app_mod, _mock_val, tmp_path):
        mock_app_mod.storage.get_local_path.return_value = str(tmp_path / 'upload2')
        mock_app_mod.storage.get_file_size.return_value = 100
        f = _fs(b'%PDF-1.4', 'test.pdf')

        error, job_id = _enqueue_v1_convert_job(
            f, 'pdf_marker', 'markdown', 'marker', True, False, True, None, str(time.time())
        )

        assert error is None
        args, kwargs = mock_app_mod.celery.send_task.call_args
        assert args[0] == 'tasks.convert_with_marker'
        assert kwargs['queue'] == 'gpu'
        assert kwargs['args'][-1] == {'force_ocr': True, 'use_llm': False, 'include_images': True}

    @patch('web.routes.conversion.validate_file_content_type', return_value=(True, None))
    @patch('web.routes.conversion._app_mod')
    def test_include_images_false_passed_through(self, mock_app_mod, _mock_val, tmp_path):
        mock_app_mod.storage.get_local_path.return_value = str(tmp_path / 'upload3')
        mock_app_mod.storage.get_file_size.return_value = 100
        f = _fs(b'%PDF-1.4', 'test.pdf')

        error, job_id = _enqueue_v1_convert_job(
            f, 'pdf_marker', 'markdown', 'marker', False, False, False, None, str(time.time())
        )

        assert error is None
        _, kwargs = mock_app_mod.celery.send_task.call_args
        assert kwargs['args'][-1]['include_images'] is False

    @patch('web.routes.conversion.validate_file_content_type', return_value=(True, None))
    @patch('web.routes.conversion._app_mod')
    def test_pdf_ocr_routes_to_cpu_queue(self, mock_app_mod, _mock_val, tmp_path):
        mock_app_mod.storage.get_local_path.return_value = str(tmp_path / 'upload4')
        mock_app_mod.storage.get_file_size.return_value = 100
        f = _fs(b'%PDF-1.4', 'test.pdf')

        error, job_id = _enqueue_v1_convert_job(
            f, 'pdf_ocr', 'markdown', 'ocr', False, False, True, None, str(time.time())
        )

        assert error is None
        args, kwargs = mock_app_mod.celery.send_task.call_args
        assert args[0] == 'tasks.convert_with_ocr'
        assert kwargs['queue'] == 'high_priority'


# ── _respond_v1_convert_success ─────────────────────────────────────────

def test_respond_v1_convert_success(app_ctx):
    ts = str(time.time())
    body, status = _respond_v1_convert_success('job-abc', ts)
    assert status == 202
    data = body.get_json()
    assert data['job_id'] == 'job-abc'
    assert data['status'] == 'queued'
    assert data['status_url'] == '/api/v1/status/job-abc'
    assert data['created_at'].endswith('Z')
