"""Builders for the data shapes tests keep hand-rolling.

Plain functions, not fixtures — BDD step definitions and characterization tests need to
call these directly, and a fixture cannot be imported and invoked inline.
"""
import time
import uuid

# Story 4.4 deepened validate_file_content_type() to require real PDF structure
# (a trailer and %%EOF), not just the %PDF header. Fixtures that travel through the
# actual upload-validation path need genuinely well-formed bytes.
VALID_PDF_BYTES = (
    b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"
    b"trailer\n<< /Root 1 0 R >>\nstartxref\n0\n%%EOF\n"
)


def new_job_id():
    return str(uuid.uuid4())


def make_job_meta(status='SUCCESS', **overrides):
    """Redis `job:{id}` hash as the worker writes it.

    Values are strings throughout, matching what Redis actually returns — tests that
    build dicts with ints here drift from production behaviour.
    """
    now = time.time()
    meta = {
        'status': status,
        'filename': 'document.md',
        'from': 'markdown',
        'to': 'html',
        'created_at': str(now - 30),
        'progress': '100' if status == 'SUCCESS' else '0',
        'file_count': '1',
        'encrypted': 'false',
    }
    if status in ('SUCCESS', 'FAILURE'):
        meta['completed_at'] = str(now - 5)
    if status == 'FAILURE':
        meta['error'] = 'conversion failed'
        meta['progress'] = '0'
    meta.update({k: str(v) for k, v in overrides.items()})
    return meta


def make_capture_session(session_id=None, status='active', page_count=0, **overrides):
    """Redis `capture:session:{id}` hash as web/routes/capture.py writes it."""
    session = {
        'session_id': session_id or new_job_id(),
        'job_id': new_job_id(),
        'status': status,
        'client_id': 'test-client',
        'created_at': str(time.time()),
        'page_count': str(page_count),
        'to_format': 'markdown',
        'force_ocr': 'false',
        'batches_queued': '0',
        'next_batch_start': '0',
    }
    session.update({k: str(v) for k, v in overrides.items()})
    return session


def make_capture_page(sequence=1, text='Some captured text.', url=None, **overrides):
    """One page payload as the browser extension POSTs it."""
    page = {
        'page_sequence': sequence,
        'text': text,
        'url': url or f'https://example.test/page/{sequence}',
        'title': f'Page {sequence}',
    }
    page.update(overrides)
    return page
