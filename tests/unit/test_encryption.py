"""Story 5.1b: unit tests for shared/encryption.py and shared/redis_encryption.py.

Covers AES-256-GCM known-answer round-trips, tamper detection (auth-tag
failure raises), DEK wrap/unwrap, and the Redis metadata encrypt/decrypt path
including tamper detection. Built on the deterministic scaffolding from 5.1a
(crypto_helpers), which is resilient to sys.modules mock pollution.
"""

import base64
import importlib.util
import os
import sys

import pytest

from tests.unit.crypto_helpers import (
    MASTER_KEY_B64,
    DEK_KEY_B64,
    KAT_PLAINTEXTS,
    make_encryption_service,
    assert_data_roundtrip,
    assert_tamper_detected,
    assert_wrong_key_fails,
)

_SHARED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "shared")


def _load_real(mod_name, filename):
    """Load a shared module from source, bypassing any sys.modules mock."""
    cached = sys.modules.get(mod_name)
    if cached is not None and type(cached).__name__ not in (
        "MagicMock", "Mock", "NonCallableMagicMock",
    ):
        return cached
    # Ensure 'encryption' is real before redis_encryption imports it.
    if "encryption" not in sys.modules or type(sys.modules["encryption"]).__name__ in (
        "MagicMock", "Mock", "NonCallableMagicMock",
    ):
        espec = importlib.util.spec_from_file_location(
            "encryption", os.path.join(_SHARED, "encryption.py"))
        emod = importlib.util.module_from_spec(espec)
        sys.modules["encryption"] = emod
        espec.loader.exec_module(emod)
    spec = importlib.util.spec_from_file_location(
        mod_name, os.path.join(_SHARED, filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# encryption.py — EncryptionService
# ---------------------------------------------------------------------------
class TestEncryptionServiceKAT:

    def test_roundtrip_all_kat_plaintexts(self):
        svc = make_encryption_service()
        for pt in KAT_PLAINTEXTS:
            assert_data_roundtrip(svc, pt)

    def test_roundtrip_with_associated_data(self):
        svc = make_encryption_service()
        assert_data_roundtrip(svc, b"payload", associated_data="job-123")

    def test_associated_data_mismatch_fails(self):
        svc = make_encryption_service()
        blob = svc.encrypt_data(b"payload", DEK_KEY_B64, associated_data="job-A")
        with pytest.raises(Exception):
            svc.decrypt_data(blob, DEK_KEY_B64, associated_data="job-B")

    def test_tamper_detected(self):
        svc = make_encryption_service()
        blob = svc.encrypt_data(b"authenticated", DEK_KEY_B64)
        assert_tamper_detected(svc, blob)

    def test_wrong_key_rejected(self):
        svc = make_encryption_service()
        blob = svc.encrypt_data(b"secret", DEK_KEY_B64)
        assert_wrong_key_fails(svc, blob)

    def test_nonce_is_random_per_encryption(self):
        """Same plaintext+key yields different ciphertext (random nonce)."""
        svc = make_encryption_service()
        a = svc.encrypt_data(b"same", DEK_KEY_B64)
        b = svc.encrypt_data(b"same", DEK_KEY_B64)
        assert a != b
        assert svc.decrypt_data(a, DEK_KEY_B64) == svc.decrypt_data(b, DEK_KEY_B64)

    def test_generate_key_is_32_bytes(self):
        svc = make_encryption_service()
        dek = svc.generate_key()
        assert len(base64.urlsafe_b64decode(dek)) == 32

    def test_invalid_master_key_length_raises(self):
        enc = _load_real("encryption", "encryption.py")
        short = base64.urlsafe_b64encode(b"too short").decode()
        with pytest.raises(ValueError):
            enc.EncryptionService(master_key=short)


class TestDEKWrapUnwrap:

    def test_wrap_unwrap_roundtrip(self):
        svc = make_encryption_service()
        dek = svc.generate_key()
        wrapped = svc.wrap_key(dek)
        assert wrapped != dek
        assert svc.unwrap_key(wrapped) == dek

    def test_wrapped_key_tamper_detected(self):
        svc = make_encryption_service()
        wrapped = svc.wrap_key(svc.generate_key())
        raw = bytearray(base64.urlsafe_b64decode(wrapped))
        raw[-1] ^= 0x01
        with pytest.raises(Exception):
            svc.unwrap_key(base64.urlsafe_b64encode(bytes(raw)).decode())


# ---------------------------------------------------------------------------
# redis_encryption.py — RedisEncryptionHelper
# ---------------------------------------------------------------------------
class TestRedisEncryption:

    def _helper(self):
        re_mod = _load_real("redis_encryption", "redis_encryption.py")
        svc = make_encryption_service()
        return re_mod, re_mod.RedisEncryptionHelper(encryption_service=svc)

    def test_sensitive_fields_roundtrip(self):
        re_mod, helper = self._helper()
        dek = make_encryption_service().generate_key()
        meta = {"filename": "secret report.pdf", "status": "SUCCESS", "progress": "100"}
        enc = helper.encrypt_metadata(meta, dek, job_id="job-1")
        # Sensitive field is encrypted + flagged; plaintext fields unchanged.
        assert enc["filename"] != "secret report.pdf"
        assert enc["filename_encrypted"] == "true"
        assert enc["status"] == "SUCCESS"
        dec = helper.decrypt_metadata(enc, dek, job_id="job-1")
        assert dec["filename"] == "secret report.pdf"
        assert dec["status"] == "SUCCESS"

    def test_non_sensitive_fields_not_encrypted(self):
        re_mod, helper = self._helper()
        dek = make_encryption_service().generate_key()
        meta = {"status": "PROCESSING", "progress": "50"}
        enc = helper.encrypt_metadata(meta, dek, job_id="j")
        assert "status_encrypted" not in enc
        assert enc["status"] == "PROCESSING"

    def test_tampered_encrypted_field_falls_back(self):
        """A tampered ciphertext fails auth; decrypt returns the raw value
        rather than crashing (defensive fallback in the helper)."""
        re_mod, helper = self._helper()
        dek = make_encryption_service().generate_key()
        enc = helper.encrypt_metadata({"filename": "a.pdf"}, dek, job_id="j")
        raw = bytearray(base64.urlsafe_b64decode(enc["filename"]))
        raw[-1] ^= 0x01
        enc["filename"] = base64.urlsafe_b64encode(bytes(raw)).decode()
        dec = helper.decrypt_metadata(enc, dek, job_id="j")
        # Not the original plaintext (tamper detected -> fallback to raw value).
        assert dec["filename"] != "a.pdf"

    def test_should_encrypt_field(self):
        re_mod, helper = self._helper()
        assert helper.should_encrypt_field("filename") is True
        assert helper.should_encrypt_field("status") is False
