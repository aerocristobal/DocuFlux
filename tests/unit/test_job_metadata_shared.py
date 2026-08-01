"""Behavioural tests for shared/job_metadata.py.

`tests/unit/test_job_metadata_builder.py` covers build_job_metadata's basics. This adds
the read/write/emit helpers and `fire_webhook` — in particular the defence-in-depth SSRF
re-check at delivery time, which nothing asserted, and which was silently broken in the
worker container until validate_webhook_url moved into shared/.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from job_metadata import (
    build_job_metadata,
    fire_webhook,
    get_job_metadata,
    update_job_metadata,
)

pytestmark = pytest.mark.unit


class TestBuildJobMetadata:

    def test_progress_is_omitted_when_none(self):
        """An absent progress key differs from '0' to the UI's progress bar."""
        meta = build_job_metadata('f.md', 'markdown', 'html')
        assert 'progress' not in meta

    def test_progress_is_included_when_given(self):
        meta = build_job_metadata('f.md', 'markdown', 'html', progress='0')
        assert meta['progress'] == '0'

    def test_extra_fields_pass_through(self):
        meta = build_job_metadata('f.md', 'markdown', 'html',
                                  engine='marker', session_id='abc')
        assert meta['engine'] == 'marker'
        assert meta['session_id'] == 'abc'

    def test_extra_can_override_the_from_and_to_keys(self):
        """The dict keys are 'from'/'to' but the parameters are from_format/to_format,
        so those two names fall through to **extra and win via metadata.update()."""
        meta = build_job_metadata('f.md', 'markdown', 'html', **{'from': 'capture'})
        assert meta['from'] == 'capture'
        assert meta['to'] == 'html'

    def test_created_at_defaults_to_a_string_timestamp(self):
        meta = build_job_metadata('f.md', 'markdown', 'html')
        assert isinstance(meta['created_at'], str)
        assert float(meta['created_at']) > 0


class TestUpdateJobMetadata:

    def test_writes_the_hash_and_emits(self):
        redis, socketio = MagicMock(), MagicMock()
        update_job_metadata(redis, socketio, 'job-1', {'status': 'SUCCESS'})

        redis.hset.assert_called_once_with('job:job-1', mapping={'status': 'SUCCESS'})
        socketio.emit.assert_called_once()
        event, payload = socketio.emit.call_args[0]
        assert event == 'job_update'
        assert payload == {'id': 'job-1', 'status': 'SUCCESS'}

    def test_emit_failure_does_not_propagate(self):
        """A dead WebSocket must never fail the job that was already written."""
        redis, socketio = MagicMock(), MagicMock()
        socketio.emit.side_effect = RuntimeError('socket gone')

        update_job_metadata(redis, socketio, 'job-1', {'status': 'SUCCESS'})

        redis.hset.assert_called_once()

    def test_no_socketio_is_tolerated(self):
        redis = MagicMock()
        update_job_metadata(redis, None, 'job-1', {'status': 'SUCCESS'})
        redis.hset.assert_called_once()

    def test_redis_failure_propagates(self):
        """Losing the write is a real failure; unlike the emit, it must surface."""
        redis = MagicMock()
        redis.hset.side_effect = ConnectionError('redis down')
        with pytest.raises(ConnectionError):
            update_job_metadata(redis, None, 'job-1', {'status': 'SUCCESS'})


class TestGetJobMetadata:

    def test_returns_none_for_a_missing_job(self):
        redis = MagicMock()
        redis.hgetall.return_value = {}
        assert get_job_metadata(redis, 'nope') is None

    def test_decodes_byte_keys_and_values(self):
        """A client without decode_responses returns bytes; callers expect str."""
        redis = MagicMock()
        redis.hgetall.return_value = {b'status': b'SUCCESS', b'progress': b'100'}
        assert get_job_metadata(redis, 'job-1') == {'status': 'SUCCESS', 'progress': '100'}

    def test_passes_through_already_decoded_values(self):
        redis = MagicMock()
        redis.hgetall.return_value = {'status': 'SUCCESS'}
        assert get_job_metadata(redis, 'job-1') == {'status': 'SUCCESS'}

    def test_returns_none_when_redis_raises(self):
        """Status polling should degrade to 'unknown job', not 500."""
        redis = MagicMock()
        redis.hgetall.side_effect = ConnectionError('redis down')
        assert get_job_metadata(redis, 'job-1') is None


class TestFireWebhook:

    def _redis_with_url(self, url):
        redis = MagicMock()
        redis.hget.return_value = url
        return redis

    def test_no_op_when_no_url_registered(self):
        redis = self._redis_with_url(None)
        with patch('requests.post') as post:
            fire_webhook(redis, 'job-1', 'SUCCESS')
        post.assert_not_called()

    def test_posts_the_expected_payload(self):
        redis = self._redis_with_url('https://hooks.example.test/x')
        with patch('webhook_validation.validate_webhook_url', return_value=(True, None)), \
             patch('requests.post') as post:
            fire_webhook(redis, 'job-1', 'SUCCESS')

        post.assert_called_once()
        payload = post.call_args[1]['json']
        assert payload['job_id'] == 'job-1'
        assert payload['status'] == 'SUCCESS'
        assert 'timestamp' in payload
        assert post.call_args[1]['timeout'] == 5

    def test_extra_fields_are_merged_into_the_payload(self):
        redis = self._redis_with_url('https://hooks.example.test/x')
        with patch('webhook_validation.validate_webhook_url', return_value=(True, None)), \
             patch('requests.post') as post:
            fire_webhook(redis, 'job-1', 'SUCCESS', extra={'quality_grade': 'good'})

        assert post.call_args[1]['json']['quality_grade'] == 'good'

    def test_byte_url_is_decoded(self):
        redis = self._redis_with_url(b'https://hooks.example.test/x')
        with patch('webhook_validation.validate_webhook_url', return_value=(True, None)) as check, \
             patch('requests.post'):
            fire_webhook(redis, 'job-1', 'SUCCESS')
        check.assert_called_once_with('https://hooks.example.test/x')

    def test_ssrf_recheck_blocks_delivery(self):
        """A host that resolved publicly at registration may now resolve privately."""
        redis = self._redis_with_url('http://169.254.169.254/latest/meta-data')
        with patch('webhook_validation.validate_webhook_url',
                   return_value=(False, 'resolves to a private/reserved IP')), \
             patch('requests.post') as post:
            fire_webhook(redis, 'job-1', 'SUCCESS')

        post.assert_not_called()

    def test_delivery_failure_is_swallowed(self):
        """A webhook is best-effort; its failure must not fail the conversion."""
        redis = self._redis_with_url('https://hooks.example.test/x')
        with patch('webhook_validation.validate_webhook_url', return_value=(True, None)), \
             patch('requests.post', side_effect=OSError('connection refused')):
            fire_webhook(redis, 'job-1', 'SUCCESS')  # must not raise

    def test_validator_is_importable_from_shared(self):
        """Regression for the worker-container ModuleNotFoundError (see test_packaging).

        The import inside fire_webhook must resolve without web/ on the path, since the
        worker image never ships it.
        """
        import webhook_validation
        assert callable(webhook_validation.validate_webhook_url)
