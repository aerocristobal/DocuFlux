"""
Per-Job Encryption Key Management for DocuFlux

Manages data encryption keys (DEKs) for each conversion job.
DEKs are generated per-job, wrapped with the master encryption key (MEK),
and stored in Redis for retrieval during encryption/decryption operations.

Epic 23.2: Per-Job Encryption Key Management
"""

import logging
import redis
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from encryption import EncryptionService


class KeyManager:
    """
    Manages per-job data encryption keys (DEKs).

    Key Lifecycle:
    1. Generate: Create unique DEK for each job
    2. Store: Wrap DEK with MEK and store in Redis
    3. Retrieve: Fetch and unwrap DEK when needed
    4. Rotate: Support key rotation for long-running jobs
    5. Delete: Remove keys when jobs are complete/expired
    """

    def __init__(self, redis_client: redis.Redis, encryption_service: EncryptionService):
        """
        Initialize key manager.

        Args:
            redis_client: Redis client for key storage
            encryption_service: Encryption service with master key
        """
        self.redis = redis_client
        self.encryption_service = encryption_service
        self.logger = logging.getLogger(__name__)

    def generate_job_key(self, job_id: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """
        Generate and store a new DEK for a job.

        Args:
            job_id: Unique job identifier
            metadata: Optional metadata to store with key (user_id, filename, etc.)

        Returns:
            Unwrapped DEK (base64 encoded) for immediate use

        Raises:
            ValueError: If job_id is invalid
            RuntimeError: If key generation or storage fails
        """
        if not job_id:
            raise ValueError("job_id cannot be empty")

        try:
            # Generate new DEK
            dek = self.encryption_service.generate_key()
            self.logger.info(f"Generated DEK for job {job_id}")

            # Wrap DEK with master key
            wrapped_dek = self.encryption_service.wrap_key(dek)

            # Store wrapped DEK in Redis
            key_redis_key = f"job:{job_id}:dek"
            self.redis.set(key_redis_key, wrapped_dek)

            # Store key metadata
            if metadata is None:
                metadata = {}
            metadata['created_at'] = datetime.utcnow().isoformat()
            metadata['job_id'] = job_id

            metadata_key = f"job:{job_id}:key_metadata"
            self.redis.hset(metadata_key, mapping=metadata)

            # Set TTL (keys expire after 7 days by default)
            ttl_seconds = 7 * 24 * 60 * 60  # 7 days
            self.redis.expire(key_redis_key, ttl_seconds)
            self.redis.expire(metadata_key, ttl_seconds)

            self.logger.info(f"Stored wrapped DEK for job {job_id} (TTL: {ttl_seconds}s)")

            return dek

        except Exception as e:
            self.logger.error(f"Failed to generate key for job {job_id}: {e}")
            raise RuntimeError(f"Key generation failed: {e}")

    def get_job_key(self, job_id: str) -> Optional[str]:
        """
        Retrieve and unwrap DEK for a job.

        Args:
            job_id: Unique job identifier

        Returns:
            Unwrapped DEK (base64 encoded) or None if not found

        Raises:
            ValueError: If decryption fails (corrupted key or wrong master key)
        """
        if not job_id:
            raise ValueError("job_id cannot be empty")

        try:
            key_redis_key = f"job:{job_id}:dek"
            wrapped_dek = self.redis.get(key_redis_key)

            if wrapped_dek is None:
                self.logger.warning(f"No DEK found for job {job_id}")
                return None

            # Unwrap DEK
            if isinstance(wrapped_dek, bytes):
                wrapped_dek = wrapped_dek.decode('utf-8')

            dek = self.encryption_service.unwrap_key(wrapped_dek)
            self.logger.debug(f"Retrieved DEK for job {job_id}")

            # Update last_accessed timestamp
            metadata_key = f"job:{job_id}:key_metadata"
            self.redis.hset(metadata_key, 'last_accessed', datetime.utcnow().isoformat())

            return dek

        except ValueError as e:
            self.logger.error(f"Failed to unwrap key for job {job_id}: {e}")
            raise
        except Exception as e:
            self.logger.error(f"Failed to retrieve key for job {job_id}: {e}")
            return None

    def delete_job_key(self, job_id: str) -> bool:
        """
        Delete DEK and metadata for a job.

        Args:
            job_id: Unique job identifier

        Returns:
            True if keys were deleted, False if not found
        """
        if not job_id:
            raise ValueError("job_id cannot be empty")

        try:
            key_redis_key = f"job:{job_id}:dek"
            metadata_key = f"job:{job_id}:key_metadata"

            deleted_count = self.redis.delete(key_redis_key, metadata_key)

            if deleted_count > 0:
                self.logger.info(f"Deleted DEK for job {job_id}")
                return True
            else:
                self.logger.debug(f"No DEK found to delete for job {job_id}")
                return False

        except Exception as e:
            self.logger.error(f"Failed to delete key for job {job_id}: {e}")
            return False

    def rotate_job_key(self, job_id: str) -> str:
        """
        Rotate DEK for a job (generate new key, keep old for decryption).

        Useful for long-running jobs or security policy compliance.
        Old key is kept with _old suffix for decrypting existing files.

        Args:
            job_id: Unique job identifier

        Returns:
            New unwrapped DEK (base64 encoded)

        Raises:
            ValueError: If job has no existing key
            RuntimeError: If rotation fails
        """
        if not job_id:
            raise ValueError("job_id cannot be empty")

        try:
            # Get current key
            current_key = self.get_job_key(job_id)
            if current_key is None:
                raise ValueError(f"Cannot rotate key for job {job_id}: no existing key found")

            # Archive old key
            key_redis_key = f"job:{job_id}:dek"
            old_key_redis_key = f"job:{job_id}:dek_old"

            wrapped_dek = self.redis.get(key_redis_key)
            self.redis.set(old_key_redis_key, wrapped_dek)
            self.redis.expire(old_key_redis_key, 30 * 24 * 60 * 60)  # 30 days

            # Generate new key (overwrites current)
            new_dek = self.generate_job_key(job_id)

            # Update metadata
            metadata_key = f"job:{job_id}:key_metadata"
            self.redis.hset(metadata_key, 'rotated_at', datetime.utcnow().isoformat())

            self.logger.info(f"Rotated DEK for job {job_id} (old key archived)")

            return new_dek

        except Exception as e:
            self.logger.error(f"Failed to rotate key for job {job_id}: {e}")
            raise RuntimeError(f"Key rotation failed: {e}")

    def get_key_metadata(self, job_id: str) -> Optional[Dict[str, str]]:
        """
        Get metadata for a job's encryption key.

        Args:
            job_id: Unique job identifier

        Returns:
            Metadata dict (created_at, last_accessed, etc.) or None
        """
        if not job_id:
            raise ValueError("job_id cannot be empty")

        try:
            metadata_key = f"job:{job_id}:key_metadata"
            metadata = self.redis.hgetall(metadata_key)

            if not metadata:
                return None

            # Convert bytes to strings
            return {k.decode('utf-8') if isinstance(k, bytes) else k:
                    v.decode('utf-8') if isinstance(v, bytes) else v
                    for k, v in metadata.items()}

        except Exception as e:
            self.logger.error(f"Failed to get metadata for job {job_id}: {e}")
            return None

    def cleanup_expired_keys(self, days: int = 7) -> int:
        """
        Clean up keys that haven't been accessed in N days.

        Redis TTL handles automatic expiration, but this allows manual cleanup
        based on last_accessed timestamp.

        Args:
            days: Delete keys not accessed in this many days

        Returns:
            Number of keys deleted
        """
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days)
            deleted_count = 0

            # Scan for all job key metadata
            for key in self.redis.scan_iter(match="job:*:key_metadata"):
                metadata = self.redis.hgetall(key)

                # Check last_accessed timestamp
                last_accessed_str = metadata.get(b'last_accessed', metadata.get('last_accessed'))
                if last_accessed_str:
                    if isinstance(last_accessed_str, bytes):
                        last_accessed_str = last_accessed_str.decode('utf-8')

                    try:
                        last_accessed = datetime.fromisoformat(last_accessed_str)

                        if last_accessed < cutoff_date:
                            # Extract job_id from key
                            job_id = key.decode('utf-8').split(':')[1] if isinstance(key, bytes) else key.split(':')[1]

                            if self.delete_job_key(job_id):
                                deleted_count += 1
                    except (ValueError, IndexError) as e:
                        self.logger.warning(f"Failed to parse timestamp for key {key}: {e}")

            self.logger.info(f"Cleaned up {deleted_count} expired keys (>{days} days old)")
            return deleted_count

        except Exception as e:
            self.logger.error(f"Failed to cleanup expired keys: {e}")
            return 0

    def list_all_keys(self) -> list:
        """
        List all job IDs with active encryption keys.

        Returns:
            List of job_id strings
        """
        try:
            job_ids = []
            for key in self.redis.scan_iter(match="job:*:dek"):
                # Extract job_id from key (format: job:{job_id}:dek)
                key_str = key.decode('utf-8') if isinstance(key, bytes) else key
                parts = key_str.split(':')
                if len(parts) >= 2:
                    job_ids.append(parts[1])

            return job_ids

        except Exception as e:
            self.logger.error(f"Failed to list keys: {e}")
            return []


# Convenience function for creating key manager instance
def create_key_manager(redis_client: redis.Redis, master_key: Optional[str] = None) -> KeyManager:
    """
    Create a KeyManager instance with encryption service.

    Args:
        redis_client: Redis client instance
        master_key: Optional master key (if None, loads from environment)

    Returns:
        Configured KeyManager instance
    """
    encryption_service = EncryptionService(master_key)
    return KeyManager(redis_client, encryption_service)


if __name__ == '__main__':
    # Test key manager
    import secrets
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import base64

    print("Testing KeyManager...")

    # Create test Redis client (assumes Redis running on localhost)
    redis_client = redis.Redis(host='localhost', port=6379, db=0, decode_responses=False)

    # Generate test master key
    master_key = AESGCM.generate_key(bit_length=256)
    master_key_b64 = base64.urlsafe_b64encode(master_key).decode('utf-8')

    # Create key manager
    key_manager = create_key_manager(redis_client, master_key_b64)

    # Test 1: Generate job key
    print("\n1. Generating job key...")
    job_id = "test-job-123"
    metadata = {"user_id": "user456", "filename": "document.pdf"}
    dek = key_manager.generate_job_key(job_id, metadata)
    print(f"   Generated DEK: {dek[:20]}...")

    # Test 2: Retrieve job key
    print("\n2. Retrieving job key...")
    retrieved_dek = key_manager.get_job_key(job_id)
    assert retrieved_dek == dek, "Retrieved DEK doesn't match!"
    print(f"   Retrieved DEK: {retrieved_dek[:20]}...")

    # Test 3: Get metadata
    print("\n3. Getting key metadata...")
    metadata_result = key_manager.get_key_metadata(job_id)
    print(f"   Metadata: {metadata_result}")

    # Test 4: Rotate key
    print("\n4. Rotating job key...")
    new_dek = key_manager.rotate_job_key(job_id)
    assert new_dek != dek, "Rotated key is the same!"
    print(f"   New DEK: {new_dek[:20]}...")

    # Test 5: List keys
    print("\n5. Listing all keys...")
    all_keys = key_manager.list_all_keys()
    print(f"   Active job IDs: {all_keys}")

    # Test 6: Delete key
    print("\n6. Deleting job key...")
    deleted = key_manager.delete_job_key(job_id)
    assert deleted, "Key deletion failed!"
    print(f"   Deleted: {deleted}")

    # Verify deletion
    retrieved_after_delete = key_manager.get_job_key(job_id)
    assert retrieved_after_delete is None, "Key still exists after deletion!"
    print("   Verified: Key no longer exists")

    print("\n✓ All key manager tests passed!")
