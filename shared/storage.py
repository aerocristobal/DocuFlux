"""
Storage abstraction layer for DocuFlux.

Epic 5: Decouple Storage from Shared Volumes.

Defines a StorageBackend protocol and implementations for local filesystem
and S3-compatible object storage.
"""

import os
import shutil
import logging
import tempfile
from typing import Protocol, Optional, BinaryIO, Iterator, Union, runtime_checkable


@runtime_checkable
class StorageBackend(Protocol):
    """Protocol defining the storage interface for DocuFlux."""

    def save_file(self, job_id: str, filename: str, data: Union[bytes, BinaryIO],
                  folder: str = "output") -> None: ...

    def read_file(self, job_id: str, filename: str,
                  folder: str = "output") -> bytes: ...

    def file_exists(self, job_id: str, filename: str = "",
                    folder: str = "output") -> bool: ...

    def delete_job(self, job_id: str) -> None: ...

    def list_files(self, job_id: str, folder: str = "output") -> list[str]: ...

    def makedirs(self, job_id: str, subpath: str = "",
                 folder: str = "output") -> None: ...

    def get_local_path(self, job_id: str, filename: str = "",
                       folder: str = "upload") -> str: ...

    def get_file_size(self, job_id: str, filename: str,
                      folder: str = "upload") -> int: ...

    def serve_download(self, job_id: str, filename: str,
                       folder: str = "output"): ...

    def disk_usage(self) -> Optional[tuple[int, int, int]]: ...

    def walk_job(self, job_id: str,
                 folder: str = "output") -> Iterator[tuple[str, list[str], list[str]]]: ...

    def delete_subpath(self, job_id: str, subpath: str,
                       folder: str = "output") -> None: ...

    def job_dir_exists(self, job_id: str, folder: str = "output") -> bool: ...


class LocalStorageBackend:
    """Local filesystem storage backend — wraps existing os/shutil operations."""

    def __init__(self, upload_folder: str, output_folder: str):
        self.upload_folder = upload_folder
        self.output_folder = output_folder

    def _base(self, folder: str) -> str:
        return self.upload_folder if folder == "upload" else self.output_folder

    def _job_path(self, job_id: str, filename: str = "", folder: str = "output") -> str:
        base = self._base(folder)
        if filename:
            return os.path.join(base, job_id, filename)
        return os.path.join(base, job_id)

    def save_file(self, job_id: str, filename: str, data: Union[bytes, BinaryIO],
                  folder: str = "output") -> None:
        path = self._job_path(job_id, filename, folder)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if isinstance(data, bytes):
            with open(path, 'wb') as f:
                f.write(data)
        else:
            with open(path, 'wb') as f:
                while True:
                    chunk = data.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)

    def read_file(self, job_id: str, filename: str,
                  folder: str = "output") -> bytes:
        path = self._job_path(job_id, filename, folder)
        with open(path, 'rb') as f:
            return f.read()

    def file_exists(self, job_id: str, filename: str = "",
                    folder: str = "output") -> bool:
        path = self._job_path(job_id, filename, folder)
        return os.path.exists(path)

    def delete_job(self, job_id: str) -> None:
        for folder in ("upload", "output"):
            path = self._job_path(job_id, folder=folder)
            if os.path.exists(path):
                shutil.rmtree(path)

    def list_files(self, job_id: str, folder: str = "output") -> list[str]:
        job_dir = self._job_path(job_id, folder=folder)
        if not os.path.exists(job_dir):
            return []
        result = []
        for root, dirs, files in os.walk(job_dir):
            for f in files:
                rel = os.path.relpath(os.path.join(root, f), job_dir)
                result.append(rel)
        return sorted(result)

    def makedirs(self, job_id: str, subpath: str = "",
                 folder: str = "output") -> None:
        if subpath:
            path = os.path.join(self._job_path(job_id, folder=folder), subpath)
        else:
            path = self._job_path(job_id, folder=folder)
        os.makedirs(path, exist_ok=True)

    def get_local_path(self, job_id: str, filename: str = "",
                       folder: str = "upload") -> str:
        return self._job_path(job_id, filename, folder)

    def get_file_size(self, job_id: str, filename: str,
                      folder: str = "upload") -> int:
        path = self._job_path(job_id, filename, folder)
        return os.path.getsize(path)

    def serve_download(self, job_id: str, filename: str,
                       folder: str = "output"):
        from flask import send_from_directory
        job_dir = self._job_path(job_id, folder=folder)
        return send_from_directory(job_dir, filename, as_attachment=True)

    def disk_usage(self) -> Optional[tuple[int, int, int]]:
        try:
            return shutil.disk_usage(self.upload_folder)
        except Exception:
            return None

    def walk_job(self, job_id: str,
                 folder: str = "output") -> Iterator[tuple[str, list[str], list[str]]]:
        job_dir = self._job_path(job_id, folder=folder)
        if os.path.exists(job_dir):
            yield from os.walk(job_dir)

    def delete_subpath(self, job_id: str, subpath: str,
                       folder: str = "output") -> None:
        path = os.path.join(self._job_path(job_id, folder=folder), subpath)
        if os.path.exists(path):
            shutil.rmtree(path, ignore_errors=True)

    def job_dir_exists(self, job_id: str, folder: str = "output") -> bool:
        return os.path.isdir(self._job_path(job_id, folder=folder))

    def ensure_directories(self) -> None:
        """Create upload and output root directories if they don't exist."""
        os.makedirs(self.upload_folder, exist_ok=True)
        os.makedirs(self.output_folder, exist_ok=True)


class S3StorageBackend:
    """S3-compatible object storage backend using boto3."""

    def __init__(self, settings):
        import boto3

        s3_access_key = settings.s3_access_key
        if s3_access_key and hasattr(s3_access_key, 'get_secret_value'):
            s3_access_key = s3_access_key.get_secret_value()

        s3_secret_key = settings.s3_secret_key
        if s3_secret_key and hasattr(s3_secret_key, 'get_secret_value'):
            s3_secret_key = s3_secret_key.get_secret_value()

        client_kwargs = {
            'region_name': settings.s3_region,
        }
        if settings.s3_endpoint_url:
            client_kwargs['endpoint_url'] = settings.s3_endpoint_url
        if s3_access_key:
            client_kwargs['aws_access_key_id'] = s3_access_key
        if s3_secret_key:
            client_kwargs['aws_secret_access_key'] = s3_secret_key

        self.s3 = boto3.client('s3', **client_kwargs)
        self.bucket = settings.s3_bucket
        self.sse_algorithm = settings.s3_sse_algorithm
        self._temp_dir = tempfile.mkdtemp(prefix='docuflux_s3_')

    def _prefix(self, folder: str) -> str:
        return "uploads" if folder == "upload" else "outputs"

    def _key(self, job_id: str, filename: str = "", folder: str = "output") -> str:
        prefix = self._prefix(folder)
        if filename:
            return f"{prefix}/{job_id}/{filename}"
        return f"{prefix}/{job_id}/"

    def _sse_kwargs(self) -> dict:
        if self.sse_algorithm:
            return {'ServerSideEncryption': self.sse_algorithm}
        return {}

    def save_file(self, job_id: str, filename: str, data: Union[bytes, BinaryIO],
                  folder: str = "output") -> None:
        key = self._key(job_id, filename, folder)
        kwargs = {'Bucket': self.bucket, 'Key': key, **self._sse_kwargs()}
        if isinstance(data, bytes):
            kwargs['Body'] = data
        else:
            kwargs['Body'] = data.read()
        self.s3.put_object(**kwargs)

    def read_file(self, job_id: str, filename: str,
                  folder: str = "output") -> bytes:
        key = self._key(job_id, filename, folder)
        response = self.s3.get_object(Bucket=self.bucket, Key=key)
        return response['Body'].read()

    def file_exists(self, job_id: str, filename: str = "",
                    folder: str = "output") -> bool:
        if not filename:
            # Check if any objects exist with the job prefix
            prefix = self._key(job_id, folder=folder)
            response = self.s3.list_objects_v2(
                Bucket=self.bucket, Prefix=prefix, MaxKeys=1)
            return response.get('KeyCount', 0) > 0
        try:
            self.s3.head_object(Bucket=self.bucket,
                                Key=self._key(job_id, filename, folder))
            return True
        except self.s3.exceptions.ClientError:
            return False

    def delete_job(self, job_id: str) -> None:
        for folder in ("upload", "output"):
            prefix = self._key(job_id, folder=folder)
            while True:
                response = self.s3.list_objects_v2(
                    Bucket=self.bucket, Prefix=prefix, MaxKeys=1000)
                contents = response.get('Contents', [])
                if not contents:
                    break
                self.s3.delete_objects(
                    Bucket=self.bucket,
                    Delete={'Objects': [{'Key': obj['Key']} for obj in contents]})
                if not response.get('IsTruncated'):
                    break

    def list_files(self, job_id: str, folder: str = "output") -> list[str]:
        prefix = self._key(job_id, folder=folder)
        result = []
        paginator = self.s3.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get('Contents', []):
                rel = obj['Key'][len(prefix):]
                if rel:  # skip the directory marker itself
                    result.append(rel)
        return sorted(result)

    def makedirs(self, job_id: str, subpath: str = "",
                 folder: str = "output") -> None:
        # S3 doesn't need directory creation — no-op
        pass

    def get_local_path(self, job_id: str, filename: str = "",
                       folder: str = "upload") -> str:
        if not filename:
            local_dir = os.path.join(self._temp_dir, job_id)
            os.makedirs(local_dir, exist_ok=True)
            return local_dir

        local_path = os.path.join(self._temp_dir, job_id, filename)
        os.makedirs(os.path.dirname(local_path), exist_ok=True)

        # Download if it exists in S3
        try:
            data = self.read_file(job_id, filename, folder)
            with open(local_path, 'wb') as f:
                f.write(data)
        except Exception:
            pass  # File may not exist yet (e.g., output path before conversion)

        return local_path

    def get_file_size(self, job_id: str, filename: str,
                      folder: str = "upload") -> int:
        key = self._key(job_id, filename, folder)
        response = self.s3.head_object(Bucket=self.bucket, Key=key)
        return response['ContentLength']

    def serve_download(self, job_id: str, filename: str,
                       folder: str = "output"):
        from flask import redirect
        url = self.generate_presigned_url(job_id, filename, folder)
        return redirect(url)

    def generate_presigned_url(self, job_id: str, filename: str,
                               folder: str = "output",
                               expiry: int = 3600) -> str:
        key = self._key(job_id, filename, folder)
        return self.s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': self.bucket, 'Key': key},
            ExpiresIn=expiry)

    def disk_usage(self) -> Optional[tuple[int, int, int]]:
        return None  # S3 has effectively unlimited storage

    def walk_job(self, job_id: str,
                 folder: str = "output") -> Iterator[tuple[str, list[str], list[str]]]:
        files = self.list_files(job_id, folder)
        if not files:
            return

        # Group files by directory for os.walk-like output
        dirs_map: dict[str, list[str]] = {}
        for f in files:
            dirname = os.path.dirname(f)
            if dirname not in dirs_map:
                dirs_map[dirname] = []
            dirs_map[dirname].append(os.path.basename(f))

        base = self._key(job_id, folder=folder)
        for dirname in sorted(dirs_map.keys()):
            root = os.path.join(base, dirname) if dirname else base.rstrip('/')
            subdirs = sorted(set(
                d.split('/')[0] for d in dirs_map
                if d and (not dirname or d.startswith(dirname + '/'))
                and d != dirname
            ))
            yield root, subdirs, dirs_map[dirname]

    def delete_subpath(self, job_id: str, subpath: str,
                       folder: str = "output") -> None:
        prefix = f"{self._prefix(folder)}/{job_id}/{subpath}/"
        while True:
            response = self.s3.list_objects_v2(
                Bucket=self.bucket, Prefix=prefix, MaxKeys=1000)
            contents = response.get('Contents', [])
            if not contents:
                break
            self.s3.delete_objects(
                Bucket=self.bucket,
                Delete={'Objects': [{'Key': obj['Key']} for obj in contents]})
            if not response.get('IsTruncated'):
                break

    def job_dir_exists(self, job_id: str, folder: str = "output") -> bool:
        return self.file_exists(job_id, "", folder)

    def ensure_directories(self) -> None:
        # S3 doesn't need directory creation
        pass


def create_storage_backend(settings) -> StorageBackend:
    """Factory function to create the appropriate storage backend."""
    backend = settings.storage_backend
    if backend == "s3":
        if not settings.s3_bucket:
            raise ValueError("S3_BUCKET is required when STORAGE_BACKEND=s3")
        logging.info(f"Using S3 storage backend: bucket={settings.s3_bucket}")
        return S3StorageBackend(settings)

    logging.info(f"Using local storage backend: "
                 f"upload={settings.upload_folder}, output={settings.output_folder}")
    return LocalStorageBackend(
        upload_folder=settings.upload_folder,
        output_folder=settings.output_folder,
    )
