"""Shared test scaffolding for the crypto/validation security suite (Story 5.1a).

Provides deterministic fixtures and Known-Answer-Test (KAT) helpers so the
sub-cards 5.1b–d can add tests against the security-critical modules
(`encryption`, `redis_encryption`, `key_manager`, `secrets_manager`,
`validation`, `metrics`, `warmup`) using a consistent, reproducible setup.

Import from a test module like::

    from tests.unit.crypto_helpers import (
        MASTER_KEY_B64, make_encryption_service, assert_data_roundtrip,
    )

Everything here is deterministic — no randomness leaks into assertions — so
tests built on it are stable across runs and CI.

The EncryptionService API used here:
    svc = EncryptionService(master_key=<b64 32-byte key>)
    dek = svc.generate_key()                  # base64 data-encryption key
    blob = svc.encrypt_data(plaintext, dek)   # -> base64 str (nonce+ct+tag)
    pt   = svc.decrypt_data(blob, dek)         # -> bytes (raises ValueError on tamper)
"""

import base64

import pytest

# ---------------------------------------------------------------------------
# Deterministic key material (KAT)
# ---------------------------------------------------------------------------
# A fixed 32-byte (256-bit) master key, base64 URL-safe encoded. Using a known
# constant rather than a random key keeps encryption tests reproducible.
MASTER_KEY_BYTES = bytes(range(32))  # 0x00..0x1f, exactly 32 bytes
MASTER_KEY_B64 = base64.urlsafe_b64encode(MASTER_KEY_BYTES).decode("utf-8")

# A second fixed 32-byte key, distinct from the master, usable as a DEK in
# deterministic tests that don't want a random generate_key().
DEK_KEY_BYTES = bytes(range(32, 64))  # 0x20..0x3f
DEK_KEY_B64 = base64.urlsafe_b64encode(DEK_KEY_BYTES).decode("utf-8")

# Sample plaintexts exercised by the round-trip KATs.
KAT_PLAINTEXTS = [
    b"",                                   # empty (boundary)
    b"hello world",                        # ascii
    b"\x00\x01\x02\xff\xfe",               # raw bytes
    "unicode: \u00e9\u00e8\u4f60\u597d".encode("utf-8"),  # multibyte
    b"A" * 4096,                           # larger payload (4 KiB)
]


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------
def make_encryption_service(master_key_b64=MASTER_KEY_B64):
    """Return an EncryptionService bound to a deterministic master key.

    Imported lazily so importing this helper module never drags in the
    crypto stack unless a test actually needs it.
    """
    from encryption import EncryptionService
    return EncryptionService(master_key=master_key_b64)


# ---------------------------------------------------------------------------
# Assertions / KAT helpers
# ---------------------------------------------------------------------------
def assert_data_roundtrip(svc, plaintext, key=DEK_KEY_B64, associated_data=None):
    """encrypt_data → decrypt_data preserves the plaintext (as bytes).

    Also asserts the ciphertext blob differs from the (non-empty) plaintext.
    """
    blob = svc.encrypt_data(plaintext, key, associated_data=associated_data)
    expected = plaintext.encode("utf-8") if isinstance(plaintext, str) else plaintext
    if expected:
        assert blob.encode("utf-8") != expected, "ciphertext must differ from plaintext"
    recovered = svc.decrypt_data(blob, key, associated_data=associated_data)
    assert recovered == expected, "round-trip did not preserve plaintext"
    return blob


def assert_tamper_detected(svc, blob, key=DEK_KEY_B64, associated_data=None):
    """Tampering with an encrypted blob must raise on decrypt (AES-GCM auth)."""
    raw = bytearray(base64.urlsafe_b64decode(blob))
    if not raw:
        pytest.skip("empty blob cannot be tampered")
    raw[-1] ^= 0x01  # flip a bit in the auth tag region
    tampered = base64.urlsafe_b64encode(bytes(raw)).decode("utf-8")
    with pytest.raises(Exception):
        svc.decrypt_data(tampered, key, associated_data=associated_data)


def assert_wrong_key_fails(svc, blob, wrong_key=MASTER_KEY_B64, associated_data=None):
    """Decrypting with the wrong key must raise rather than return garbage."""
    with pytest.raises(Exception):
        svc.decrypt_data(blob, wrong_key, associated_data=associated_data)


# ---------------------------------------------------------------------------
# Pytest fixtures (importable; sub-cards can request them by name once this
# module is imported into a conftest or test module).
# ---------------------------------------------------------------------------
@pytest.fixture
def master_key_b64():
    """The deterministic base64 master key as a fixture."""
    return MASTER_KEY_B64


@pytest.fixture
def dek_b64():
    """A deterministic base64 data-encryption key as a fixture."""
    return DEK_KEY_B64


@pytest.fixture
def encryption_service():
    """A ready-to-use EncryptionService bound to the deterministic key."""
    return make_encryption_service()


@pytest.fixture
def set_master_key_env(monkeypatch):
    """Set MASTER_ENCRYPTION_KEY in the environment for the duration of a test."""
    monkeypatch.setenv("MASTER_ENCRYPTION_KEY", MASTER_KEY_B64)
    return MASTER_KEY_B64
