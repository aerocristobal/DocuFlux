"""Tests for web.validation — Epic 8: Strengthen Input Validation."""

import io
import pytest
from unittest.mock import patch
from web.validation import (
    sanitize_string,
    validate_file_content_type,
    validate_job_id,
    require_valid_uuid,
)


@pytest.fixture
def api_headers():
    """Provide a valid API key header by patching _validate_api_key."""
    with patch('web.app._validate_api_key', return_value={'created_at': '1700000000.0', 'label': 'test'}):
        yield {'X-API-Key': 'dk_testkey'}


# ── sanitize_string ──────────────────────────────────────────────────────────

class TestSanitizeString:
    def test_removes_null_bytes(self):
        assert sanitize_string("hello\x00world") == "helloworld"

    def test_truncates_to_max_length(self):
        result = sanitize_string("a" * 200, max_length=50)
        assert len(result) == 50

    def test_strips_whitespace(self):
        assert sanitize_string("  hello  ") == "hello"

    def test_removes_newlines_by_default(self):
        assert sanitize_string("line1\nline2\rline3") == "line1 line2 line3"

    def test_allows_newlines_when_requested(self):
        result = sanitize_string("line1\nline2", allow_newlines=True)
        assert "line1\nline2" == result

    def test_returns_empty_for_none(self):
        assert sanitize_string(None) == ""

    def test_returns_empty_for_empty_string(self):
        assert sanitize_string("") == ""

    def test_converts_non_string(self):
        assert sanitize_string(12345) == "12345"


# ── validate_job_id ──────────────────────────────────────────────────────────

class TestValidateJobId:
    def test_accepts_valid_uuid(self):
        is_valid, error = validate_job_id("123e4567-e89b-12d3-a456-426614174000")
        assert is_valid is True
        assert error is None

    def test_rejects_invalid_uuid(self):
        is_valid, error = validate_job_id("not-a-uuid")
        assert is_valid is False
        assert error is not None

    def test_rejects_empty(self):
        is_valid, error = validate_job_id("")
        assert is_valid is False

    def test_rejects_none(self):
        is_valid, error = validate_job_id(None)
        assert is_valid is False


# ── validate_file_content_type ───────────────────────────────────────────────

def _make_file(content: bytes):
    """Create a Werkzeug-like file object from bytes."""
    return io.BytesIO(content)


class TestValidateFileContentType:
    def test_pdf_valid(self):
        f = _make_file(b'%PDF-1.4 rest of content')
        is_valid, error = validate_file_content_type(f, '.pdf')
        assert is_valid is True
        assert error is None

    def test_pdf_invalid(self):
        f = _make_file(b'PK\x03\x04 not a pdf')
        is_valid, error = validate_file_content_type(f, '.pdf')
        assert is_valid is False
        assert '%PDF' in error

    def test_docx_valid(self):
        f = _make_file(b'PK\x03\x04 docx content')
        is_valid, error = validate_file_content_type(f, '.docx')
        assert is_valid is True

    def test_docx_invalid(self):
        f = _make_file(b'%PDF-1.4 not a docx')
        is_valid, error = validate_file_content_type(f, '.docx')
        assert is_valid is False
        assert 'ZIP/PK' in error

    def test_odt_valid(self):
        f = _make_file(b'PK\x03\x04 odt content')
        is_valid, error = validate_file_content_type(f, '.odt')
        assert is_valid is True

    def test_epub_valid(self):
        f = _make_file(b'PK\x03\x04 epub content')
        is_valid, error = validate_file_content_type(f, '.epub')
        assert is_valid is True

    def test_text_valid_utf8(self):
        f = _make_file('Hello world'.encode('utf-8'))
        is_valid, error = validate_file_content_type(f, '.md')
        assert is_valid is True

    def test_text_valid_latin1(self):
        f = _make_file(b'\xe9\xe8\xea latin chars')
        is_valid, error = validate_file_content_type(f, '.txt')
        assert is_valid is True  # latin-1 fallback succeeds

    def test_empty_file(self):
        f = _make_file(b'')
        is_valid, error = validate_file_content_type(f, '.pdf')
        assert is_valid is False
        assert 'empty' in error.lower()

    def test_unknown_extension_passes(self):
        f = _make_file(b'\x00\x01\x02\x03 binary stuff')
        is_valid, error = validate_file_content_type(f, '.xyz')
        assert is_valid is True

    def test_preserves_stream_position(self):
        f = _make_file(b'%PDF-1.4 content here')
        f.seek(5)
        validate_file_content_type(f, '.pdf')
        assert f.tell() == 5


# ── require_valid_uuid decorator (integration via Flask test client) ─────────

class TestRequireValidUuidDecorator:
    def test_rejects_invalid_uuid(self, client):
        resp = client.get('/api/v1/status/not-a-uuid')
        assert resp.status_code == 400

    def test_accepts_valid_uuid(self, client):
        # Valid UUID but job won't exist — should get 404, not 400
        resp = client.get('/api/v1/status/123e4567-e89b-12d3-a456-426614174000')
        assert resp.status_code != 400


# ── Magic bytes integration in upload routes ─────────────────────────────────

class TestUploadMagicBytesValidation:
    def test_api_v1_convert_rejects_pdf_with_wrong_magic_bytes(self, client, api_headers):
        data = {
            'to_format': 'markdown',
            'file': (io.BytesIO(b'PK\x03\x04 not a pdf'), 'test.pdf'),
        }
        resp = client.post(
            '/api/v1/convert',
            data=data,
            content_type='multipart/form-data',
            headers=api_headers,
        )
        assert resp.status_code in (400, 422)
        assert b'%PDF' in resp.data

    def test_api_v1_convert_accepts_pdf_with_correct_magic_bytes(self, client, api_headers):
        data = {
            'to_format': 'markdown',
            'from_format': 'pdf',
            'file': (io.BytesIO(b'%PDF-1.4 fake pdf content'), 'test.pdf'),
        }
        resp = client.post(
            '/api/v1/convert',
            data=data,
            content_type='multipart/form-data',
            headers=api_headers,
        )
        # Should pass magic bytes check — may fail later for other reasons
        # but should NOT be 422 for magic bytes
        if resp.status_code in (400, 422):
            assert b'%PDF' not in resp.data
