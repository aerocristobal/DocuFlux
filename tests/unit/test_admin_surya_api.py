"""Tests for the admin Surya lifecycle endpoints (DocuFlux-pfm.5)."""
import pytest
from pydantic import SecretStr

import web.app as web_app
from shared import surya_state as ss

SECRET = 'test-admin-secret'
AUTH = {'Authorization': f'Bearer {SECRET}'}


class FakeRedis:
    def __init__(self):
        self.kv, self.h = {}, {}

    def get(self, k): return self.kv.get(k)
    def set(self, k, v, ex=None): self.kv[k] = v
    def delete(self, k): self.kv.pop(k, None)
    def hgetall(self, k): return dict(self.h.get(k, {}))
    def hget(self, k, f): return self.h.get(k, {}).get(f)
    def hset(self, k, mapping=None, **kw): self.h.setdefault(k, {}).update({a: str(b) for a, b in (mapping or {}).items()})


@pytest.fixture
def r(app):
    fake = FakeRedis()
    web_app.redis_client = fake
    original = web_app.app_settings.admin_api_secret
    web_app.app_settings.admin_api_secret = SecretStr(SECRET)
    yield fake
    web_app.app_settings.admin_api_secret = original


def _gpu(r, vram='24'):
    r.kv[ss.GPU_STATUS_KEY] = 'available'
    r.h[ss.GPU_INFO_KEY] = {'vram_total': vram}


WRITES = [('post', '/api/v1/admin/surya', {'action': 'load'}),
          ('post', '/api/v1/admin/surya', {'action': 'unload'}),
          ('put', '/api/v1/admin/ocr-settings', {'mode': 'cpu_only'})]


@pytest.mark.parametrize('method,url,body', WRITES)
class TestAuth:
    def test_missing_secret_401(self, client, r, method, url, body):
        assert getattr(client, method)(url, json=body).status_code == 401

    def test_malformed_401(self, client, r, method, url, body):
        resp = getattr(client, method)(url, json=body, headers={'Authorization': SECRET})
        assert resp.status_code == 401

    def test_wrong_secret_403_and_no_change(self, client, r, method, url, body):
        _gpu(r)
        resp = getattr(client, method)(url, json=body, headers={'Authorization': 'Bearer nope'})
        assert resp.status_code == 403
        assert r.kv.get(ss.DESIRED_KEY) is None and r.h.get(ss.SETTINGS_KEY) is None

    def test_secret_unset_503(self, client, r, method, url, body):
        web_app.app_settings.admin_api_secret = SecretStr('')
        assert getattr(client, method)(url, json=body, headers=AUTH).status_code == 503


class TestSurya:
    def test_get_is_public(self, client, r):
        r.kv[ss.STATE_KEY] = 'running'
        data = client.get('/api/v1/admin/surya').get_json()
        assert data['state'] == 'running'
        assert data['surya_supported'] is False

    def test_load_pins_and_requests_running(self, client, r):
        _gpu(r)
        resp = client.post('/api/v1/admin/surya', json={'action': 'load'}, headers=AUTH)
        assert resp.status_code == 202
        assert ss.get_desired(r) == 'running' and ss.is_pinned(r)
        assert resp.get_json()['pinned'] is True

    def test_unload_unpins_and_requests_stopped(self, client, r):
        _gpu(r)
        ss.set_desired(r, 'running'); ss.set_pinned(r, True)
        resp = client.post('/api/v1/admin/surya', json={'action': 'unload'}, headers=AUTH)
        assert resp.status_code == 202
        assert ss.get_desired(r) == 'stopped' and not ss.is_pinned(r)

    def test_unload_allowed_when_unsupported(self, client, r):
        assert client.post('/api/v1/admin/surya', json={'action': 'unload'}, headers=AUTH).status_code == 202

    def test_load_rejected_when_unsupported(self, client, r):
        resp = client.post('/api/v1/admin/surya', json={'action': 'load'}, headers=AUTH)
        assert resp.status_code == 409
        assert r.kv.get(ss.DESIRED_KEY) is None and not ss.is_pinned(r)

    def test_load_rejected_in_cpu_only(self, client, r):
        _gpu(r)
        ss.set_ocr_settings(r, mode='cpu_only')
        resp = client.post('/api/v1/admin/surya', json={'action': 'load'}, headers=AUTH)
        assert resp.status_code == 409
        assert not ss.is_pinned(r)

    @pytest.mark.parametrize('body', [{}, {'action': 'reboot'}])
    def test_bad_action_400(self, client, r, body):
        assert client.post('/api/v1/admin/surya', json=body, headers=AUTH).status_code == 400


class TestOcrSettings:
    def test_get_defaults_public(self, client, r):
        data = client.get('/api/v1/admin/ocr-settings').get_json()
        assert data['mode'] == web_app.app_settings.ocr_mode
        assert data['idle_timeout_min'] == web_app.app_settings.surya_idle_timeout_minutes

    def test_put_updates(self, client, r):
        resp = client.put('/api/v1/admin/ocr-settings',
                          json={'mode': 'cpu_only', 'idle_timeout_min': 5}, headers=AUTH)
        assert resp.status_code == 200
        assert resp.get_json() == {'mode': 'cpu_only', 'idle_timeout_min': 5}
        assert client.get('/api/v1/admin/ocr-settings').get_json()['mode'] == 'cpu_only'

    def test_put_partial_keeps_other(self, client, r):
        client.put('/api/v1/admin/ocr-settings', json={'idle_timeout_min': 7}, headers=AUTH)
        resp = client.put('/api/v1/admin/ocr-settings', json={'mode': 'on_demand'}, headers=AUTH)
        assert resp.get_json()['idle_timeout_min'] == 7

    @pytest.mark.parametrize('body', [{'mode': 'gpu'}, {'idle_timeout_min': 0},
                                      {'idle_timeout_min': 'x'}, {'idle_timeout_min': True}])
    def test_put_invalid_400(self, client, r, body):
        assert client.put('/api/v1/admin/ocr-settings', json=body, headers=AUTH).status_code == 400
        assert r.h.get(ss.SETTINGS_KEY) is None

    def test_put_non_object_400(self, client, r):
        assert client.put('/api/v1/admin/ocr-settings', json=[1], headers=AUTH).status_code == 400
