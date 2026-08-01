"""Steps for tests/features/capabilities/security/."""
import time
import uuid
from unittest.mock import patch

from pytest_bdd import given, parsers, then, when

PUBLIC_IP = '93.184.216.34'
PRIVATE_IP = '10.0.0.7'


def _capture_meta(filename='Internal Doc'):
    return {
        'status': 'SUCCESS', 'filename': filename, 'from': 'capture', 'to': 'markdown',
        'created_at': str(time.time()), 'progress': '100',
    }


def _index(mock_redis, ctx, lists, meta=None):
    """Wire lrange to per-key lists and the pipeline to answer one meta per job id.

    Accumulates across Given steps, so a scenario can set up two owners' captures and
    still have both visible. Answering per job rather than with a fixed list matters
    once a caller presenting two identities merges two indexes: a single-element return
    value would silently truncate the result through zip() instead of failing.
    """
    known = ctx.setdefault('capture_lists', {})
    known.update(lists)

    mock_redis.lrange.side_effect = lambda key, start, end: known.get(key, [])
    pipe = mock_redis.pipeline.return_value
    requested = []
    pipe.hgetall.side_effect = lambda key: requested.append(key.split(':', 1)[1])

    def _execute():
        out = [meta or _capture_meta() for _ in requested]
        requested.clear()
        return out

    pipe.execute.side_effect = _execute


# ── capture access control ────────────────────────────────────────────────────


@given('captures exist belonging to another client')
def _other_client(mock_redis, ctx):
    _index(mock_redis, ctx, {'capture:all_jobs': ['job-other'],
                             'capture:jobs:client:someone-else': ['job-other']})
    ctx['expected'] = 'job-other'


@given(parsers.parse('captures exist belonging to client "{client}"'))
def _client_captures(mock_redis, ctx, client):
    _index(mock_redis, ctx, {f'capture:jobs:client:{client}': [f'job-{client}'],
                             'capture:all_jobs': [f'job-{client}']})
    ctx['expected'] = f'job-{client}'


@given('captures exist belonging to my session')
def _session_captures(mock_redis, session_id, ctx):
    _index(mock_redis, ctx, {f'capture:jobs:session:{session_id}': ['job-mine']})
    ctx['expected'] = 'job-mine'


@when('an anonymous caller lists captures')
def _anon_list(bdd_client, ctx):
    ctx['response'] = bdd_client.get('/api/captures')


@when(parsers.parse('client "{client}" lists captures'))
def _client_list(bdd_client, ctx, client):
    ctx['response'] = bdd_client.get('/api/captures', headers={'X-Client-ID': client})


@when('I list captures')
def _i_list(bdd_client, ctx):
    ctx['response'] = bdd_client.get('/api/captures')


@when(parsers.parse('I list captures as client "{client}"'))
def _i_list_as_client(bdd_client, ctx, client):
    """The web UI: a session cookie *and* the extension's client id."""
    ctx['response'] = bdd_client.get('/api/captures', headers={'X-Client-ID': client})


@when('an admin lists all captures')
def _admin_list(bdd_client, admin_headers, ctx):
    ctx['response'] = bdd_client.get('/api/v1/admin/captures', headers=admin_headers)


@when('an anonymous caller lists all captures')
def _anon_admin_list(bdd_client, ctx):
    import web.app as web_app
    with patch.object(web_app.app_settings, 'admin_api_secret', 'test-admin-secret'):
        ctx['response'] = bdd_client.get('/api/v1/admin/captures')


@then('no captures are returned')
def _none_returned(ctx):
    assert ctx['response'].get_json() == []


@then('the captures are returned')
def _some_returned(ctx):
    assert ctx['response'].get_json(), 'expected captures, got none'


@then(parsers.parse('the captures belonging to "{client}" are returned'))
def _client_returned(ctx, client):
    ids = [j['id'] for j in ctx['response'].get_json()]
    assert ids == [f'job-{client}'], ids


@then('the captures belonging to my session are returned')
def _session_returned(ctx):
    ids = [j['id'] for j in ctx['response'].get_json()]
    assert ids == ['job-mine'], ids


@then(parsers.parse('both my session\'s captures and "{client}"\'s are returned'))
def _union_returned(ctx, client):
    ids = sorted(j['id'] for j in ctx['response'].get_json())
    assert ids == sorted(['job-mine', f'job-{client}']), ids


@then('the global capture index is never read')
def _global_untouched(mock_redis):
    read = [c.args[0] for c in mock_redis.lrange.call_args_list]
    assert 'capture:all_jobs' not in read, read


# ── webhook SSRF ──────────────────────────────────────────────────────────────


@given('a job exists to attach a webhook to', target_fixture='job_id')
def _webhook_job(mock_redis, ctx):
    job_id = str(uuid.uuid4())
    mock_redis.exists.return_value = 1
    mock_redis.hgetall.return_value = {'status': 'PROCESSING'}
    ctx['job_id'] = job_id
    return job_id


@given('no such job')
def _no_job(mock_redis, ctx):
    mock_redis.exists.return_value = 0
    mock_redis.hgetall.return_value = {}


@when(parsers.parse('I register the webhook "{url}"'))
def _register(bdd_client, api_headers, ctx, url):
    ctx['response'] = bdd_client.post('/api/v1/webhooks', json={
        'job_id': ctx['job_id'], 'webhook_url': url,
    }, headers=api_headers)


@when('I register a webhook on a public address')
def _register_public(bdd_client, api_headers, ctx):
    with patch('webhook_validation.socket.getaddrinfo',
               return_value=[(2, 1, 0, '', (PUBLIC_IP, 0))]):
        ctx['response'] = bdd_client.post('/api/v1/webhooks', json={
            'job_id': ctx['job_id'], 'webhook_url': 'https://hooks.example.test/x',
        }, headers=api_headers)


@then('the webhook is refused as a bad request')
def _webhook_refused(ctx):
    assert ctx['response'].status_code == 400, ctx['response'].get_data(as_text=True)


@then('the webhook is registered')
def _webhook_ok(ctx):
    assert ctx['response'].status_code in (200, 201), ctx['response'].get_data(as_text=True)


@then('the job is reported as not found')
def _job_missing(ctx):
    assert ctx['response'].status_code == 404


# ── delivery-time re-check ────────────────────────────────────────────────────


@given('a webhook registered for a job', target_fixture='job_id')
def _registered_webhook(mock_redis, ctx):
    job_id = str(uuid.uuid4())
    mock_redis.hget.return_value = 'https://hooks.example.test/x'
    ctx['job_id'] = job_id
    return job_id


@when('the destination now resolves to a private address')
def _now_private(ctx):
    ctx['resolves_to'] = PRIVATE_IP


@when('the job completes')
def _job_completes(mock_redis, ctx):
    from job_metadata import fire_webhook
    with patch('webhook_validation.socket.getaddrinfo',
               return_value=[(2, 1, 0, '', (ctx['resolves_to'], 0))]), \
         patch('requests.post') as post:
        fire_webhook(mock_redis, ctx['job_id'], 'SUCCESS')
    ctx['webhook_post'] = post


@then('nothing is sent to the destination')
def _nothing_sent(ctx):
    ctx['webhook_post'].assert_not_called()
