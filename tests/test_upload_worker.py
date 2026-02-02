"""Tests for upload worker."""

import threading
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from s3ui.core.credentials import Profile
from s3ui.core.s3_client import S3Client
from s3ui.core.upload_worker import UploadWorker, select_part_size
from s3ui.db.database import Database


@pytest.fixture
def db(tmp_path: Path) -> Database:
    d = Database(tmp_path / "test.db")
    yield d
    d.close()


@pytest.fixture
def bucket_id(db: Database) -> int:
    cursor = db.execute(
        "INSERT INTO buckets (name, region, profile) VALUES (?, ?, ?)",
        ("test-bucket", "us-east-1", "test"),
    )
    return cursor.lastrowid


@pytest.fixture
def profile() -> Profile:
    return Profile("test", "testing", "testing", "us-east-1")


def _create_transfer(db, bucket_id, key, local_path, direction="upload"):
    cursor = db.execute(
        "INSERT INTO transfers (bucket_id, object_key, direction, local_path, status) "
        "VALUES (?, ?, ?, ?, 'queued')",
        (bucket_id, key, direction, str(local_path)),
    )
    return cursor.lastrowid


def _run_worker(worker):
    """Run an upload worker synchronously."""
    worker.run()


class TestPartSizeSelection:
    def test_small_file(self):
        assert select_part_size(1 * 1024 * 1024) == 8 * 1024 * 1024

    def test_10gb_file(self):
        assert select_part_size(10 * 1024**3) == 8 * 1024 * 1024

    def test_100gb_file(self):
        assert select_part_size(100 * 1024**3) == 64 * 1024 * 1024

    def test_1tb_file(self):
        assert select_part_size(1024**4) == 512 * 1024 * 1024


class TestSmallUpload:
    def test_object_appears_on_s3(self, db, bucket_id, profile, tmp_path):
        with mock_aws():
            raw = boto3.client("s3", region_name="us-east-1")
            raw.create_bucket(Bucket="test-bucket")
            client = S3Client(profile)

            # Create local file
            src = tmp_path / "small.txt"
            src.write_text("hello world")

            tid = _create_transfer(db, bucket_id, "small.txt", src)
            worker = UploadWorker(
                tid,
                client,
                db,
                "test-bucket",
                threading.Event(),
                threading.Event(),
            )

            finished = []
            worker.signals.finished.connect(lambda t: finished.append(t))
            _run_worker(worker)

            assert len(finished) == 1
            body = raw.get_object(Bucket="test-bucket", Key="small.txt")["Body"].read()
            assert body == b"hello world"


class TestMultipartUpload:
    def test_large_file_upload(self, db, bucket_id, profile, tmp_path):
        with mock_aws():
            raw = boto3.client("s3", region_name="us-east-1")
            raw.create_bucket(Bucket="test-bucket")
            client = S3Client(profile)

            # Create file >8 MB
            src = tmp_path / "large.bin"
            data = b"x" * (9 * 1024 * 1024)
            src.write_bytes(data)

            tid = _create_transfer(db, bucket_id, "large.bin", src)
            worker = UploadWorker(
                tid,
                client,
                db,
                "test-bucket",
                threading.Event(),
                threading.Event(),
            )

            finished = []
            worker.signals.finished.connect(lambda t: finished.append(t))
            _run_worker(worker)

            assert len(finished) == 1
            item = client.head_object("test-bucket", "large.bin")
            assert item.size == len(data)


class TestUploadPause:
    def test_pause_stops_worker(self, db, bucket_id, profile, tmp_path):
        with mock_aws():
            raw = boto3.client("s3", region_name="us-east-1")
            raw.create_bucket(Bucket="test-bucket")
            client = S3Client(profile)

            src = tmp_path / "pause.bin"
            src.write_bytes(b"y" * (9 * 1024 * 1024))

            tid = _create_transfer(db, bucket_id, "pause.bin", src)
            pause_evt = threading.Event()
            pause_evt.set()  # pre-set to pause immediately

            worker = UploadWorker(
                tid,
                client,
                db,
                "test-bucket",
                pause_evt,
                threading.Event(),
            )
            _run_worker(worker)

            row = db.fetchone("SELECT status FROM transfers WHERE id = ?", (tid,))
            assert row["status"] == "paused"


class TestUploadCancel:
    def test_cancel_aborts_multipart(self, db, bucket_id, profile, tmp_path):
        with mock_aws():
            raw = boto3.client("s3", region_name="us-east-1")
            raw.create_bucket(Bucket="test-bucket")
            client = S3Client(profile)

            src = tmp_path / "cancel.bin"
            src.write_bytes(b"z" * (9 * 1024 * 1024))

            tid = _create_transfer(db, bucket_id, "cancel.bin", src)
            cancel_evt = threading.Event()
            cancel_evt.set()  # pre-set to cancel immediately

            worker = UploadWorker(
                tid,
                client,
                db,
                "test-bucket",
                threading.Event(),
                cancel_evt,
            )
            _run_worker(worker)

            row = db.fetchone("SELECT status FROM transfers WHERE id = ?", (tid,))
            assert row["status"] == "cancelled"


class TestUploadFailure:
    def test_missing_source_file(self, db, bucket_id, profile, tmp_path):
        with mock_aws():
            boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="test-bucket")
            client = S3Client(profile)

            tid = _create_transfer(db, bucket_id, "gone.txt", tmp_path / "nonexistent.txt")
            worker = UploadWorker(
                tid,
                client,
                db,
                "test-bucket",
                threading.Event(),
                threading.Event(),
            )

            failed = []
            worker.signals.failed.connect(lambda t, m, d: failed.append(m))
            _run_worker(worker)

            assert len(failed) == 1
            assert "no longer exists" in failed[0]

            row = db.fetchone("SELECT status FROM transfers WHERE id = ?", (tid,))
            assert row["status"] == "failed"
