"""Tests for download worker."""

import threading
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from s3ui.core.credentials import Profile
from s3ui.core.download_worker import DownloadWorker
from s3ui.core.s3_client import S3Client
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


def _create_transfer(db, bucket_id, key, local_path):
    cursor = db.execute(
        "INSERT INTO transfers (bucket_id, object_key, direction, local_path, status) "
        "VALUES (?, ?, 'download', ?, 'queued')",
        (bucket_id, key, str(local_path)),
    )
    return cursor.lastrowid


class TestSmallDownload:
    def test_local_file_matches(self, db, bucket_id, profile, tmp_path):
        with mock_aws():
            raw = boto3.client("s3", region_name="us-east-1")
            raw.create_bucket(Bucket="test-bucket")
            raw.put_object(Bucket="test-bucket", Key="small.txt", Body=b"hello world")
            client = S3Client(profile)

            dest = tmp_path / "downloads" / "small.txt"
            dest.parent.mkdir(parents=True)
            tid = _create_transfer(db, bucket_id, "small.txt", dest)

            worker = DownloadWorker(
                tid,
                client,
                db,
                "test-bucket",
                threading.Event(),
                threading.Event(),
            )

            finished = []
            worker.signals.finished.connect(lambda t: finished.append(t))
            worker.run()

            assert len(finished) == 1
            assert dest.read_text() == "hello world"


class TestRangedDownload:
    def test_large_file_download(self, db, bucket_id, profile, tmp_path):
        with mock_aws():
            raw = boto3.client("s3", region_name="us-east-1")
            raw.create_bucket(Bucket="test-bucket")
            data = b"x" * (9 * 1024 * 1024)
            raw.put_object(Bucket="test-bucket", Key="large.bin", Body=data)
            client = S3Client(profile)

            dest = tmp_path / "downloads" / "large.bin"
            dest.parent.mkdir(parents=True)
            tid = _create_transfer(db, bucket_id, "large.bin", dest)

            worker = DownloadWorker(
                tid,
                client,
                db,
                "test-bucket",
                threading.Event(),
                threading.Event(),
            )

            finished = []
            worker.signals.finished.connect(lambda t: finished.append(t))
            worker.run()

            assert len(finished) == 1
            assert dest.read_bytes() == data


class TestDownloadPause:
    def test_pause_preserves_temp(self, db, bucket_id, profile, tmp_path):
        with mock_aws():
            raw = boto3.client("s3", region_name="us-east-1")
            raw.create_bucket(Bucket="test-bucket")
            raw.put_object(Bucket="test-bucket", Key="pause.bin", Body=b"p" * (9 * 1024 * 1024))
            client = S3Client(profile)

            dest = tmp_path / "downloads" / "pause.bin"
            dest.parent.mkdir(parents=True)
            tid = _create_transfer(db, bucket_id, "pause.bin", dest)

            pause_evt = threading.Event()
            pause_evt.set()  # pause immediately

            worker = DownloadWorker(
                tid,
                client,
                db,
                "test-bucket",
                pause_evt,
                threading.Event(),
            )
            worker.run()

            row = db.fetchone("SELECT status FROM transfers WHERE id = ?", (tid,))
            assert row["status"] == "paused"


class TestDownloadCancel:
    def test_cancel_deletes_temp(self, db, bucket_id, profile, tmp_path):
        with mock_aws():
            raw = boto3.client("s3", region_name="us-east-1")
            raw.create_bucket(Bucket="test-bucket")
            raw.put_object(Bucket="test-bucket", Key="cancel.bin", Body=b"c" * (9 * 1024 * 1024))
            client = S3Client(profile)

            dest = tmp_path / "downloads" / "cancel.bin"
            dest.parent.mkdir(parents=True)
            tid = _create_transfer(db, bucket_id, "cancel.bin", dest)

            cancel_evt = threading.Event()
            cancel_evt.set()  # cancel immediately

            worker = DownloadWorker(
                tid,
                client,
                db,
                "test-bucket",
                threading.Event(),
                cancel_evt,
            )
            worker.run()

            row = db.fetchone("SELECT status FROM transfers WHERE id = ?", (tid,))
            assert row["status"] == "cancelled"
            # Temp file should be deleted
            temp = dest.parent / f".s3ui-download-{tid}.tmp"
            assert not temp.exists()


class TestDownloadFailure:
    def test_nonexistent_dest_dir(self, db, bucket_id, profile, tmp_path):
        with mock_aws():
            raw = boto3.client("s3", region_name="us-east-1")
            raw.create_bucket(Bucket="test-bucket")
            raw.put_object(Bucket="test-bucket", Key="file.txt", Body=b"data")
            client = S3Client(profile)

            dest = tmp_path / "no_such_dir" / "file.txt"
            tid = _create_transfer(db, bucket_id, "file.txt", dest)

            worker = DownloadWorker(
                tid,
                client,
                db,
                "test-bucket",
                threading.Event(),
                threading.Event(),
            )

            failed = []
            worker.signals.failed.connect(lambda t, m, d: failed.append(m))
            worker.run()

            assert len(failed) == 1
            assert "directory" in failed[0].lower()
