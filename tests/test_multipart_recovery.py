"""Failure-boundary tests verify the bytes published after multipart recovery."""

import math
import threading
from io import BytesIO
from unittest.mock import MagicMock

import pytest

from s3ui.core.download_worker import DownloadWorker
from s3ui.core.s3_client import S3ClientError
from s3ui.core.upload_worker import UploadWorker, select_part_size
from s3ui.db.database import Database
from s3ui.models.s3_objects import S3Item


@pytest.fixture
def transfer(tmp_path, monkeypatch):
    monkeypatch.setattr("s3ui.core.upload_worker.MULTIPART_THRESHOLD", 4)
    monkeypatch.setattr("s3ui.core.upload_worker.select_part_size", lambda size: 4)
    monkeypatch.setattr("s3ui.core.download_worker.MULTIPART_THRESHOLD", 4)
    monkeypatch.setattr("s3ui.core.download_worker.DEFAULT_PART_SIZE", 4)
    monkeypatch.setattr("s3ui.core.download_worker._backoff_delay", lambda attempt: 0)
    monkeypatch.setattr("s3ui.core.upload_worker._backoff_delay", lambda attempt: 0)
    db = Database(tmp_path / "test.db")
    bid = db.execute("INSERT INTO buckets (name, profile) VALUES ('bucket', 'profile')").lastrowid
    src = tmp_path / "file.bin"
    src.write_bytes(b"aaaabbbbcccc")
    tid = db.execute(
        "INSERT INTO transfers (bucket_id, object_key, direction, local_path, total_bytes, "
        "source_mtime_ns) VALUES (?, 'file.bin', 'upload', ?, 12, ?)",
        (bid, str(src), src.stat().st_mtime_ns),
    ).lastrowid
    yield db, tid, src
    db.close()


@pytest.fixture
def remote():
    client = MagicMock()
    parts = {}
    published = []
    client.create_multipart_upload.return_value = "new-session"
    client.list_parts.side_effect = lambda *args: [
        {"PartNumber": n, "Size": len(data), "ETag": f"etag-{n}"} for n, data in parts.items()
    ]

    def upload(bucket, key, uid, n, data):
        parts[n] = data.read()
        return f"etag-{n}"

    def complete(bucket, key, uid, manifest):
        published.append(b"".join(parts[p["PartNumber"]] for p in manifest))

    client.upload_part.side_effect = upload
    client.complete_multipart_upload.side_effect = complete
    client.abort_multipart_upload.side_effect = lambda *args: parts.clear()
    return client, parts, published


def worker(transfer, remote, pause=None, cancel=None):
    db, tid, _ = transfer
    return UploadWorker(
        tid, remote[0], db, "bucket", pause or threading.Event(), cancel or threading.Event()
    )


def status(transfer):
    return transfer[0].fetchone("SELECT * FROM transfers WHERE id = ?", (transfer[1],))["status"]


def resume_session(transfer):
    transfer[0].execute(
        "UPDATE transfers SET upload_id = 'old-session' WHERE id = ?", (transfer[1],)
    )


def test_reconstructs_missing_part_records(transfer, remote):
    resume_session(transfer)
    remote[1][1] = b"aaaa"
    worker(transfer, remote).run()
    assert remote[2] == [transfer[2].read_bytes()]
    assert [c.args[3] for c in remote[0].upload_part.call_args_list] == [2, 3]
    assert status(transfer) == "completed"


@pytest.mark.parametrize("server_part", [None, b"x"])
def test_reuploads_missing_or_wrong_sized_server_part(transfer, remote, server_part):
    resume_session(transfer)
    db, tid, src = transfer
    db.execute(
        "INSERT INTO transfer_parts (transfer_id, part_number, offset, size, etag, status) "
        "VALUES (?, 1, 0, 4, 'stale-etag', 'completed')",
        (tid,),
    )
    if server_part:
        remote[1][1] = server_part
    worker(transfer, remote).run()
    assert remote[2] == [src.read_bytes()]
    assert remote[0].upload_part.call_args_list[0].args[3] == 1


def test_expired_session_restarts(transfer, remote):
    resume_session(transfer)
    remote[0].list_parts.side_effect = S3ClientError("Expired", "", code="NoSuchUpload")
    worker(transfer, remote).run()
    assert remote[2] == [transfer[2].read_bytes()]
    assert remote[0].create_multipart_upload.call_count == 1


def test_permission_failure_does_not_restart_session(transfer, remote):
    resume_session(transfer)
    remote[0].list_parts.side_effect = S3ClientError("Denied", "", code="AccessDenied")
    worker(transfer, remote).run()
    assert status(transfer) == "failed"
    remote[0].create_multipart_upload.assert_not_called()
    assert remote[2] == []


@pytest.mark.parametrize("legacy", [False, True])
def test_changed_or_unidentified_source_restarts(transfer, remote, legacy):
    resume_session(transfer)
    remote[1][1] = b"OLD!"
    transfer[0].execute(
        "UPDATE transfers SET source_mtime_ns = ? WHERE id = ?",
        (None if legacy else 0, transfer[1]),
    )
    worker(transfer, remote).run()
    remote[0].abort_multipart_upload.assert_called_once_with("bucket", "file.bin", "old-session")
    assert remote[2] == [transfer[2].read_bytes()]


def test_source_changed_during_last_part_is_not_published(transfer, remote):
    upload = remote[0].upload_part.side_effect

    def change_source(*args):
        etag = upload(*args)
        if args[3] == 3:
            transfer[2].write_bytes(b"changed")
        return etag

    remote[0].upload_part.side_effect = change_source
    worker(transfer, remote).run()
    assert status(transfer) == "failed"
    assert remote[2] == []


@pytest.mark.parametrize("control", ["pause", "cancel"])
def test_stop_during_last_part_prevents_completion(transfer, remote, control):
    event = threading.Event()
    upload = remote[0].upload_part.side_effect

    def stop_after_upload(*args):
        etag = upload(*args)
        if args[3] == 3:
            event.set()
        return etag

    remote[0].upload_part.side_effect = stop_after_upload
    worker(transfer, remote, **{control: event}).run()
    assert remote[2] == []
    assert status(transfer) == ("paused" if control == "pause" else "cancelled")
    if control == "pause":
        # Resume with all parts already uploaded must complete without uploading again.
        remote[0].upload_part.reset_mock()
        worker(transfer, remote).run()
        remote[0].upload_part.assert_not_called()
        assert remote[2] == [transfer[2].read_bytes()]
    else:
        assert remote[1] == {}


def test_retry_recovers_part_accepted_before_response_was_lost(transfer, remote):
    upload = remote[0].upload_part.side_effect

    def lost_response(*args):
        upload(*args)
        raise TimeoutError("Response lost")

    remote[0].upload_part.side_effect = lost_response
    worker(transfer, remote).run()
    assert status(transfer) == "failed"
    remote[0].upload_part.side_effect = upload
    remote[0].upload_part.reset_mock()
    worker(transfer, remote).run()
    assert [c.args[3] for c in remote[0].upload_part.call_args_list] == [2, 3]
    assert remote[2] == [transfer[2].read_bytes()]


def test_part_size_covers_large_objects():
    size = 5 * 1024**4
    assert math.ceil(size / select_part_size(size)) <= 10_000
    assert select_part_size(size) <= 5 * 1024**3


def download(transfer, data=b"111122223333", old_etag='"old"', partial=b"OLD!"):
    db, tid, dest = transfer
    db.execute(
        "UPDATE transfers SET direction = 'download', source_etag = ? WHERE id = ?",
        (old_etag, tid),
    )
    temp = dest.parent / f".s3ui-download-{tid}.tmp"
    temp.write_bytes(partial)
    client = MagicMock()
    client.head_object.return_value = S3Item(
        name="file.bin", key="file.bin", is_prefix=False, size=len(data), etag='"new"'
    )
    bodies = []

    def get(bucket, key, range_header=None, *, etag=None):
        assert etag == '"new"'
        start, end = map(int, range_header.removeprefix("bytes=").split("-"))
        body = BytesIO(data[start : end + 1])
        bodies.append(body)
        return body

    client.get_object.side_effect = get
    w = DownloadWorker(tid, client, db, "bucket", threading.Event(), threading.Event())
    return w, client, bodies, temp


def test_download_changed_object_restarts_and_replaces_destination(transfer):
    w, client, bodies, temp = download(transfer)
    w.run()
    assert transfer[2].read_bytes() == b"111122223333"
    assert status(transfer) == "completed"
    assert client.get_object.call_args_list[0].args[2] == "bytes=0-3"
    assert all(b.closed for b in bodies)
    assert not temp.exists()


def test_download_same_object_resumes_at_existing_length(transfer):
    w, client, _, _ = download(transfer, old_etag='"new"', partial=b"1111")
    w.run()
    assert client.get_object.call_args_list[0].args[2] == "bytes=4-7"
    assert transfer[2].read_bytes() == b"111122223333"


@pytest.mark.parametrize("body", [b"", b"x", b"x" * 12])
def test_download_rejects_invalid_ranges_without_overwriting(transfer, body):
    original = transfer[2].read_bytes()
    w, client, _, _ = download(transfer)
    client.get_object.side_effect = lambda *args, **kwargs: BytesIO(body)
    w.run()
    assert status(transfer) == "failed"
    assert client.get_object.call_count == 3
    assert transfer[2].read_bytes() == original


def test_download_cancel_after_last_range_preserves_destination(transfer):
    original = transfer[2].read_bytes()
    w, client, _, temp = download(transfer)
    get = client.get_object.side_effect

    def cancel_last(*args, **kwargs):
        body = get(*args, **kwargs)
        if args[2] == "bytes=8-11":
            w._cancel.set()
        return body

    client.get_object.side_effect = cancel_last
    w.run()
    assert status(transfer) == "cancelled"
    assert transfer[2].read_bytes() == original
    assert not temp.exists()
