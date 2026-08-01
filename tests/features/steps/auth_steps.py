"""Steps for tests/features/capabilities/auth_and_keys/."""
import uuid
from unittest.mock import patch

from pytest_bdd import parsers, then, when

ADMIN_SECRET = 'test-admin-secret'


def _admin_headers():
    return {'Authorization': f'Bearer {ADMIN_SECRET}'}


def _with_admin_secret():
    """Configure the deployment as having an admin secret set."""
    import web.app as web_app
    return patch.object(web_app.app_settings, 'admin_api_secret', ADMIN_SECRET)


@when('I ask for a new key without admin credentials')
def _no_admin(bdd_client, mock_redis, ctx):
    with _with_admin_secret():
        ctx['response'] = bdd_client.post('/api/v1/auth/keys', json={'label': 'x'})


@when(parsers.parse('I ask for a new key with the header "{header}"'))
def _bad_header(bdd_client, mock_redis, ctx, header):
    with _with_admin_secret():
        ctx['response'] = bdd_client.post('/api/v1/auth/keys', json={'label': 'x'},
                                          headers={'Authorization': header})


@when('I ask for a new key with the wrong admin secret')
def _wrong_secret(bdd_client, mock_redis, ctx):
    with _with_admin_secret():
        ctx['response'] = bdd_client.post('/api/v1/auth/keys', json={'label': 'x'},
                                          headers={'Authorization': 'Bearer wrong'})


@when(parsers.parse('I mint a key labelled "{label}"'))
def _mint(bdd_client, mock_redis, ctx, label):
    with _with_admin_secret():
        ctx['response'] = bdd_client.post('/api/v1/auth/keys', json={'label': label},
                                          headers=_admin_headers())


@when(parsers.parse('I mint a key that expires in {days:d} days'))
def _mint_ttl(bdd_client, mock_redis, ctx, days):
    with _with_admin_secret():
        ctx['response'] = bdd_client.post(
            '/api/v1/auth/keys',
            json={'label': 'x', 'expires_in_days': days},
            headers=_admin_headers(),
        )
    ctx['requested_days'] = days


@when('I revoke a key that does not exist')
def _revoke_missing(bdd_client, mock_redis, ctx):
    mock_redis.delete.return_value = 0
    with _with_admin_secret():
        ctx['response'] = bdd_client.delete(f'/api/v1/auth/keys/dk_{uuid.uuid4().hex}',
                                            headers=_admin_headers())


# ── outcomes ──────────────────────────────────────────────────────────────────


@then('the request is rejected as unauthenticated')
def _unauth(ctx):
    assert ctx['response'].status_code == 401, ctx['response'].get_data(as_text=True)


@then('the request is rejected as forbidden')
def _forbidden_auth(ctx):
    assert ctx['response'].status_code == 403


@then('a key is issued')
def _issued(ctx):
    assert ctx['response'].status_code in (200, 201), ctx['response'].get_data(as_text=True)
    assert ctx['response'].get_json()['api_key']


@then(parsers.parse('the key begins with "{prefix}"'))
def _prefix(ctx, prefix):
    assert ctx['response'].get_json()['api_key'].startswith(prefix)


def _stored_mapping(mock_redis):
    for call in mock_redis.hset.call_args_list:
        mapping = call.kwargs.get('mapping')
        if mapping and 'label' in mapping:
            return mapping
    raise AssertionError('no API key was written to Redis')


@then(parsers.parse('the stored key records the label "{label}"'))
def _stored_label(mock_redis, ctx, label):
    assert _stored_mapping(mock_redis)['label'] == label


@then('the stored key records an expiry')
def _stored_expiry(mock_redis, ctx):
    mapping = _stored_mapping(mock_redis)
    assert mapping.get('expires_at'), mapping


@then(parsers.parse('the stored key expires in {days:d} days'))
def _stored_ttl(mock_redis, ctx, days):
    import time
    mapping = _stored_mapping(mock_redis)
    expected = time.time() + days * 86400
    assert abs(float(mapping['expires_at']) - expected) < 120, mapping


@then('the key is reported as not found')
def _key_missing(ctx):
    assert ctx['response'].status_code == 404
