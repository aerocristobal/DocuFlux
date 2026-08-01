"""GET /api/captures must not expose one caller's captures to another.

The endpoint used to return the global `capture:all_jobs` index to any unauthenticated
caller, leaking every user's captured document titles and source URLs. It is now scoped
to the caller's identity — X-Client-ID for the browser extension (which posts
cross-origin without credentials and therefore has no Flask session), or the Flask
session for same-origin UI callers.
"""
import time
from unittest.mock import patch

import pytest

from tests.support.fixtures import (  # noqa: F401
    isolated_client as client,
    mock_redis,
    admin_headers,
)

pytestmark = [pytest.mark.security, pytest.mark.capture]


def _capture_meta(filename='Secret Internal Doc'):
    return {
        'status': 'SUCCESS',
        'filename': filename,
        'from': 'capture',
        'to': 'markdown',
        'created_at': str(time.time()),
        'progress': '100',
    }


def _redis_returning(mock_redis, owner_lists, meta):
    """Wire lrange to per-key lists and the pipeline to return `meta` per job id."""
    mock_redis.lrange.side_effect = lambda key, start, end: owner_lists.get(key, [])
    pipe = mock_redis.pipeline.return_value
    pipe.execute.return_value = [meta]
    return mock_redis


class TestCaptureListScoping:

    def test_anonymous_caller_sees_nothing(self, client, mock_redis):
        """No session and no client id: empty list, and the global index is never read."""
        _redis_returning(mock_redis, {'capture:all_jobs': ['job-1']}, _capture_meta())

        response = client.get('/api/captures')

        assert response.status_code == 200
        assert response.get_json() == []
        read_keys = [call.args[0] for call in mock_redis.lrange.call_args_list]
        assert 'capture:all_jobs' not in read_keys, (
            "the global capture index must not be read for an unidentified caller"
        )

    def test_client_sees_only_its_own_captures(self, client, mock_redis):
        _redis_returning(
            mock_redis,
            {'capture:jobs:client:alice': ['job-alice']},
            _capture_meta('Alice Doc'),
        )

        response = client.get('/api/captures', headers={'X-Client-ID': 'alice'})

        assert response.status_code == 200
        body = response.get_json()
        assert [j['id'] for j in body] == ['job-alice']
        assert body[0]['filename'] == 'Alice Doc'

    def test_second_client_captures_are_not_visible(self, client, mock_redis):
        """Bob's captures exist, but Alice must not see them."""
        _redis_returning(
            mock_redis,
            {
                'capture:jobs:client:bob': ['job-bob'],
                'capture:all_jobs': ['job-bob'],
            },
            _capture_meta('Bob Doc'),
        )

        response = client.get('/api/captures', headers={'X-Client-ID': 'alice'})

        assert response.status_code == 200
        assert response.get_json() == []

    def test_unknown_client_id_is_not_an_identity(self, client, mock_redis):
        """The extension defaults X-Client-ID to 'unknown'; that must not pool callers."""
        _redis_returning(
            mock_redis,
            {'capture:jobs:client:unknown': ['job-x'], 'capture:all_jobs': ['job-x']},
            _capture_meta(),
        )

        response = client.get('/api/captures', headers={'X-Client-ID': 'unknown'})

        assert response.get_json() == []

    def test_session_owner_sees_its_captures(self, client, mock_redis):
        """Same-origin UI callers are identified by their Flask session."""
        with client.session_transaction() as sess:
            sess['session_id'] = 'ui-session-1'
        _redis_returning(
            mock_redis,
            {'capture:jobs:session:ui-session-1': ['job-ui']},
            _capture_meta('UI Doc'),
        )

        response = client.get('/api/captures')

        assert [j['id'] for j in response.get_json()] == ['job-ui']

    def test_client_id_takes_precedence_over_session(self, client, mock_redis):
        """A request carrying both is the extension talking; prefer its client id."""
        with client.session_transaction() as sess:
            sess['session_id'] = 'ui-session-1'
        _redis_returning(
            mock_redis,
            {'capture:jobs:client:alice': ['job-alice']},
            _capture_meta(),
        )

        response = client.get('/api/captures', headers={'X-Client-ID': 'alice'})

        assert [j['id'] for j in response.get_json()] == ['job-alice']


class TestCaptureListScopeEscapeHatch:

    def test_global_scope_restores_legacy_behaviour(self, client, mock_redis):
        """CAPTURE_LIST_SCOPE=global is documented for single-user self-hosts."""
        import web.app as web_app
        _redis_returning(mock_redis, {'capture:all_jobs': ['job-1']}, _capture_meta())

        with patch.object(web_app.app_settings, 'capture_list_scope', 'global'):
            response = client.get('/api/captures')

        assert [j['id'] for j in response.get_json()] == ['job-1']


class TestAdminCaptureIndex:

    def test_requires_admin_secret(self, client, mock_redis):
        response = client.get('/api/v1/admin/captures')
        assert response.status_code in (401, 403, 503)

    def test_returns_global_index_with_admin_secret(self, client, mock_redis, admin_headers):
        _redis_returning(mock_redis, {'capture:all_jobs': ['job-any']}, _capture_meta())

        response = client.get('/api/v1/admin/captures', headers=admin_headers)

        assert response.status_code == 200
        assert [j['id'] for j in response.get_json()] == ['job-any']


class TestCaptureOwnershipRecorded:
    """Session creation must write the per-owner index the listing reads."""

    def test_client_id_capture_is_indexed_by_client(self, client, mock_redis):
        client.post(
            '/api/v1/capture/sessions',
            json={'title': 'Doc', 'to_format': 'markdown'},
            headers={'X-Client-ID': 'alice'},
        )
        pushed_keys = [call.args[0] for call in mock_redis.lpush.call_args_list]
        assert 'capture:jobs:client:alice' in pushed_keys

    def test_session_capture_is_indexed_by_session(self, client, mock_redis):
        with client.session_transaction() as sess:
            sess['session_id'] = 'ui-session-1'

        client.post('/api/v1/capture/sessions', json={'title': 'Doc'})

        pushed_keys = [call.args[0] for call in mock_redis.lpush.call_args_list]
        assert 'capture:jobs:session:ui-session-1' in pushed_keys

    def test_anonymous_capture_is_only_in_the_global_index(self, client, mock_redis):
        client.post('/api/v1/capture/sessions', json={'title': 'Doc'})

        pushed_keys = [call.args[0] for call in mock_redis.lpush.call_args_list]
        assert 'capture:all_jobs' in pushed_keys
        assert not any(k.startswith('capture:jobs:') for k in pushed_keys)
