"""Behavioural tests for shared/settings_loader.py.

This module runs on every import of web/app.py and worker/tasks/__init__.py, so its
*line* coverage was already near-total — but nothing asserted what it actually does.
These tests pin the fallback rules and the no-mutation guarantee.
"""
from unittest.mock import patch

import pytest

from settings_loader import load_settings

pytestmark = pytest.mark.unit


class _Settings:
    """Minimal stand-in exposing the attributes load_settings touches.

    model_copy(update=...) mirrors pydantic's semantics: a new object, base untouched.
    """

    def __init__(self, **kwargs):
        self.redis_metadata_url = kwargs.pop('redis_metadata_url', 'redis://localhost:6379/1')
        self.storage_uri = kwargs.pop('storage_uri', None)
        self.socketio_message_queue = kwargs.pop('socketio_message_queue', None)
        for key, value in kwargs.items():
            setattr(self, key, value)

    def model_copy(self, update=None):
        clone = _Settings.__new__(_Settings)
        clone.__dict__.update(self.__dict__)
        for key, value in (update or {}).items():
            setattr(clone, key, value)
        return clone


def _with_secrets(secrets):
    return patch('settings_loader.load_all_secrets', return_value=secrets)


class TestFallbacks:

    def test_storage_uri_falls_back_to_redis_metadata_url(self):
        base = _Settings(redis_metadata_url='redis://cache:6379/3', storage_uri=None)
        with _with_secrets({}):
            result = load_settings(base)
        assert result.storage_uri == 'redis://cache:6379/3'

    def test_socketio_message_queue_falls_back_to_redis_metadata_url(self):
        base = _Settings(redis_metadata_url='redis://cache:6379/3',
                         socketio_message_queue=None)
        with _with_secrets({}):
            result = load_settings(base)
        assert result.socketio_message_queue == 'redis://cache:6379/3'

    def test_explicit_storage_uri_is_not_overwritten(self):
        base = _Settings(redis_metadata_url='redis://cache:6379/3',
                         storage_uri='memory://')
        with _with_secrets({}):
            result = load_settings(base)
        assert result.storage_uri == 'memory://'

    def test_explicit_socketio_queue_is_not_overwritten(self):
        base = _Settings(redis_metadata_url='redis://cache:6379/3',
                         socketio_message_queue='amqp://broker/')
        with _with_secrets({}):
            result = load_settings(base)
        assert result.socketio_message_queue == 'amqp://broker/'


class TestSecretMerging:

    def test_secret_names_are_lowercased_into_settings_fields(self):
        """Secrets arrive as SCREAMING_CASE; settings fields are snake_case."""
        base = _Settings()
        with _with_secrets({'SECRET_KEY': 's3kr3t'}):
            result = load_settings(base)
        assert result.secret_key == 's3kr3t'

    def test_none_valued_secrets_are_dropped(self):
        """An unset optional secret must not clobber a configured default."""
        base = _Settings()
        base.admin_api_secret = 'from-config'
        with _with_secrets({'ADMIN_API_SECRET': None}):
            result = load_settings(base)
        assert result.admin_api_secret == 'from-config'

    def test_empty_string_secret_is_kept(self):
        """Only None is dropped — an explicit empty value is a real setting."""
        base = _Settings()
        with _with_secrets({'ADMIN_API_SECRET': ''}):
            result = load_settings(base)
        assert result.admin_api_secret == ''


class TestFailureAndIsolation:

    def test_secret_loading_failure_propagates(self):
        """A missing required secret must stop startup, not silently degrade."""
        base = _Settings()
        with patch('settings_loader.load_all_secrets',
                   side_effect=ValueError("Required secret 'secret_key' not found")):
            with pytest.raises(ValueError, match='secret_key'):
                load_settings(base)

    def test_base_settings_object_is_not_mutated(self):
        """load_settings returns a copy; the caller's base instance is untouched."""
        base = _Settings(redis_metadata_url='redis://cache:6379/3', storage_uri=None)
        with _with_secrets({'SECRET_KEY': 's3kr3t'}):
            result = load_settings(base)

        assert base.storage_uri is None
        assert not hasattr(base, 'secret_key')
        assert result is not base
