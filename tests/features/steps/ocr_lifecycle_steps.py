"""Steps for tests/features/capabilities/ocr_lifecycle/ (those not tagged @pending)."""
from unittest.mock import patch

import pytest
from pydantic import SecretStr
from pytest_bdd import given, parsers, then, when

ADMIN_SECRET = 'test-admin-secret'


class _FakeRedis:
    """Just enough Redis for shared.surya_state."""

    def __init__(self):
        self.kv, self.h = {}, {}

    def get(self, k): return self.kv.get(k)
    def set(self, k, v, ex=None): self.kv[k] = v
    def delete(self, k): self.kv.pop(k, None)
    def hgetall(self, k): return dict(self.h.get(k, {}))
    def hget(self, k, f): return self.h.get(k, {}).get(f)
    def hset(self, k, mapping=None, **kw): self.h.setdefault(k, {}).update({a: str(b) for a, b in (mapping or {}).items()})


@pytest.fixture
def surya_redis(isolated_client):
    """Fake Redis behind web.app, with an admin secret configured."""
    import web.app as web_app
    fake = _FakeRedis()
    with patch.object(web_app, 'redis_client', fake), \
            patch.object(web_app.app_settings, 'admin_api_secret', SecretStr(ADMIN_SECRET)), \
            patch('web.app.check_disk_space', return_value=True):
        yield fake


@given(parsers.parse('an NVIDIA GPU with {gb:d} GB of memory is available'))
def _gpu(surya_redis, gb):
    from shared import surya_state
    surya_redis.kv[surya_state.GPU_STATUS_KEY] = 'available'
    surya_redis.h[surya_state.GPU_INFO_KEY] = {'vram_total': str(gb)}


@given('no GPU is available')
def _no_gpu(surya_redis):
    from shared import surya_state
    surya_redis.kv[surya_state.GPU_STATUS_KEY] = 'unavailable'


@given('I am signed in as an admin')
def _admin():
    """The admin secret is sent explicitly by the steps that need it."""


@given('the AI engine is running')
def _engine_running(surya_redis):
    from shared import surya_state
    surya_state.set_state(surya_redis, 'running')


@when('I open the OCR settings')
def _open_settings(bdd_client, surya_redis, ctx):
    ctx['status'] = bdd_client.get('/api/status/services').get_json()
    ctx['page'] = bdd_client.get('/').data.decode()


@when('I try to start the AI engine')
def _start_engine(bdd_client, surya_redis, ctx):
    ctx['response'] = bdd_client.post(
        '/api/v1/admin/surya', json={'action': 'load'},
        headers={'Authorization': f'Bearer {ADMIN_SECRET}'})


@then('the AI engine controls are shown')
def _controls_shown(ctx):
    assert ctx['status']['surya_supported'] is True


@then('the AI engine controls are hidden')
def _controls_hidden(ctx):
    assert ctx['status']['surya_supported'] is False


@then('I am told a GPU with at least 16 GB is needed')
def _hint(ctx):
    assert 'at least 16 GB' in ctx['page']


@then('I see that the AI engine is running')
def _sees_running(ctx):
    assert ctx['status']['surya_state'] == 'running'


@then('the request is refused')
def _refused(ctx):
    assert ctx['response'].status_code == 409


@then('the reason says no compatible GPU was found')
def _reason(ctx):
    assert 'no GPU' in ctx['response'].get_json()['error']
