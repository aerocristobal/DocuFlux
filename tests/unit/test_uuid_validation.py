"""Behavioural tests for shared/uuid_validation.py.

`validate_uuid` gates every job-id-bearing route via `require_valid_uuid`, so what it
accepts is a security-relevant contract. It is deliberately permissive — it delegates to
`uuid.UUID(str(value))`, which accepts several spellings beyond the canonical dashed
form. These tests pin that, so a future tightening is a deliberate change rather than an
accident.
"""
import uuid

import pytest

from uuid_validation import validate_uuid

pytestmark = pytest.mark.unit


class TestAccepted:

    def test_canonical_v4_string(self):
        assert validate_uuid(str(uuid.uuid4())) is True

    def test_v1_uuid(self):
        assert validate_uuid(str(uuid.uuid1())) is True

    def test_nil_uuid(self):
        assert validate_uuid('00000000-0000-0000-0000-000000000000') is True

    def test_uppercase_is_accepted(self):
        assert validate_uuid(str(uuid.uuid4()).upper()) is True

    @pytest.mark.parametrize('spelling', [
        '6ba7b8109dad11d180b400c04fd430c8',            # no dashes
        '{6ba7b810-9dad-11d1-80b4-00c04fd430c8}',      # braced
        'urn:uuid:6ba7b810-9dad-11d1-80b4-00c04fd430c8',  # URN
    ])
    def test_permissive_spellings_are_accepted(self, spelling):
        """uuid.UUID() accepts these, so validate_uuid does too.

        Callers building Redis keys or paths from a job id therefore cannot assume the
        canonical dashed form. Tighten deliberately if that ever matters.
        """
        assert validate_uuid(spelling) is True

    def test_uuid_object_is_accepted(self):
        """str() is applied first, so a UUID instance round-trips."""
        assert validate_uuid(uuid.uuid4()) is True


class TestRejected:

    @pytest.mark.parametrize('value', [
        '',
        'not-a-uuid',
        '12345',
        '6ba7b810-9dad-11d1-80b4',              # too short
        '6ba7b810-9dad-11d1-80b4-00c04fd430c8x',  # trailing junk
        'zzzzzzzz-zzzz-zzzz-zzzz-zzzzzzzzzzzz',   # non-hex
    ])
    def test_malformed_strings_are_rejected(self, value):
        assert validate_uuid(value) is False

    @pytest.mark.parametrize('value', [None, 123, 12.5, [], {}, object()])
    def test_non_string_inputs_are_rejected_without_raising(self, value):
        """Route decorators call this on raw user input; it must never raise."""
        assert validate_uuid(value) is False

    def test_path_traversal_attempt_is_rejected(self):
        assert validate_uuid('../../etc/passwd') is False

    def test_redis_key_injection_attempt_is_rejected(self):
        assert validate_uuid('job:*') is False
