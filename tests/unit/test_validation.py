"""Tests for web.validation — Epic 8: Strengthen Input Validation."""

import io
import zipfile
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


def _make_pdf(body: bytes = b'1 0 obj\n<< /Type /Catalog >>\nendobj\n') -> bytes:
    """A structurally real, minimal PDF: header, one object, trailer, %%EOF."""
    return (
        b'%PDF-1.4\n' + body +
        b'trailer\n<< /Root 1 0 R >>\nstartxref\n0\n%%EOF\n'
    )


def _make_zip(entries: dict) -> bytes:
    """A real ZIP archive (genuine EOCD record) with the given entries."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _make_docx() -> bytes:
    return _make_zip({
        '[Content_Types].xml': '<Types/>',
        'word/document.xml': '<w:document/>',
    })


def _make_odt() -> bytes:
    return _make_zip({
        'mimetype': 'application/vnd.oasis.opendocument.text',
        'content.xml': '<office:document-content/>',
    })


def _make_epub() -> bytes:
    return _make_zip({
        'mimetype': 'application/epub+zip',
        'META-INF/container.xml': '<container/>',
    })


def _make_pdf_zip_polyglot() -> bytes:
    """A real PDF (with %%EOF) that also carries a genuine ZIP archive
    appended after it — a classic PDF/ZIP polyglot. A header-only check
    sees a valid PDF; the ZIP's End-Of-Central-Directory record is real."""
    return _make_pdf() + _make_zip({'payload.txt': 'hidden'})


class TestValidateFileContentType:
    def test_pdf_valid(self):
        f = _make_file(_make_pdf())
        is_valid, error = validate_file_content_type(f, '.pdf')
        assert is_valid is True
        assert error is None

    def test_pdf_invalid(self):
        f = _make_file(b'PK\x03\x04 not a pdf')
        is_valid, error = validate_file_content_type(f, '.pdf')
        assert is_valid is False
        assert '%PDF' in error

    def test_docx_valid(self):
        f = _make_file(_make_docx())
        is_valid, error = validate_file_content_type(f, '.docx')
        assert is_valid is True
        assert error is None

    def test_docx_invalid(self):
        f = _make_file(_make_pdf())
        is_valid, error = validate_file_content_type(f, '.docx')
        assert is_valid is False
        assert 'ZIP/PK' in error

    def test_odt_valid(self):
        f = _make_file(_make_odt())
        is_valid, error = validate_file_content_type(f, '.odt')
        assert is_valid is True
        assert error is None

    def test_epub_valid(self):
        f = _make_file(_make_epub())
        is_valid, error = validate_file_content_type(f, '.epub')
        assert is_valid is True
        assert error is None

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
        f = _make_file(_make_pdf() + b'padding')
        f.seek(5)
        validate_file_content_type(f, '.pdf')
        assert f.tell() == 5

    # ── Story 4.4: deep validation beyond 8 magic bytes ─────────────────────

    def test_pdf_zip_polyglot_rejected(self):
        """Scenario: ZIP-PDF polyglot uploaded as .pdf is rejected (error path)."""
        f = _make_file(_make_pdf_zip_polyglot())
        is_valid, error = validate_file_content_type(f, '.pdf')
        assert is_valid is False
        assert 'polyglot' in error.lower() or 'mismatch' in error.lower()

    def test_pdf_with_coincidental_eocd_bytes_in_body_not_rejected(self):
        """A legitimate, large PDF whose binary body happens to contain the
        4-byte ZIP EOCD signature (PK\\x05\\x06) well before the file's
        trailing region — e.g. inside an embedded compressed stream — is not
        a real PDF/ZIP polyglot and must not be rejected. Only an EOCD within
        the trailing ~64KB (where ZIP readers actually look for it) indicates
        a genuine polyglot; the signature must appear far enough from EOF
        that it falls outside that window, which requires the file to exceed
        the window size."""
        body = (
            b'stream\n' + (b'x' * 200) + b'PK\x05\x06' + (b'y' * 200) + b'\nendstream\n'
            + b'z' * 70000  # pushes the EOCD bytes outside the trailing 65557-byte window
        )
        f = _make_file(_make_pdf(body))
        is_valid, error = validate_file_content_type(f, '.pdf')
        assert is_valid is True, f"expected valid PDF, got error: {error}"

    def test_docx_bytes_declared_as_pdf_rejected(self):
        """Scenario: extension/content-type mismatch is rejected (alternative error)."""
        f = _make_file(_make_docx())
        is_valid, error = validate_file_content_type(f, '.pdf')
        assert is_valid is False
        assert '%PDF' in error

    def test_truncated_pdf_missing_eof_rejected(self):
        """Scenario: structurally corrupt file of the right type is rejected (boundary)."""
        truncated = b'%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n'  # no trailer/%%EOF
        f = _make_file(truncated)
        is_valid, error = validate_file_content_type(f, '.pdf')
        assert is_valid is False
        assert 'corrupt' in error.lower() or 'truncated' in error.lower() or '%%EOF' in error

    def test_corrupt_zip_declared_as_docx_rejected(self):
        """A .docx-declared file with a PK header but no real ZIP structure
        (no End-Of-Central-Directory record) is rejected as corrupt, not
        waved through on the header alone."""
        f = _make_file(b'PK\x03\x04 this is not actually a real zip archive')
        is_valid, error = validate_file_content_type(f, '.docx')
        assert is_valid is False
        assert 'corrupt' in error.lower() or 'unparseable' in error.lower()

    def test_plain_zip_declared_as_docx_rejected(self):
        """A genuinely valid ZIP that lacks word/document.xml can't pass as
        a .docx just because it opens successfully."""
        f = _make_file(_make_zip({'hello.txt': 'just a plain zip'}))
        is_valid, error = validate_file_content_type(f, '.docx')
        assert is_valid is False
        assert 'mismatch' in error.lower()

    def test_odt_bytes_declared_as_epub_rejected(self):
        """ODT and EPUB are both ZIP-based; the wrong marker entry must
        still be caught even though both pass the shallow PK header check."""
        f = _make_file(_make_odt())
        is_valid, error = validate_file_content_type(f, '.epub')
        assert is_valid is False
        assert 'mismatch' in error.lower()


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
