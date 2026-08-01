"""Shared scaffolding for the characterization tests.

These run the worker tasks against a **real** LocalStorageBackend in a temp directory
rather than patching os.makedirs/os.path.exists. Patching the filesystem makes the tests
agree with whatever the mocks say instead of what the code does, which is the opposite of
what a characterization test is for.
"""
import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def storage(tmp_path):
    """A real LocalStorageBackend rooted in tmp_path, patched onto tasks.storage."""
    import tasks
    from storage import LocalStorageBackend

    upload = tmp_path / 'uploads'
    output = tmp_path / 'outputs'
    upload.mkdir()
    output.mkdir()
    backend = LocalStorageBackend(upload_folder=str(upload), output_folder=str(output))
    with patch.object(tasks, 'storage', backend):
        yield backend


@pytest.fixture
def redis():
    """MagicMock Redis with falsy defaults, patched onto tasks.redis_client."""
    import tasks
    mock = MagicMock()
    mock.hgetall.return_value = {}
    mock.lrange.return_value = []
    mock.hget.return_value = None
    mock.get.return_value = None
    with patch.object(tasks, 'redis_client', mock):
        yield mock


@pytest.fixture
def job_meta():
    """Records every update_job_metadata call as {job_id: merged dict}, plus the sequence."""
    import tasks

    recorded = {}
    sequence = []

    def _record(job_id, updates):
        recorded.setdefault(job_id, {}).update(updates)
        sequence.append((job_id, dict(updates)))

    with patch.object(tasks, 'update_job_metadata', side_effect=_record) as m:
        m.merged = recorded
        m.sequence = sequence
        yield m


def make_page(page_hint=0, text='# Chapter', images=None):
    """One page as the extension stored it in Redis (a JSON string per list entry)."""
    return json.dumps({
        'page_hint': page_hint,
        'text': text,
        'images': images or [],
    })


def make_session_meta(**overrides):
    meta = {
        'title': 'Test Book',
        'to_format': 'markdown',
        'source_url': 'https://example.test/book',
        'force_ocr': 'false',
        'batches_queued': '0',
        'batches_done': '0',
        'batches_failed': '0',
    }
    meta.update({k: str(v) for k, v in overrides.items()})
    return meta


def png_b64(prefixed=True):
    """A tiny valid PNG as base64, optionally with a data-URL prefix."""
    import base64
    raw = base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='
    )
    encoded = base64.b64encode(raw).decode()
    return f'data:image/png;base64,{encoded}' if prefixed else encoded
