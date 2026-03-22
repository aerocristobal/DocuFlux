"""
Integration tests for the encryption pipeline.

Story 9.2: Round-trip encryption with key management via fakeredis.
Tests AES-256-GCM encryption/decryption, key wrapping, and KeyManager
operations against a real (in-memory) Redis backend.
"""

import base64
import importlib
import os
import tempfile

import fakeredis
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _get_real_classes():
    """Import the real EncryptionService and KeyManager classes.

    Other test files (test_worker.py, test_e2e.py) replace
    sys.modules['encryption'].EncryptionService with MagicMock at module
    level.  Reloading the modules here restores the original classes so
    these integration tests exercise the real crypto code.
    """
    import encryption as _enc_mod
    import key_manager as _km_mod
    importlib.reload(_enc_mod)
    importlib.reload(_km_mod)
    return _enc_mod.EncryptionService, _km_mod.KeyManager


_EncryptionService, _KeyManager = _get_real_classes()


@pytest.fixture
def master_key():
    """Generate a random 256-bit master encryption key (base64 encoded)."""
    raw = AESGCM.generate_key(bit_length=256)
    return base64.urlsafe_b64encode(raw).decode('utf-8')


@pytest.fixture
def encryption_service(master_key):
    return _EncryptionService(master_key)


@pytest.fixture
def fake_redis():
    return fakeredis.FakeRedis()


@pytest.fixture
def key_manager(fake_redis, encryption_service):
    return _KeyManager(fake_redis, encryption_service)


class TestEncryptionPipeline:
    """Full round-trip tests: keygen -> encrypt -> store -> retrieve -> decrypt."""

    def test_round_trip_encryption(self, encryption_service, key_manager):
        """AC 1: Round-trip encryption test passes."""
        job_id = "test-job-roundtrip"
        plaintext = b"Sensitive document content for encryption test."

        # Generate and store DEK
        dek = key_manager.generate_job_key(job_id)

        # Encrypt
        encrypted = encryption_service.encrypt_data(plaintext, dek)

        # Retrieve DEK from Redis
        retrieved_dek = key_manager.get_job_key(job_id)
        assert retrieved_dek == dek

        # Decrypt
        decrypted = encryption_service.decrypt_data(encrypted, retrieved_dek)
        assert decrypted == plaintext

    def test_round_trip_with_associated_data(self, encryption_service, key_manager):
        """Round-trip with AAD binding ensures context is validated."""
        job_id = "test-job-aad"
        plaintext = b"Content with associated data binding."

        dek = key_manager.generate_job_key(job_id)
        encrypted = encryption_service.encrypt_data(plaintext, dek, associated_data=job_id)

        retrieved_dek = key_manager.get_job_key(job_id)
        decrypted = encryption_service.decrypt_data(encrypted, retrieved_dek, associated_data=job_id)
        assert decrypted == plaintext

    def test_round_trip_file_encryption(self, encryption_service, key_manager):
        """Round-trip file encryption/decryption through temp files."""
        job_id = "test-job-file"
        content = b"File content to encrypt and decrypt."

        dek = key_manager.generate_job_key(job_id)

        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, "input.txt")
            encrypted_path = os.path.join(tmpdir, "encrypted.enc")
            output_path = os.path.join(tmpdir, "output.txt")

            with open(input_path, 'wb') as f:
                f.write(content)

            encryption_service.encrypt_file(input_path, encrypted_path, dek, associated_data=job_id)
            retrieved_dek = key_manager.get_job_key(job_id)
            encryption_service.decrypt_file(encrypted_path, output_path, retrieved_dek, associated_data=job_id)

            with open(output_path, 'rb') as f:
                assert f.read() == content

    def test_wrong_key_fails_decryption(self, encryption_service, key_manager):
        """AC 2: Wrong key fails decryption."""
        dek_a = key_manager.generate_job_key("job-a")
        dek_b = key_manager.generate_job_key("job-b")

        encrypted = encryption_service.encrypt_data(b"secret data", dek_a)

        with pytest.raises(ValueError, match="Decryption failed"):
            encryption_service.decrypt_data(encrypted, dek_b)

    def test_tampered_ciphertext_fails_gcm_auth(self, encryption_service):
        """AC 3: Tampered content fails GCM authentication."""
        dek = encryption_service.generate_key()
        encrypted_b64 = encryption_service.encrypt_data(b"original content", dek)

        # Decode, tamper with a ciphertext byte (after the 12-byte nonce), re-encode
        encrypted_bytes = base64.urlsafe_b64decode(encrypted_b64)
        tampered = bytearray(encrypted_bytes)
        # Flip a byte in the ciphertext portion (index 12+)
        tamper_idx = 14
        tampered[tamper_idx] ^= 0xFF
        tampered_b64 = base64.urlsafe_b64encode(bytes(tampered)).decode('utf-8')

        with pytest.raises(ValueError, match="Decryption failed"):
            encryption_service.decrypt_data(tampered_b64, dek)

    def test_tampered_associated_data_fails_gcm_auth(self, encryption_service):
        """Mismatched associated data fails GCM authentication."""
        dek = encryption_service.generate_key()
        encrypted = encryption_service.encrypt_data(
            b"content with AAD", dek, associated_data="job-1"
        )

        with pytest.raises(ValueError, match="Decryption failed"):
            encryption_service.decrypt_data(encrypted, dek, associated_data="job-2")

    def test_key_manager_stores_and_retrieves_via_redis(self, key_manager, fake_redis):
        """AC 4: Key storage uses fakeredis for realistic Redis behavior."""
        job_id = "test-job-redis"
        dek = key_manager.generate_job_key(job_id)

        # Verify key exists in Redis
        assert fake_redis.get(f"job:{job_id}:dek") is not None

        # Verify metadata exists
        metadata = fake_redis.hgetall(f"job:{job_id}:key_metadata")
        assert metadata is not None
        assert b'job_id' in metadata or 'job_id' in metadata

        # Retrieve and verify match
        retrieved = key_manager.get_job_key(job_id)
        assert retrieved == dek

    def test_key_manager_delete_removes_key(self, key_manager, fake_redis):
        """Deleted key returns None on retrieval."""
        job_id = "test-job-delete"
        key_manager.generate_job_key(job_id)

        assert key_manager.delete_job_key(job_id) is True
        assert key_manager.get_job_key(job_id) is None
        assert fake_redis.get(f"job:{job_id}:dek") is None

    def test_key_manager_rotate_produces_new_key(self, key_manager, fake_redis):
        """Rotation produces a different key and archives the old one."""
        job_id = "test-job-rotate"
        original_dek = key_manager.generate_job_key(job_id)

        new_dek = key_manager.rotate_job_key(job_id)

        assert new_dek != original_dek
        # Old key is archived
        assert fake_redis.get(f"job:{job_id}:dek_old") is not None
        # New key is retrievable
        assert key_manager.get_job_key(job_id) == new_dek

    def test_key_manager_get_nonexistent_returns_none(self, key_manager):
        """Getting a key for a non-existent job returns None."""
        assert key_manager.get_job_key("nonexistent-job") is None

    def test_key_wrapping_round_trip(self, encryption_service):
        """DEK wrapping and unwrapping preserves the key."""
        dek = encryption_service.generate_key()
        wrapped = encryption_service.wrap_key(dek)
        unwrapped = encryption_service.unwrap_key(wrapped)
        assert unwrapped == dek
