"""
File Encryption Service for DocuFlux

Provides AES-256-GCM encryption/decryption for files and sensitive data.
Implements per-job encryption with unique data encryption keys (DEKs)
wrapped by a master encryption key (MEK).

Epic 23.1: File Encryption Service with AES-256-GCM
"""

import os
import logging
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2
from cryptography.hazmat.backends import default_backend
import base64


class EncryptionService:
    """
    AES-256-GCM encryption service for files and data.

    Uses authenticated encryption (AEAD) to ensure both confidentiality
    and integrity of encrypted data.
    """

    def __init__(self, master_key=None):
        """
        Initialize encryption service with master key.

        Args:
            master_key: 32-byte master encryption key (base64 encoded)
                       If None, will attempt to load from environment
        """
        if master_key is None:
            # Load from environment (set by secrets module)
            master_key_b64 = os.environ.get('MASTER_ENCRYPTION_KEY')
            if not master_key_b64:
                raise ValueError(
                    "MASTER_ENCRYPTION_KEY not set. "
                    "Generate with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
                )
            master_key = base64.urlsafe_b64decode(master_key_b64)
        elif isinstance(master_key, str):
            master_key = base64.urlsafe_b64decode(master_key)

        if len(master_key) != 32:
            raise ValueError(f"Master key must be 32 bytes, got {len(master_key)}")

        self.master_key = master_key
        self.aesgcm = AESGCM(self.master_key)

    def generate_key(self):
        """
        Generate a random 256-bit encryption key.

        Returns:
            32-byte key (base64 URL-safe encoded string)
        """
        key = AESGCM.generate_key(bit_length=256)
        return base64.urlsafe_b64encode(key).decode('utf-8')

    def encrypt_data(self, plaintext, key, associated_data=None):
        """
        Encrypt data using AES-256-GCM.

        Args:
            plaintext: Data to encrypt (bytes or string)
            key: 32-byte encryption key (base64 encoded string)
            associated_data: Optional associated data for authentication (string)

        Returns:
            Encrypted data as base64 string: base64(nonce + ciphertext + tag)
        """
        if isinstance(plaintext, str):
            plaintext = plaintext.encode('utf-8')

        if isinstance(key, str):
            key = base64.urlsafe_b64decode(key)

        if associated_data and isinstance(associated_data, str):
            associated_data = associated_data.encode('utf-8')

        # Generate random 96-bit nonce (12 bytes - recommended for GCM)
        nonce = os.urandom(12)

        # Encrypt with AES-256-GCM
        aesgcm = AESGCM(key)
        ciphertext = aesgcm.encrypt(nonce, plaintext, associated_data)

        # Return nonce + ciphertext (ciphertext includes 16-byte auth tag)
        encrypted = nonce + ciphertext
        return base64.urlsafe_b64encode(encrypted).decode('utf-8')

    def decrypt_data(self, encrypted_data, key, associated_data=None):
        """
        Decrypt data encrypted with AES-256-GCM.

        Args:
            encrypted_data: Base64 encoded encrypted data
            key: 32-byte encryption key (base64 encoded string)
            associated_data: Optional associated data for authentication (string)

        Returns:
            Decrypted plaintext as bytes

        Raises:
            ValueError: If decryption fails (wrong key, corrupted data, or tampered data)
        """
        if isinstance(key, str):
            key = base64.urlsafe_b64decode(key)

        if associated_data and isinstance(associated_data, str):
            associated_data = associated_data.encode('utf-8')

        try:
            # Decode base64
            encrypted = base64.urlsafe_b64decode(encrypted_data)

            # Extract nonce (first 12 bytes)
            nonce = encrypted[:12]
            ciphertext = encrypted[12:]

            # Decrypt
            aesgcm = AESGCM(key)
            plaintext = aesgcm.decrypt(nonce, ciphertext, associated_data)

            return plaintext

        except Exception as e:
            logging.error(f"Decryption failed: {e}")
            raise ValueError(f"Decryption failed: {e}")

    def encrypt_file(self, input_path, output_path, key, associated_data=None):
        """
        Encrypt a file using AES-256-GCM.

        Args:
            input_path: Path to plaintext file
            output_path: Path for encrypted output
            key: 32-byte encryption key (base64 encoded string)
            associated_data: Optional associated data (e.g., job_id)

        Raises:
            FileNotFoundError: If input file doesn't exist
            IOError: If file operations fail
        """
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"Input file not found: {input_path}")

        # Read plaintext
        with open(input_path, 'rb') as f:
            plaintext = f.read()

        # Encrypt
        encrypted = self.encrypt_data(plaintext, key, associated_data)

        # Write encrypted file
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w') as f:
            f.write(encrypted)

        logging.info(f"Encrypted file: {input_path} -> {output_path} ({len(plaintext)} bytes)")

    def decrypt_file(self, input_path, output_path, key, associated_data=None):
        """
        Decrypt a file encrypted with AES-256-GCM.

        Args:
            input_path: Path to encrypted file
            output_path: Path for decrypted output
            key: 32-byte encryption key (base64 encoded string)
            associated_data: Optional associated data (must match encryption)

        Raises:
            FileNotFoundError: If input file doesn't exist
            ValueError: If decryption fails
            IOError: If file operations fail
        """
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"Encrypted file not found: {input_path}")

        # Read encrypted file
        with open(input_path, 'r') as f:
            encrypted_data = f.read()

        # Decrypt
        plaintext = self.decrypt_data(encrypted_data, key, associated_data)

        # Write decrypted file
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'wb') as f:
            f.write(plaintext)

        logging.info(f"Decrypted file: {input_path} -> {output_path} ({len(plaintext)} bytes)")

    def encrypt_file_streaming(self, input_path, key, associated_data=None, chunk_size=64*1024):
        """
        Generator that yields encrypted chunks for streaming.

        Useful for encrypting large files without loading entire file into memory.

        Args:
            input_path: Path to plaintext file
            key: 32-byte encryption key (base64 encoded string)
            associated_data: Optional associated data
            chunk_size: Size of chunks to read (default 64KB)

        Yields:
            Encrypted data chunks (bytes)
        """
        if isinstance(key, str):
            key = base64.urlsafe_b64decode(key)

        if associated_data and isinstance(associated_data, str):
            associated_data = associated_data.encode('utf-8')

        # Generate nonce
        nonce = os.urandom(12)
        yield base64.urlsafe_b64encode(nonce)

        # Encrypt file in chunks
        aesgcm = AESGCM(key)
        with open(input_path, 'rb') as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break

                # Encrypt chunk
                encrypted_chunk = aesgcm.encrypt(nonce, chunk, associated_data)
                yield base64.urlsafe_b64encode(encrypted_chunk)

                # Increment nonce for next chunk (to avoid nonce reuse)
                nonce = self._increment_nonce(nonce)

    def decrypt_file_streaming(self, encrypted_stream, key, associated_data=None):
        """
        Generator that yields decrypted chunks from encrypted stream.

        Args:
            encrypted_stream: Iterator of encrypted chunks (base64 encoded)
            key: 32-byte encryption key (base64 encoded string)
            associated_data: Optional associated data

        Yields:
            Decrypted data chunks (bytes)
        """
        if isinstance(key, str):
            key = base64.urlsafe_b64decode(key)

        if associated_data and isinstance(associated_data, str):
            associated_data = associated_data.encode('utf-8')

        aesgcm = AESGCM(key)
        chunks = iter(encrypted_stream)

        # First chunk is the nonce
        nonce = base64.urlsafe_b64decode(next(chunks))

        # Decrypt remaining chunks
        for encrypted_chunk in chunks:
            encrypted_data = base64.urlsafe_b64decode(encrypted_chunk)
            plaintext = aesgcm.decrypt(nonce, encrypted_data, associated_data)
            yield plaintext

            # Increment nonce
            nonce = self._increment_nonce(nonce)

    @staticmethod
    def _increment_nonce(nonce):
        """Increment nonce as big-endian integer."""
        nonce_int = int.from_bytes(nonce, byteorder='big')
        nonce_int = (nonce_int + 1) % (2 ** (len(nonce) * 8))
        return nonce_int.to_bytes(len(nonce), byteorder='big')

    def wrap_key(self, data_encryption_key):
        """
        Wrap (encrypt) a data encryption key with the master key.

        Args:
            data_encryption_key: DEK to wrap (base64 encoded string)

        Returns:
            Wrapped key (base64 encoded string)
        """
        return self.encrypt_data(data_encryption_key, base64.urlsafe_b64encode(self.master_key).decode('utf-8'))

    def unwrap_key(self, wrapped_key):
        """
        Unwrap (decrypt) a wrapped data encryption key.

        Args:
            wrapped_key: Wrapped DEK (base64 encoded string)

        Returns:
            Unwrapped DEK (base64 encoded string)
        """
        decrypted = self.decrypt_data(wrapped_key, base64.urlsafe_b64encode(self.master_key).decode('utf-8'))
        return decrypted.decode('utf-8')


# Convenience functions for quick encryption/decryption

def encrypt_string(plaintext, key):
    """Encrypt a string with the given key."""
    service = EncryptionService()
    return service.encrypt_data(plaintext, key)


def decrypt_string(encrypted_data, key):
    """Decrypt a string with the given key."""
    service = EncryptionService()
    decrypted = service.decrypt_data(encrypted_data, key)
    return decrypted.decode('utf-8')


if __name__ == '__main__':
    # Test encryption service
    import secrets

    print("Testing EncryptionService...")

    # Generate test master key
    master_key = AESGCM.generate_key(bit_length=256)
    master_key_b64 = base64.urlsafe_b64encode(master_key).decode('utf-8')
    print(f"Generated master key: {master_key_b64}")

    # Initialize service
    service = EncryptionService(master_key_b64)

    # Generate DEK
    dek = service.generate_key()
    print(f"Generated DEK: {dek}")

    # Test data encryption
    plaintext = "This is sensitive data that needs encryption!"
    encrypted = service.encrypt_data(plaintext, dek, associated_data="job123")
    print(f"Encrypted: {encrypted[:50]}...")

    # Test data decryption
    decrypted = service.decrypt_data(encrypted, dek, associated_data="job123")
    assert decrypted.decode('utf-8') == plaintext
    print(f"Decrypted: {decrypted.decode('utf-8')}")

    # Test key wrapping
    wrapped_dek = service.wrap_key(dek)
    print(f"Wrapped DEK: {wrapped_dek[:50]}...")

    unwrapped_dek = service.unwrap_key(wrapped_dek)
    assert unwrapped_dek == dek
    print(f"Unwrapped DEK: {unwrapped_dek}")

    print("\n✓ All encryption tests passed!")
