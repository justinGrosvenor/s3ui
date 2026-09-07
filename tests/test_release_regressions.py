"""Release safety and event-loop regressions with deterministic worker barriers."""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QEventLoop, QTimer

from s3ui.core.listing_cache import ListingCache
from s3ui.core.s3_client import S3ClientError
from s3ui.core.transfers import TransferEngine
from s3ui.db.database import Database
from s3ui.main_window import MainWindow
from s3ui.models.s3_objects import S3Item
from s3ui.ui.s3_pane import S3PaneWidget


@pytest.fixture
def db(tmp_path):
    db = Database(tmp_path / "test.db")
    yield db
    db.close()


def bucket(db, name="bucket", profile="profile"):
    return db.execute(
        "INSERT INTO buckets (name, profile) VALUES (?, ?)", (name, profile)
    ).lastrowid


def transfer(db, bid, path, status="queued"):
    return db.execute(
        "INSERT INTO transfers (bucket_id, object_key, direction, local_path, status) "
        "VALUES (?, 'file.txt', 'upload', ?, ?)",
        (bid, str(path), status),
    ).lastrowid


def item(key):
    return S3Item(name=key.rsplit("/", 1)[-1], key=key, is_prefix=False, size=1)


def test_all_transfer_entry_points_reject_other_profile(db, tmp_path, qtbot):
    bucket(db)
    other = bucket(db, profile="other")
    tid = transfer(db, other, tmp_path / "file")
    engine = TransferEngine(MagicMock(), db, "bucket", profile="profile")
    for action in ("enqueue", "resume", "retry", "pause", "cancel"):
        getattr(engine, action)(tid)
        assert (
            db.fetchone("SELECT status FROM transfers WHERE id = ?", (tid,))["status"] == "queued"
        )
        assert not engine.owns(tid)


def test_paused_transfers_stay_paused_on_restore(db, tmp_path, qtbot):
    bid = bucket(db)
    src = tmp_path / "file"
    src.write_bytes(b"x")
    tid = transfer(db, bid, src, status="paused")
    client = MagicMock()
    engine = TransferEngine(client, db, "bucket", profile="profile")
    engine.restore_pending()
    client.put_object.assert_not_called()
    assert db.fetchone("SELECT status FROM transfers WHERE id = ?", (tid,))["status"] == "paused"


def test_resume_all_is_scoped_to_profile(db, tmp_path, qtbot):
    own = bucket(db)
    other = bucket(db, profile="other")
    tid = transfer(db, own, tmp_path / "own", "paused")
    other_tid = transfer(db, other, tmp_path / "other", "paused")
    engine = TransferEngine(MagicMock(), db, "bucket", profile="profile")
    with patch.object(engine, "enqueue") as enqueue:
        engine.resume_all()
    enqueue.assert_any_call(tid)
    assert (
        db.fetchone("SELECT status FROM transfers WHERE id = ?", (other_tid,))["status"] == "paused"
    )


def test_pause_resume_waits_for_previous_worker_to_exit(db, tmp_path, qtbot, monkeypatch):
    from s3ui.core.upload_worker import UploadWorker

    bid = bucket(db)
    src = tmp_path / "file"
    src.write_bytes(b"x")
    tid = transfer(db, bid, src)
    entered, release = threading.Event(), threading.Event()
    original = UploadWorker._do_upload
    runs = []

    def blocked(worker):
        runs.append(worker)
        if len(runs) == 1:
            entered.set()
            assert release.wait(5)
        original(worker)

    monkeypatch.setattr(UploadWorker, "_do_upload", blocked)
    client = MagicMock()
    engine = TransferEngine(client, db, "bucket", profile="profile")
    finished = []
    engine.transfer_finished.connect(finished.append)
    engine.enqueue(tid)
    try:
        qtbot.waitUntil(entered.is_set)
        engine.pause(tid)
        engine.resume(tid)
        assert len(runs) == 1
    finally:
        release.set()
    qtbot.waitUntil(lambda: finished == [tid], timeout=5000)
    assert len(runs) == 2
    client.put_object.assert_called_once_with("bucket", "file.txt", b"x")


def test_pending_work_stays_controllable_outside_threadpool(db, tmp_path, qtbot):
    bid = bucket(db)
    source = tmp_path / "file"
    source.write_bytes(b"x")
    tids = [transfer(db, bid, source) for _ in range(3)]
    entered, release = threading.Event(), threading.Event()
    client = MagicMock()

    def blocked(*args):
        entered.set()
        assert release.wait(5)

    client.put_object.side_effect = blocked
    engine = TransferEngine(client, db, "bucket", max_workers=1, profile="profile")
    try:
        for tid in tids:
            engine.enqueue(tid)
        qtbot.waitUntil(entered.is_set)
        assert len(engine._active) == 1
        engine.cancel(tids[1])
        engine.pause(tids[2])
    finally:
        release.set()
    qtbot.waitUntil(lambda: not engine._active)
    assert client.put_object.call_count == 1
    statuses = [r["status"] for r in db.fetchall("SELECT status FROM transfers ORDER BY id")]
    assert statuses == ["completed", "cancelled", "paused"]


def test_window_keeps_original_engine_for_paused_transfer(db, tmp_path, qtbot):
    first = bucket(db, "first")
    bucket(db, "second")
    tid = transfer(db, first, tmp_path / "file", "paused")
    window = MainWindow(db, auto_connect=False)
    qtbot.addWidget(window)
    one = TransferEngine(MagicMock(), db, "first", profile="profile")
    two = TransferEngine(MagicMock(), db, "second", profile="profile")
    window.set_transfer_engine(one)
    window.set_transfer_engine(two)
    with patch.object(one, "resume") as correct, patch.object(two, "resume") as wrong:
        window._on_resume_transfer(tid)
    correct.assert_called_once_with(tid)
    wrong.assert_not_called()


def test_cached_navigation_rejects_previous_folder_result(qtbot):
    pane = S3PaneWidget()
    qtbot.addWidget(pane)
    pane.set_client(MagicMock())
    pane._bucket = "bucket"
    pane._current_prefix = "slow/"
    old_id = pane._fetch_id
    pane._cache.put("cached/", [item("cached/right")])
    pane.navigate_to("cached/")
    pane._on_listing_complete("slow/", [item("slow/wrong")], old_id)
    assert pane._model.get_item(0).key == "cached/right"


def test_old_bucket_results_never_enter_new_cache(qtbot):
    pane = S3PaneWidget()
    qtbot.addWidget(pane)
    pane.set_client(MagicMock())
    old_id = pane._fetch_id
    pane.set_client(MagicMock())
    pane._on_listing_complete("", [item("wrong")], old_id)
    pane._on_revalidation_complete("", [item("wrong")], old_id, 0)
    assert pane._cache.get("") is None
    assert pane._model.item_count() == 0


def test_overwrite_updates_one_listing_row(qtbot):
    pane = S3PaneWidget()
    qtbot.addWidget(pane)
    pane._on_listing_complete("", [item("file")], pane._fetch_id)
    pane.notify_upload_complete("file", 123)
    assert pane._model.item_count() == 1
    assert pane._model.get_item(0).size == 123
    assert len(pane._cache.get("").items) == 1


def test_stale_revalidation_does_not_resurrect_deleted_objects():
    cache = ListingCache()
    cache.put("", [item("deleted"), item("kept")])
    cache.apply_mutation("", lambda items: items.pop(0))
    assert not cache.safe_revalidate("", [item("deleted"), item("kept")], 0)
    assert [i.key for i in cache.get("").items] == ["kept"]


def test_permission_error_is_not_an_available_copy_destination():
    client = MagicMock()
    client.head_object.side_effect = S3ClientError("Denied", "", code="AccessDenied")
    with pytest.raises(S3ClientError):
        MainWindow._free_dest_key(client, "bucket", "", "file")
    client.head_object.side_effect = S3ClientError("Missing", "", code="404")
    assert MainWindow._free_dest_key(client, "bucket", "", "file") == "file"


def test_folder_download_rejects_symlink_escape(tmp_path):
    dest, outside = tmp_path / "dest", tmp_path / "outside"
    dest.mkdir()
    outside.mkdir()
    try:
        (dest / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks requires permission on this platform")
    assert MainWindow._safe_local_path(dest, "link/file") is None
    assert MainWindow._safe_local_path(dest, "normal/file") == dest / "normal/file"


def test_folder_creation_and_shutdown_leave_event_loop_responsive(db, qtbot):
    window = MainWindow(db, auto_connect=False)
    qtbot.addWidget(window)
    client = MagicMock()
    window._s3_client = client
    window._bucket_combo.blockSignals(True)
    window._bucket_combo.addItem("bucket", "bucket")
    window._bucket_combo.blockSignals(False)
    entered, release = threading.Event(), threading.Event()

    def slow_put(*args):
        entered.set()
        assert release.wait(5)

    client.put_object.side_effect = slow_put
    try:
        with patch("s3ui.main_window.QInputDialog.getText", return_value=("folder", True)):
            window._on_new_folder_requested()
        qtbot.waitUntil(entered.is_set)
        ticks = []
        timer = QTimer()
        timer.setInterval(5)
        timer.timeout.connect(lambda: ticks.append(time.monotonic()))
        timer.start()
        loop = QEventLoop()
        QTimer.singleShot(60, loop.quit)
        loop.exec()
        assert len(ticks) >= 3
        assert not window.close()  # Deferred while the request is still running.
        loop = QEventLoop()
        QTimer.singleShot(60, loop.quit)
        before = len(ticks)
        loop.exec()
        assert len(ticks) >= before + 3
        timer.stop()
    finally:
        release.set()
    qtbot.waitUntil(lambda: not window._bg_workers)


def test_unknown_multipart_sessions_are_never_aborted(db, qtbot):
    bucket(db)
    client = MagicMock()
    engine = TransferEngine(client, db, "bucket", profile="profile")
    assert engine.cleanup_orphaned_uploads() == 0
    client.list_multipart_uploads.assert_not_called()
    client.abort_multipart_upload.assert_not_called()


def test_cancelled_owned_upload_is_cleaned_up(db, tmp_path, qtbot):
    bid = bucket(db)
    tid = transfer(db, bid, tmp_path / "file", "cancelled")
    db.execute("UPDATE transfers SET upload_id = 'owned' WHERE id = ?", (tid,))
    client = MagicMock()
    engine = TransferEngine(client, db, "bucket", profile="profile")
    assert engine.cleanup_orphaned_uploads() == 1
    client.abort_multipart_upload.assert_called_once_with("bucket", "file.txt", "owned")
    assert db.fetchone("SELECT upload_id FROM transfers WHERE id = ?", (tid,))["upload_id"] is None


def test_batch_transaction_rolls_back_all_writes(db):
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        db.execute_batch(
            [
                ("INSERT INTO buckets (name, profile) VALUES ('one', 'profile')", ()),
                ("INSERT INTO buckets (name, profile) VALUES ('one', 'profile')", ()),
            ]
        )
    assert db.fetchall("SELECT * FROM buckets") == []


def test_v1_database_upgrade_preserves_resumable_parts(tmp_path):
    import sqlite3

    from s3ui.db.database import MIGRATIONS_DIR

    path = tmp_path / "v1.db"
    conn = sqlite3.connect(path)
    conn.executescript((MIGRATIONS_DIR / "001_initial.sql").read_text())
    conn.execute("INSERT INTO schema_version (version) VALUES (1)")
    conn.execute("INSERT INTO buckets (id, name, profile) VALUES (1, 'bucket', 'profile')")
    conn.execute(
        "INSERT INTO transfers (id, bucket_id, object_key, direction, local_path, upload_id) "
        "VALUES (1, 1, 'file', 'upload', '/file', 'resume-me')"
    )
    conn.execute(
        "INSERT INTO transfer_parts (transfer_id, part_number, offset, size, etag, status) "
        "VALUES (1, 1, 0, 8, 'etag', 'completed')"
    )
    conn.commit()
    conn.close()
    upgraded = Database(path)
    try:
        row = upgraded.fetchone("SELECT * FROM transfers WHERE id = 1")
        assert row["upload_id"] == "resume-me"
        assert row["source_mtime_ns"] is None
        assert upgraded.fetchone("SELECT etag FROM transfer_parts")["etag"] == "etag"
        assert upgraded.fetchone("SELECT MAX(version) AS v FROM schema_version")["v"] == 2
    finally:
        upgraded.close()


def test_stats_snapshot_uses_actual_schema_and_selected_profile(db, qtbot):
    from s3ui.core.stats import StatsCollector

    bucket(db)
    selected = bucket(db, profile="selected")
    client = MagicMock()
    client._client.get_paginator.return_value.paginate.return_value = [
        {
            "Contents": [
                {"Key": "standard", "Size": 5, "StorageClass": "STANDARD"},
                {"Key": "cold", "Size": 9, "StorageClass": "DEEP_ARCHIVE"},
            ]
        },
    ]
    completed, errors = [], []
    scan = StatsCollector(client, "bucket", db, bucket_id=selected)
    scan.signals.complete.connect(completed.append)
    scan.signals.error.connect(errors.append)
    scan.run()
    scan.run()  # Same-day rescan updates rather than violating the unique constraint.
    assert not errors
    assert len(completed) == 2
    rows = db.fetchall("SELECT * FROM bucket_snapshots")
    assert len(rows) == 1
    assert rows[0]["bucket_id"] == selected
    assert rows[0]["total_bytes"] == 14
    assert rows[0]["deep_archive_bytes"] == 9


def test_upload_discovery_runs_off_gui_in_small_batches(db, tmp_path, qtbot, monkeypatch):
    from pathlib import Path

    from PyQt6.QtCore import QThread
    from PyQt6.QtWidgets import QApplication

    from s3ui.core.upload_batch import UploadBatchWorker

    bid = bucket(db)
    root = tmp_path / "folder"
    root.mkdir()
    for n in range(121):
        (root / f"{n}.txt").write_bytes(b"x")
    original = Path.rglob
    discovered_on = []

    def tracked_walk(path, pattern):
        discovered_on.append(QThread.currentThread() is QApplication.instance().thread())
        yield from original(path, pattern)

    monkeypatch.setattr(Path, "rglob", tracked_walk)
    worker = UploadBatchWorker(db, bid, "prefix/", [str(root)])
    batches, errors = [], []
    worker.batch_ready.connect(batches.append)
    worker.failed.connect(errors.append)
    with qtbot.waitSignal(worker.finished):
        worker.start()
    worker.wait()
    assert not errors
    assert discovered_on == [False]
    assert [len(batch) for batch in batches] == [50, 50, 21]
    rows = db.fetchall("SELECT object_key FROM transfers")
    assert len(rows) == 121
    assert all(row["object_key"].startswith("prefix/folder/") for row in rows)


def test_model_rearms_after_failure_without_polling_idle_queue(db, tmp_path, qtbot):
    from s3ui.models.transfer_model import TransferModel

    bid = bucket(db)
    tid = transfer(db, bid, tmp_path / "file")
    model = TransferModel(db)
    model.add_transfer(tid)
    model.on_error(tid, "retry me", "")
    qtbot.waitUntil(lambda: model.get_transfer_row(tid).status == "failed")
    assert not model._timer.isActive()
    model.on_status_changed(tid, "queued")
    qtbot.waitUntil(lambda: model.get_transfer_row(tid).status == "queued")
    assert not model._timer.isActive()


def test_pause_all_is_one_commit_and_covers_all_buckets(db, tmp_path, qtbot):
    from s3ui.ui.transfer_panel import TransferPanelWidget

    first = bucket(db, "first")
    second = bucket(db, "second")
    for bid in (first, second):
        for _ in range(5):
            transfer(db, bid, tmp_path / "file")
    one = TransferEngine(MagicMock(), db, "first", profile="profile")
    two = TransferEngine(MagicMock(), db, "second", profile="profile")
    panel = TransferPanelWidget(db)
    qtbot.addWidget(panel)
    panel.set_engine(one)
    panel.set_engine(two)
    commits = []
    db._get_conn().set_trace_callback(lambda sql: commits.append(sql) if sql == "COMMIT" else None)
    panel._on_pause_all()
    db._get_conn().set_trace_callback(None)
    assert len(commits) == 2  # One per bucket, independent of queue length.
    assert {r["status"] for r in db.fetchall("SELECT status FROM transfers")} == {"paused"}


def test_download_does_not_race_an_existing_transfer_to_same_path(db, tmp_path, qtbot):
    from s3ui.ui.name_conflict import ConflictResolution

    bid = bucket(db)
    dest = tmp_path / "file"
    tid = transfer(db, bid, dest)
    db.execute("UPDATE transfers SET direction = 'download' WHERE id = ?", (tid,))
    window = MainWindow(db, auto_connect=False)
    qtbot.addWidget(window)
    assert window._resolve_local_conflict(dest, {"apply_all": ConflictResolution.REPLACE}) is None
    assert not dest.exists()


def test_cancelling_paused_upload_aborts_without_reconnecting(db, tmp_path, qtbot):
    bid = bucket(db)
    tid = transfer(db, bid, tmp_path / "file", "paused")
    db.execute("UPDATE transfers SET upload_id = 'paused-session' WHERE id = ?", (tid,))
    client = MagicMock()
    engine = TransferEngine(client, db, "bucket", profile="profile")
    engine.cancel(tid)
    qtbot.waitUntil(
        lambda: db.fetchone("SELECT upload_id FROM transfers WHERE id = ?", (tid,))["upload_id"]
        is None
    )
    assert engine._pool.waitForDone(5000)
    client.abort_multipart_upload.assert_called_once_with("bucket", "file.txt", "paused-session")
