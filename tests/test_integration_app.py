"""End-to-end integration: drive the assembled MainWindow against mock S3.

Unlike the focused unit tests, this exercises the real window, engine, and
worker threads wired together against a moto backend (the same boto3 path the
app uses for MinIO/AWS) — the smoke test that would have caught the large-queue
freeze before release. Counts are kept small; correctness, not throughput, is
what this asserts. Timing/profiling lives outside CI.
"""

from __future__ import annotations

import contextlib

import boto3
import pytest
from moto import mock_aws
from PyQt6.QtWidgets import QInputDialog, QMessageBox

from s3ui.core.credentials import Profile
from s3ui.core.s3_client import S3Client
from s3ui.core.transfers import TransferEngine
from s3ui.db.database import Database
from s3ui.main_window import MainWindow
from s3ui.models.s3_objects import S3Item

BUCKET = "it-bucket"


@pytest.fixture
def app_window(qtbot, tmp_path, monkeypatch):
    """A connected MainWindow talking to an in-process mock S3 backend."""
    # Handlers that would otherwise block on a modal dialog.
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: QMessageBox.StandardButton.Ok)

    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        db = Database(tmp_path / "s3ui.db")
        window = MainWindow(db=db, auto_connect=False)
        qtbot.addWidget(window)

        client = S3Client(Profile("demo", "test", "test", "us-east-1"))
        window._on_connected(client, [BUCKET])
        qtbot.waitUntil(lambda: window._transfer_engine is not None, timeout=5000)

        s3 = boto3.client("s3", region_name="us-east-1")
        try:
            yield window, db, s3, tmp_path
        finally:
            # Drain workers before the mock backend tears down, so no request
            # escapes to a real endpoint.
            window._closing = True
            for worker in list(window._bg_workers):
                worker.requestInterruption()
            engines = [window._transfer_engine, *window._retired_engines]
            for engine in engines:
                if isinstance(engine, TransferEngine):
                    engine.pause_all()
                    engine.shutdown()
            with contextlib.suppress(Exception):
                qtbot.waitUntil(
                    lambda: all(
                        not isinstance(e, TransferEngine) or e._pool.activeThreadCount() == 0
                        for e in engines
                    ),
                    timeout=10000,
                )
            db.close()


def _keys(s3) -> set[str]:
    return {o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET).get("Contents", [])}


class TestUploadDownload:
    def test_folder_upload_then_clear_completed(self, app_window, qtbot):
        window, db, s3, tmp_path = app_window
        folder = tmp_path / "up"
        folder.mkdir()
        for i in range(8):
            (folder / f"f{i}.txt").write_text(f"data {i}")

        window._enqueue_uploads([str(folder)])
        qtbot.waitUntil(lambda: len(_keys(s3)) >= 8, timeout=15000)
        assert s3.get_object(Bucket=BUCKET, Key="up/f3.txt")["Body"].read() == b"data 3"

        model = window._transfer_panel.model
        qtbot.waitUntil(
            lambda: model.rowCount() == 8 and all(r.status == "completed" for r in model._rows),
            timeout=15000,
        )
        window._on_clear_completed()
        assert model.rowCount() == 0

    def test_folder_download_round_trips(self, app_window, qtbot):
        window, db, s3, tmp_path = app_window
        for i in range(6):
            s3.put_object(Bucket=BUCKET, Key=f"photos/p{i}.txt", Body=f"img{i}".encode())
        dest = tmp_path / "dl"
        dest.mkdir()
        window._local_pane.navigate_to(str(dest))

        window._on_download_requested(
            [S3Item(key="photos/", name="photos", size=0, is_prefix=True)]
        )
        qtbot.waitUntil(
            lambda: (dest / "photos").exists() and len(list((dest / "photos").glob("*.txt"))) >= 6,
            timeout=15000,
        )
        assert (dest / "photos" / "p2.txt").read_text() == "img2"


class TestObjectOps:
    def test_new_folder_rename_delete(self, app_window, qtbot, monkeypatch):
        window, db, s3, tmp_path = app_window
        s3.put_object(Bucket=BUCKET, Key="doc.txt", Body=b"hello")

        monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("newdir", True))
        window._on_new_folder_requested()
        qtbot.waitUntil(lambda: "newdir/" in _keys(s3), timeout=10000)

        monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("renamed.txt", True))
        window._on_rename_requested(S3Item(key="doc.txt", name="doc.txt", size=5, is_prefix=False))
        qtbot.waitUntil(
            lambda: "renamed.txt" in _keys(s3) and "doc.txt" not in _keys(s3), timeout=10000
        )

        window._on_delete_requested(
            [S3Item(key="renamed.txt", name="renamed.txt", size=5, is_prefix=False)]
        )
        qtbot.waitUntil(lambda: "renamed.txt" not in _keys(s3), timeout=10000)


class TestLargeQueueAndCancel:
    def test_cancel_all_stops_and_reclaims(self, app_window, qtbot):
        """A big upload queue can be cancelled and its rows are dropped."""
        window, db, s3, tmp_path = app_window
        # Pause first so all files stay queued — Cancel All then deterministically
        # clears the whole queue (live-worker cancellation is covered elsewhere).
        window._transfer_panel._on_pause_all()
        folder = tmp_path / "bulk"
        folder.mkdir()
        for i in range(120):
            (folder / f"b{i:03d}.txt").write_bytes(b"x")
        bucket_id = window._ensure_bucket_id()

        def not_terminal() -> int:
            return db.fetchone(
                "SELECT COUNT(*) c FROM transfers WHERE bucket_id = ? "
                "AND status IN ('queued', 'in_progress', 'paused')",
                (bucket_id,),
            )["c"]

        window._enqueue_uploads([str(folder)])
        qtbot.waitUntil(lambda: window._transfer_panel.model.rowCount() >= 120, timeout=20000)
        assert window._transfer_panel.model.rowCount() == 120

        window._on_cancel_all_transfers()
        qtbot.waitUntil(lambda: not_terminal() == 0, timeout=15000)
        assert not_terminal() == 0
        # Cancel All removes the stopped rows from the model (memory reclaimed).
        assert window._transfer_panel.model.rowCount() == 0

    def test_folder_download_enqueues_across_chunk_boundary(self, app_window, qtbot):
        """>500 files exercise the chunked, non-blocking folder-download enqueue."""
        window, db, s3, tmp_path = app_window
        # Pause so we measure the chunked enqueue without launching download workers.
        window._transfer_panel._on_pause_all()
        dest = tmp_path / "big"
        dest.mkdir()
        window._local_pane.navigate_to(str(dest))
        bucket_id = window._ensure_bucket_id()

        files = [
            S3Item(key=f"many/f{i:04d}.txt", name=f"f{i:04d}.txt", size=1, is_prefix=False)
            for i in range(600)
        ]
        window._on_folder_enumerated("many/", files, dest, BUCKET, bucket_id)

        def dl_rows() -> int:
            return db.fetchone(
                "SELECT COUNT(*) c FROM transfers WHERE bucket_id = ? AND direction = 'download'",
                (bucket_id,),
            )["c"]

        qtbot.waitUntil(lambda: dl_rows() >= 600, timeout=15000)
        assert dl_rows() == 600
        # All queued (paused), none started.
        assert window._transfer_panel.model.rowCount() == 600
