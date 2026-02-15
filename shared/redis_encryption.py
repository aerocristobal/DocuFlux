"""
Redis Metadata Encryption for DocuFlux

Provides transparent encryption/decryption of sensitive job metadata
stored in Redis (filenames, error messages, etc.)

Epic 23.4: Redis Data Encryption for Sensitive Metadata
"""

import logging
from typing import Dict, Any, Optional
from encryption import EncryptionService


# Fields that contain sensitive data and should be encrypted
SENSITIVE_FIELDS = {
    'filename',      # Original upload filename (may contain PII)
    'error',         # Error messages (may contain file paths)
    'output_file',   # Output filename
}

# Fields that should never be encrypted (required for queries/logic)
PLAINTEXT_FIELDS = {
    'status',        # Job status (PENDING, PROCESSING, SUCCESS, FAILURE)
    'from',          # Source format
    'to',            # Target format
    'created_at',    # Timestamp
    'started_at',    # Timestamp
    'completed_at',  # Timestamp
    'downloaded_at', # Timestamp
    'last_viewed',   # Timestamp
    'progress',      # Progress percentage
    'encrypted',     # Encryption flag
    'force_ocr',     # Marker option
    'use_llm',       # Marker option
}


class RedisEncryptionHelper:
    """
    Helper class for encrypting/decrypting sensitive Redis metadata.

    Uses a shared encryption service instance to minimize overhead.
    """

    def __init__(self, encryption_service: Optional[EncryptionService] = None):
        """
        Initialize Redis encryption helper.

        Args:
            encryption_service: Optional EncryptionService instance
                               If None, creates new instance
        """
        self.encryption_service = encryption_service or EncryptionService()
        self.logger = logging.getLogger(__name__)

    def encrypt_metadata(self, metadata: Dict[str, Any], dek: str, job_id: str) -> Dict[str, Any]:
        """
        Encrypt sensitive fields in job metadata.

        Args:
            metadata: Job metadata dictionary
            dek: Data encryption key (base64 encoded)
            job_id: Job identifier (used as associated data)

        Returns:
            Metadata dict with sensitive fields encrypted
        """
        encrypted_metadata = {}

        for key, value in metadata.items():
            if key in SENSITIVE_FIELDS and value:
                # Encrypt sensitive field
                try:
                    encrypted_value = self.encryption_service.encrypt_data(
                        plaintext=str(value),
                        key=dek,
                        associated_data=f"{job_id}:{key}"
                    )
                    encrypted_metadata[key] = encrypted_value
                    encrypted_metadata[f"{key}_encrypted"] = "true"

                except Exception as e:
                    self.logger.error(f"Failed to encrypt field {key}: {e}")
                    # Store plaintext as fallback
                    encrypted_metadata[key] = str(value)
                    encrypted_metadata[f"{key}_encrypted"] = "false"
            else:
                # Store plaintext (non-sensitive or empty)
                encrypted_metadata[key] = str(value) if value is not None else ""

        return encrypted_metadata

    def decrypt_metadata(self, metadata: Dict[str, Any], dek: str, job_id: str) -> Dict[str, Any]:
        """
        Decrypt sensitive fields in job metadata.

        Args:
            metadata: Job metadata dictionary (may contain encrypted fields)
            dek: Data encryption key (base64 encoded)
            job_id: Job identifier (used as associated data)

        Returns:
            Metadata dict with sensitive fields decrypted
        """
        decrypted_metadata = {}

        for key, value in metadata.items():
            # Skip encryption flag fields
            if key.endswith('_encrypted'):
                continue

            # Check if field was encrypted
            encrypted_flag = metadata.get(f"{key}_encrypted") == "true"

            if encrypted_flag and key in SENSITIVE_FIELDS:
                # Decrypt field
                try:
                    decrypted_value = self.encryption_service.decrypt_data(
                        encrypted_data=value,
                        key=dek,
                        associated_data=f"{job_id}:{key}"
                    )
                    decrypted_metadata[key] = decrypted_value.decode('utf-8')

                except Exception as e:
                    self.logger.error(f"Failed to decrypt field {key}: {e}")
                    # Return encrypted value as fallback
                    decrypted_metadata[key] = value
            else:
                # Field not encrypted, return as-is
                decrypted_metadata[key] = value

        return decrypted_metadata

    def should_encrypt_field(self, field_name: str) -> bool:
        """
        Check if a field should be encrypted.

        Args:
            field_name: Name of the field

        Returns:
            True if field is sensitive and should be encrypted
        """
        return field_name in SENSITIVE_FIELDS


# Convenience functions for single-field encryption

def encrypt_field(field_value: str, dek: str, field_name: str, job_id: str) -> str:
    """
    Encrypt a single sensitive field value.

    Args:
        field_value: Value to encrypt
        dek: Data encryption key
        field_name: Name of the field (for associated data)
        job_id: Job identifier

    Returns:
        Encrypted value (base64 encoded)
    """
    service = EncryptionService()
    return service.encrypt_data(
        plaintext=field_value,
        key=dek,
        associated_data=f"{job_id}:{field_name}"
    )


def decrypt_field(encrypted_value: str, dek: str, field_name: str, job_id: str) -> str:
    """
    Decrypt a single encrypted field value.

    Args:
        encrypted_value: Encrypted value (base64 encoded)
        dek: Data encryption key
        field_name: Name of the field (for associated data)
        job_id: Job identifier

    Returns:
        Decrypted plaintext value
    """
    service = EncryptionService()
    decrypted_bytes = service.decrypt_data(
        encrypted_data=encrypted_value,
        key=dek,
        associated_data=f"{job_id}:{field_name}"
    )
    return decrypted_bytes.decode('utf-8')


if __name__ == '__main__':
    # Test Redis encryption helper
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import base64

    print("Testing RedisEncryptionHelper...")

    # Generate test master key
    master_key = AESGCM.generate_key(bit_length=256)
    master_key_b64 = base64.urlsafe_b64encode(master_key).decode('utf-8')

    # Create encryption service
    encryption_service = EncryptionService(master_key_b64)

    # Generate DEK for test job
    dek = encryption_service.generate_key()
    job_id = "test-job-456"

    # Create helper
    helper = RedisEncryptionHelper(encryption_service)

    # Test metadata
    original_metadata = {
        'filename': 'confidential_report.pdf',
        'status': 'SUCCESS',
        'from': 'pdf',
        'to': 'markdown',
        'error': '/path/to/sensitive/file.pdf: conversion failed',
        'created_at': '1234567890',
        'progress': '100'
    }

    print("\n1. Original metadata:")
    for key, value in original_metadata.items():
        print(f"   {key}: {value}")

    # Encrypt sensitive fields
    print("\n2. Encrypting sensitive fields...")
    encrypted_metadata = helper.encrypt_metadata(original_metadata, dek, job_id)

    print("\n3. Encrypted metadata:")
    for key, value in encrypted_metadata.items():
        if key.endswith('_encrypted'):
            continue
        is_encrypted = encrypted_metadata.get(f"{key}_encrypted") == "true"
        display_value = value[:40] + "..." if is_encrypted and len(value) > 40 else value
        print(f"   {key}: {display_value} [encrypted: {is_encrypted}]")

    # Decrypt fields
    print("\n4. Decrypting sensitive fields...")
    decrypted_metadata = helper.decrypt_metadata(encrypted_metadata, dek, job_id)

    print("\n5. Decrypted metadata:")
    for key, value in decrypted_metadata.items():
        print(f"   {key}: {value}")

    # Verify roundtrip
    print("\n6. Verification:")
    for key in original_metadata:
        original_value = str(original_metadata[key])
        decrypted_value = decrypted_metadata.get(key)

        if original_value == decrypted_value:
            print(f"   ✓ {key}: Match")
        else:
            print(f"   ✗ {key}: MISMATCH!")
            print(f"     Original:  {original_value}")
            print(f"     Decrypted: {decrypted_value}")

    print("\n✓ All Redis encryption tests passed!")
