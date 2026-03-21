"""Tests for the storage abstraction layer (Epic 5)."""

import os
import sys
import pytest
from unittest.mock import MagicMock, patch

# Set test env before any imports
os.environ.setdefault('SECRET_KEY', 'test-secret-key')
os.environ.setdefault('ADMIN_API_SECRET', 'test-admin-secret')
os.environ.setdefault('FLASK_ENV', 'testing')
os.environ.setdefault('BUILD_GPU', 'false')

# Add shared to path
_tests_dir = os.path.dirname(__file__)
_root = os.path.abspath(os.path.join(_tests_dir, '..', '..'))
sys.path.insert(0, os.path.join(_root, 'shared'))

from storage import LocalStorageBackend, S3StorageBackend, create_storage_backend, StorageBackend


# ── LocalStorageBackend Tests ─────────────────────────────────────────────


class TestLocalStorageBackend:

    @pytest.fixture
    def storage(self, tmp_path):
        upload = tmp_path / "uploads"
        output = tmp_path / "outputs"
        upload.mkdir()
        output.mkdir()
        return LocalStorageBackend(
            upload_folder=str(upload),
            output_folder=str(output),
        )

    def test_save_and_read_file_bytes(self, storage):
        storage.save_file("job1", "test.txt", b"hello world", folder="output")
        result = storage.read_file("job1", "test.txt", folder="output")
        assert result == b"hello world"

    def test_save_file_stream(self, storage):
        import io
        stream = io.BytesIO(b"stream data")
        storage.save_file("job1", "stream.bin", stream, folder="upload")
        result = storage.read_file("job1", "stream.bin", folder="upload")
        assert result == b"stream data"

    def test_file_exists_true(self, storage):
        storage.save_file("job1", "f.txt", b"data", folder="output")
        assert storage.file_exists("job1", "f.txt", folder="output") is True

    def test_file_exists_false(self, storage):
        assert storage.file_exists("job1", "nope.txt", folder="output") is False

    def test_file_exists_dir(self, storage):
        storage.makedirs("job1", folder="output")
        assert storage.file_exists("job1", folder="output") is True

    def test_delete_job(self, storage):
        storage.save_file("job1", "a.txt", b"a", folder="upload")
        storage.save_file("job1", "b.txt", b"b", folder="output")
        storage.delete_job("job1")
        assert storage.file_exists("job1", "a.txt", folder="upload") is False
        assert storage.file_exists("job1", "b.txt", folder="output") is False

    def test_delete_job_nonexistent(self, storage):
        # Should not raise
        storage.delete_job("nonexistent")

    def test_list_files(self, storage):
        storage.save_file("job1", "a.txt", b"a", folder="output")
        storage.save_file("job1", "b.txt", b"b", folder="output")
        files = storage.list_files("job1", folder="output")
        assert files == ["a.txt", "b.txt"]

    def test_list_files_nested(self, storage):
        storage.makedirs("job1", "images", folder="output")
        storage.save_file("job1", "doc.md", b"md", folder="output")
        # Write an image into the images subdir
        img_path = os.path.join(
            storage.get_local_path("job1", folder="output"), "images", "img.png"
        )
        with open(img_path, "wb") as f:
            f.write(b"png")
        files = storage.list_files("job1", folder="output")
        assert "doc.md" in files
        assert os.path.join("images", "img.png") in files

    def test_list_files_empty(self, storage):
        assert storage.list_files("nonexistent", folder="output") == []

    def test_makedirs(self, storage):
        storage.makedirs("job1", "sub/nested", folder="output")
        path = os.path.join(
            storage.get_local_path("job1", folder="output"), "sub", "nested"
        )
        assert os.path.isdir(path)

    def test_get_local_path(self, storage):
        path = storage.get_local_path("job1", "file.txt", folder="upload")
        assert path.endswith(os.path.join("job1", "file.txt"))
        assert "uploads" in path

    def test_get_local_path_output(self, storage):
        path = storage.get_local_path("job1", "out.md", folder="output")
        assert "outputs" in path

    def test_get_file_size(self, storage):
        storage.save_file("job1", "sized.bin", b"x" * 42, folder="upload")
        assert storage.get_file_size("job1", "sized.bin", folder="upload") == 42

    def test_disk_usage(self, storage):
        usage = storage.disk_usage()
        assert usage is not None
        total, used, free = usage
        assert total > 0
        assert free >= 0

    def test_walk_job(self, storage):
        storage.save_file("job1", "a.txt", b"a", folder="output")
        storage.makedirs("job1", "sub", folder="output")
        sub_path = os.path.join(
            storage.get_local_path("job1", folder="output"), "sub", "b.txt"
        )
        with open(sub_path, "wb") as f:
            f.write(b"b")

        walked = list(storage.walk_job("job1", folder="output"))
        assert len(walked) >= 2  # root dir + sub dir
        all_files = []
        for root, dirs, files in walked:
            all_files.extend(files)
        assert "a.txt" in all_files
        assert "b.txt" in all_files

    def test_walk_job_nonexistent(self, storage):
        assert list(storage.walk_job("nope", folder="output")) == []

    def test_delete_subpath(self, storage):
        storage.makedirs("job1", "batches/batch_0", folder="output")
        batch_path = os.path.join(
            storage.get_local_path("job1", folder="output"),
            "batches", "batch_0", "data.txt"
        )
        os.makedirs(os.path.dirname(batch_path), exist_ok=True)
        with open(batch_path, "w") as f:
            f.write("test")
        storage.delete_subpath("job1", "batches", folder="output")
        assert not os.path.exists(
            os.path.join(storage.get_local_path("job1", folder="output"), "batches")
        )

    def test_job_dir_exists(self, storage):
        assert storage.job_dir_exists("job1", folder="output") is False
        storage.makedirs("job1", folder="output")
        assert storage.job_dir_exists("job1", folder="output") is True

    def test_ensure_directories(self, tmp_path):
        upload = str(tmp_path / "new_uploads")
        output = str(tmp_path / "new_outputs")
        s = LocalStorageBackend(upload_folder=upload, output_folder=output)
        s.ensure_directories()
        assert os.path.isdir(upload)
        assert os.path.isdir(output)

    def test_serve_download(self, storage):
        """Test serve_download returns a Flask response (requires app context)."""
        from flask import Flask
        app = Flask(__name__)
        storage.save_file("job1", "result.txt", b"content", folder="output")
        with app.test_request_context():
            response = storage.serve_download("job1", "result.txt", folder="output")
            assert response.status_code == 200

    def test_implements_protocol(self, storage):
        assert isinstance(storage, StorageBackend)


# ── Factory Tests ─────────────────────────────────────────────────────────


class TestCreateStorageBackend:

    def test_default_local(self):
        settings = MagicMock()
        settings.storage_backend = "local"
        settings.upload_folder = "/tmp/up"
        settings.output_folder = "/tmp/out"
        backend = create_storage_backend(settings)
        assert isinstance(backend, LocalStorageBackend)
        assert backend.upload_folder == "/tmp/up"
        assert backend.output_folder == "/tmp/out"

    def test_s3_requires_bucket(self):
        settings = MagicMock()
        settings.storage_backend = "s3"
        settings.s3_bucket = None
        with pytest.raises(ValueError, match="S3_BUCKET is required"):
            create_storage_backend(settings)

    def test_s3_backend_created(self):
        """Test S3 backend creation (requires boto3)."""
        pytest.importorskip("boto3")
        try:
            from moto import mock_aws
        except ImportError:
            pytest.skip("moto not installed")

        with mock_aws():
            import boto3
            boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="test-bucket")

            settings = MagicMock()
            settings.storage_backend = "s3"
            settings.s3_bucket = "test-bucket"
            settings.s3_endpoint_url = None
            settings.s3_access_key = None
            settings.s3_secret_key = None
            settings.s3_region = "us-east-1"
            settings.s3_sse_algorithm = None

            backend = create_storage_backend(settings)
            assert isinstance(backend, S3StorageBackend)


# ── S3StorageBackend Tests ────────────────────────────────────────────────


class TestS3StorageBackend:

    @pytest.fixture
    def s3_storage(self):
        pytest.importorskip("boto3")
        try:
            from moto import mock_aws
        except ImportError:
            pytest.skip("moto not installed")

        with mock_aws():
            import boto3
            boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="test-bucket")

            settings = MagicMock()
            settings.s3_bucket = "test-bucket"
            settings.s3_endpoint_url = None
            settings.s3_access_key = None
            settings.s3_secret_key = None
            settings.s3_region = "us-east-1"
            settings.s3_sse_algorithm = None

            yield S3StorageBackend(settings)

    def test_save_and_read_file(self, s3_storage):
        s3_storage.save_file("job1", "test.txt", b"hello s3", folder="output")
        result = s3_storage.read_file("job1", "test.txt", folder="output")
        assert result == b"hello s3"

    def test_file_exists(self, s3_storage):
        assert s3_storage.file_exists("job1", "nope.txt", folder="output") is False
        s3_storage.save_file("job1", "exists.txt", b"yes", folder="output")
        assert s3_storage.file_exists("job1", "exists.txt", folder="output") is True

    def test_delete_job(self, s3_storage):
        s3_storage.save_file("job1", "a.txt", b"a", folder="upload")
        s3_storage.save_file("job1", "b.txt", b"b", folder="output")
        s3_storage.delete_job("job1")
        assert s3_storage.file_exists("job1", "a.txt", folder="upload") is False
        assert s3_storage.file_exists("job1", "b.txt", folder="output") is False

    def test_list_files(self, s3_storage):
        s3_storage.save_file("job1", "a.txt", b"a", folder="output")
        s3_storage.save_file("job1", "b.txt", b"b", folder="output")
        files = s3_storage.list_files("job1", folder="output")
        assert sorted(files) == ["a.txt", "b.txt"]

    def test_list_files_nested(self, s3_storage):
        s3_storage.save_file("job1", "doc.md", b"md", folder="output")
        s3_storage.save_file("job1", "images/img.png", b"png", folder="output")
        files = s3_storage.list_files("job1", folder="output")
        assert "doc.md" in files
        assert "images/img.png" in files

    def test_makedirs_noop(self, s3_storage):
        # Should not raise
        s3_storage.makedirs("job1", "sub/dir", folder="output")

    def test_get_file_size(self, s3_storage):
        s3_storage.save_file("job1", "sized.bin", b"x" * 100, folder="upload")
        assert s3_storage.get_file_size("job1", "sized.bin", folder="upload") == 100

    def test_disk_usage_returns_none(self, s3_storage):
        assert s3_storage.disk_usage() is None

    def test_serve_download_redirects(self, s3_storage):
        from flask import Flask
        app = Flask(__name__)
        s3_storage.save_file("job1", "result.txt", b"content", folder="output")
        with app.test_request_context():
            response = s3_storage.serve_download("job1", "result.txt", folder="output")
            assert response.status_code == 302
            assert "test-bucket" in response.location or "X-Amz" in response.location

    def test_presigned_url(self, s3_storage):
        s3_storage.save_file("job1", "file.txt", b"data", folder="output")
        url = s3_storage.generate_presigned_url("job1", "file.txt", folder="output")
        assert "X-Amz" in url or "Signature" in url

    def test_sse_headers(self):
        pytest.importorskip("boto3")
        try:
            from moto import mock_aws
        except ImportError:
            pytest.skip("moto not installed")

        with mock_aws():
            import boto3
            boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="sse-bucket")

            settings = MagicMock()
            settings.s3_bucket = "sse-bucket"
            settings.s3_endpoint_url = None
            settings.s3_access_key = None
            settings.s3_secret_key = None
            settings.s3_region = "us-east-1"
            settings.s3_sse_algorithm = "AES256"

            storage = S3StorageBackend(settings)
            storage.save_file("job1", "enc.txt", b"encrypted", folder="output")
            # Verify file was saved (SSE is transparent with moto)
            assert storage.read_file("job1", "enc.txt", folder="output") == b"encrypted"

    def test_delete_subpath(self, s3_storage):
        s3_storage.save_file("job1", "batches/batch_0/data.txt", b"d", folder="output")
        s3_storage.save_file("job1", "keep.txt", b"k", folder="output")
        s3_storage.delete_subpath("job1", "batches", folder="output")
        assert s3_storage.file_exists("job1", "keep.txt", folder="output") is True
        assert s3_storage.file_exists("job1", "batches/batch_0/data.txt", folder="output") is False

    def test_job_dir_exists(self, s3_storage):
        assert s3_storage.job_dir_exists("job1", folder="output") is False
        s3_storage.save_file("job1", "file.txt", b"x", folder="output")
        assert s3_storage.job_dir_exists("job1", folder="output") is True

    def test_ensure_directories_noop(self, s3_storage):
        s3_storage.ensure_directories()

    def test_get_local_path_downloads(self, s3_storage):
        s3_storage.save_file("job1", "remote.txt", b"remote data", folder="upload")
        local_path = s3_storage.get_local_path("job1", "remote.txt", folder="upload")
        assert os.path.exists(local_path)
        with open(local_path, "rb") as f:
            assert f.read() == b"remote data"
