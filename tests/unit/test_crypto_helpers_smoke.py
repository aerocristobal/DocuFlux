"""Smoke test for the Story 5.1a crypto test scaffolding.

Verifies the shared fixtures and KAT helpers in `crypto_helpers` are usable
and collectible by pytest — i.e. a sub-card (5.1b–d) building on them will
work. This is the executable form of the 5.1a "scaffolding is available"
acceptance scenario.
"""

from tests.unit.crypto_helpers import (
    MASTER_KEY_B64,
    MASTER_KEY_BYTES,
    DEK_KEY_B64,
    KAT_PLAINTEXTS,
    make_encryption_service,
    assert_data_roundtrip,
    assert_tamper_detected,
    assert_wrong_key_fails,
)


def test_master_key_is_32_bytes():
    assert len(MASTER_KEY_BYTES) == 32


def test_factory_builds_service():
    svc = make_encryption_service()
    assert svc is not None


def test_kat_roundtrip_via_helpers():
    """The shared KAT helper round-trips every sample plaintext."""
    svc = make_encryption_service()
    for pt in KAT_PLAINTEXTS:
        assert_data_roundtrip(svc, pt)


def test_tamper_detection_via_helpers():
    svc = make_encryption_service()
    blob = svc.encrypt_data(b"authenticated payload", DEK_KEY_B64)
    assert_tamper_detected(svc, blob)


def test_wrong_key_fails_via_helpers():
    svc = make_encryption_service()
    blob = svc.encrypt_data(b"secret", DEK_KEY_B64)
    assert_wrong_key_fails(svc, blob)


def test_fixtures_importable(encryption_service, master_key_b64, dek_b64):
    """Fixtures defined in crypto_helpers resolve when used directly."""
    assert master_key_b64 == MASTER_KEY_B64
    assert dek_b64 == DEK_KEY_B64
    assert encryption_service is not None
