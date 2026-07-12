"""Tests for shared/job_metadata.py's build_job_metadata (Story 6.4b)."""

from job_metadata import build_job_metadata


def test_default_status_and_created_at():
    meta = build_job_metadata('doc.md', 'markdown', 'docx')
    assert meta['status'] == 'PENDING'
    assert meta['filename'] == 'doc.md'
    assert meta['from'] == 'markdown'
    assert meta['to'] == 'docx'
    assert 'created_at' in meta and meta['created_at']
    assert 'progress' not in meta  # only included when explicitly passed


def test_explicit_created_at_and_progress():
    meta = build_job_metadata('doc.pdf', 'pdf', 'markdown', created_at='123.45', progress='0')
    assert meta['created_at'] == '123.45'
    assert meta['progress'] == '0'


def test_custom_status():
    meta = build_job_metadata('capture', 'capture', 'markdown', status='CAPTURING')
    assert meta['status'] == 'CAPTURING'


def test_extra_fields_pass_through():
    meta = build_job_metadata(
        'doc.pdf', 'pdf_marker', 'markdown',
        force_ocr='True', use_llm='False', engine='marker',
    )
    assert meta['force_ocr'] == 'True'
    assert meta['use_llm'] == 'False'
    assert meta['engine'] == 'marker'


def test_extra_fields_do_not_leak_between_calls():
    """Regression guard: **extra must not accidentally share mutable state."""
    meta1 = build_job_metadata('a.md', 'markdown', 'docx', force_ocr='True')
    meta2 = build_job_metadata('b.md', 'markdown', 'docx')
    assert 'force_ocr' in meta1
    assert 'force_ocr' not in meta2
