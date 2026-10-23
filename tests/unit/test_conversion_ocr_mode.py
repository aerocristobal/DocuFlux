"""OCR mode enforcement on the conversion routes (DocuFlux-pfm.6)."""
import io
import os
from unittest.mock import patch

import pytest

import web.app as web_app
from shared import surya_state as ss
from tests.support.factories import VALID_PDF_BYTES

AI_FORMATS = ['pdf_marker', 'pdf_hybrid', 'pdf_marker_slm']
V1_ENGINE = {'pdf_marker': 'marker', 'pdf_hybrid': 'hybrid', 'pdf_marker_slm': 'marker_slm'}
ENDPOINTS = ['convert', 'retry', 'v1']


class FakeRedis:
    """Minimal in-memory redis covering surya_state and the conversion routes."""

    def __init__(self):
        self.kv, self.h, self.jobs = {}, {}, {}

    def get(self, k): return self.kv.get(k)
    def set(self, k, v, ex=None): self.kv[k] = v
    def delete(self, k): self.kv.pop(k, None)
    def hgetall(self, k): return dict(self.h.get(k, {}))
    def hget(self, k, f): return self.h.get(k, {}).get(f)

    def hset(self, k, key=None, value=None, mapping=None, **kw):
        d = self.h.setdefault(k, {})
        if mapping:
            d.update({a: str(b) for a, b in mapping.items()})
        if key is not None:
            d[key] = str(value)

    def zadd(self, k, mapping): self.jobs.update(mapping)
    def lpush(self, *a, **k): pass
    def expire(self, *a, **k): pass


@pytest.fixture
def r(app, monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(web_app, 'redis_client', fake)
    monkeypatch.setattr(web_app, 'update_job_metadata',
                        lambda job_id, meta: fake.hset(f'job:{job_id}', mapping=meta))
    with patch('web.app.check_disk_space', return_value=True), \
            patch('web.app._validate_api_key',
                  return_value={'created_at': '1700000000.0', 'label': 'test'}):
        yield fake


def _set_mode(r, mode):
    r.h[ss.SETTINGS_KEY] = {'mode': mode}


def _upload_count():
    return sum(len(files) + len(dirs) for _, dirs, files in os.walk(web_app.UPLOAD_FOLDER))


def _submit(client, r, endpoint, from_format, filename='doc.pdf', data=VALID_PDF_BYTES):
    """Hit one of the three enqueue sites for from_format."""
    if endpoint == 'convert':
        return client.post('/convert', data={
            'file': (io.BytesIO(data), filename),
            'from_format': from_format, 'to_format': 'markdown',
        }, content_type='multipart/form-data')
    if endpoint == 'v1':
        if from_format in V1_ENGINE:
            form = {'engine': V1_ENGINE[from_format]}
        elif from_format == 'pdf_ocr':
            form = {'engine': 'ocr'}
        else:
            form = {'engine': 'pandoc'}
        return client.post('/api/v1/convert', data={
            'file': (io.BytesIO(data), filename), 'to_format': 'markdown', **form,
        }, content_type='multipart/form-data', headers={'X-API-Key': 'dk_testkey'})
    # retry: seed an existing job + its upload
    job_id = '11111111-1111-4111-8111-111111111111'
    r.h[f'job:{job_id}'] = {'filename': filename, 'from': from_format, 'to': 'markdown',
                            'force_ocr': 'False', 'use_llm': 'False'}
    web_app.storage.makedirs(job_id, folder='upload')
    web_app.storage.save_file(job_id, filename, data, folder='upload')
    before = _upload_count()
    resp = client.post(f'/api/retry/{job_id}')
    resp.upload_delta = _upload_count() - before
    return resp


def _sent_queues():
    return [c.kwargs.get('queue') for c in web_app.celery.send_task.call_args_list]


@pytest.mark.parametrize('endpoint', ENDPOINTS)
@pytest.mark.parametrize('fmt', AI_FORMATS)
class TestCpuOnly:
    def test_rejected_409_with_no_side_effects(self, client, r, endpoint, fmt):
        _set_mode(r, 'cpu_only')
        before = _upload_count()
        resp = _submit(client, r, endpoint, fmt)
        assert resp.status_code == 409
        assert 'cpu_only' in resp.get_json()['error']
        web_app.celery.send_task.assert_not_called()
        assert r.jobs == {}
        assert r.get(ss.DESIRED_KEY) is None
        if endpoint == 'retry':
            assert resp.upload_delta == 0
        else:
            assert _upload_count() == before


@pytest.mark.parametrize('endpoint', ENDPOINTS)
@pytest.mark.parametrize('fmt', AI_FORMATS)
class TestOnDemand:
    def test_requests_load_and_enqueues_to_gpu(self, client, r, endpoint, fmt):
        _set_mode(r, 'on_demand')
        resp = _submit(client, r, endpoint, fmt)
        assert resp.status_code in (200, 202), resp.get_data(as_text=True)
        assert r.get(ss.DESIRED_KEY) == 'running'
        assert _sent_queues() == ['gpu']

    def test_request_restarts_idle_clock(self, client, r, endpoint, fmt):
        _set_mode(r, 'on_demand')
        assert ss.get_last_used(r) is None
        resp = _submit(client, r, endpoint, fmt)
        assert resp.status_code in (200, 202), resp.get_data(as_text=True)
        assert ss.get_last_used(r) is not None


@pytest.mark.parametrize('endpoint', ENDPOINTS)
class TestNonAiFormatsUnaffected:
    @pytest.mark.parametrize('mode', ['cpu_only', 'on_demand'])
    def test_pdf_ocr_not_blocked_and_no_load_requested(self, client, r, endpoint, mode):
        _set_mode(r, mode)
        resp = _submit(client, r, endpoint, 'pdf_ocr')
        assert resp.status_code in (200, 202), resp.get_data(as_text=True)
        assert r.get(ss.DESIRED_KEY) is None
        assert 'gpu' not in _sent_queues()

    @pytest.mark.parametrize('mode', ['cpu_only', 'on_demand'])
    def test_pandoc_not_blocked_and_no_load_requested(self, client, r, endpoint, mode):
        _set_mode(r, mode)
        resp = _submit(client, r, endpoint, 'markdown', filename='a.md', data=b'# hi')
        assert resp.status_code in (200, 202), resp.get_data(as_text=True)
        assert r.get(ss.DESIRED_KEY) is None
        assert 'gpu' not in _sent_queues()
