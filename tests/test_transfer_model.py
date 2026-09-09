"""Tests for TransferModel and transfer panel."""

from s3ui.models.transfer_model import (
    TransferModel,
    TransferRow,
    _format_eta,
    _format_pct,
    _format_progress,
    _format_speed,
    _format_status,
)
from s3ui.ui.confirm_delete import DeleteConfirmDialog
from s3ui.ui.get_info import GetInfoDialog
from s3ui.ui.name_conflict import ConflictResolution, NameConflictDialog
from s3ui.ui.transfer_panel import TransferPanelWidget


class TestFormatSpeed:
    def test_zero(self):
        assert _format_speed(0) == "—"

    def test_bytes_per_sec(self):
        assert _format_speed(512) == "512 B/s"

    def test_kb_per_sec(self):
        assert _format_speed(10240) == "10.0 KB/s"

    def test_mb_per_sec(self):
        assert _format_speed(12.4 * 1024 * 1024) == "12.4 MB/s"


class TestFormatEta:
    def test_zero(self):
        assert _format_eta(0) == "—"

    def test_seconds(self):
        assert _format_eta(47) == "~47 sec"

    def test_minutes(self):
        assert _format_eta(120) == "~2 min"

    def test_hours(self):
        assert _format_eta(7200) == "~2.0 hr"


class TestFormatProgress:
    def test_zero_total(self):
        assert _format_progress(0, 0) == ""

    def test_half(self):
        assert _format_progress(50, 100) == "50 B / 100 B"

    def test_complete(self):
        assert _format_progress(100, 100) == "100 B / 100 B"

    def test_megabytes(self):
        mb = 1024 * 1024
        assert _format_progress(5 * mb, 10 * mb) == "5.0 MB / 10.0 MB"


class TestFormatPct:
    def test_zero_total(self):
        assert _format_pct(0, 0) == "0%"

    def test_half(self):
        assert _format_pct(50, 100) == "50%"

    def test_complete(self):
        assert _format_pct(100, 100) == "100%"

    def test_clamped(self):
        assert _format_pct(200, 100) == "100%"


class TestFormatStatus:
    def test_queued(self):
        row = TransferRow(1, "upload", "f.txt", "/f.txt", "f.txt", 100)
        assert _format_status(row) == "Queued"

    def test_in_progress(self):
        row = TransferRow(1, "upload", "f.txt", "/f.txt", "f.txt", 100, 50, status="in_progress")
        assert "50%" in _format_status(row)

    def test_completed(self):
        row = TransferRow(1, "upload", "f.txt", "/f.txt", "f.txt", 100, status="completed")
        assert _format_status(row) == "Complete"

    def test_failed(self):
        row = TransferRow(1, "upload", "f.txt", "/f.txt", "f.txt", 100, status="failed")
        assert _format_status(row) == "Failed"


class TestTransferModel:
    def test_empty(self, qtbot):
        model = TransferModel()
        assert model.rowCount() == 0
        assert model.columnCount() == 6

    def test_header_data(self, qtbot):
        from PyQt6.QtCore import Qt

        model = TransferModel()
        assert model.headerData(1, Qt.Orientation.Horizontal) == "File"

    def test_on_progress_buffers(self, qtbot):
        model = TransferModel()
        # Manually add a row
        row = TransferRow(1, "upload", "f.txt", "/f.txt", "f.txt", 1000)
        model.beginInsertRows(model._EMPTY_INDEX, 0, 0)
        model._rows.append(row)
        model._id_to_row[1] = 0
        model.endInsertRows()

        model.on_progress(1, 500, 1000)
        assert 1 in model._pending_updates

        model._flush_updates()
        assert model._rows[0].transferred_bytes == 500

    def test_on_finished(self, qtbot):
        model = TransferModel()
        row = TransferRow(1, "upload", "f.txt", "/f.txt", "f.txt", 1000, status="in_progress")
        model.beginInsertRows(model._EMPTY_INDEX, 0, 0)
        model._rows.append(row)
        model._id_to_row[1] = 0
        model.endInsertRows()

        model.on_finished(1)
        model._flush_updates()
        assert model._rows[0].status == "completed"

    def test_active_and_queued_count(self, qtbot):
        model = TransferModel()
        rows = [
            TransferRow(1, "upload", "a.txt", "/a", "a", 100, status="in_progress"),
            TransferRow(2, "upload", "b.txt", "/b", "b", 100, status="queued"),
            TransferRow(3, "upload", "c.txt", "/c", "c", 100, status="completed"),
        ]
        model.beginInsertRows(model._EMPTY_INDEX, 0, 2)
        model._rows.extend(rows)
        for i, r in enumerate(rows):
            model._id_to_row[r.transfer_id] = i
        model.endInsertRows()

        assert model.active_count() == 1
        assert model.queued_count() == 1


def _seed_transfer(db, bucket_id, key, status="queued"):
    return db.execute(
        "INSERT INTO transfers (bucket_id, object_key, direction, local_path, status, "
        "total_bytes, transferred) VALUES (?, ?, 'upload', ?, ?, 0, 0)",
        (bucket_id, key, f"/tmp/{key}", status),
    ).lastrowid


class TestAddTransfers:
    def _db(self, tmp_path):
        from s3ui.db.database import Database

        db = Database(tmp_path / "m.db")
        bucket_id = db.execute(
            "INSERT INTO buckets (name, region, profile) VALUES ('b', '', 'p')"
        ).lastrowid
        return db, bucket_id

    def test_bulk_insert_preserves_order(self, qtbot, tmp_path):
        db, bucket_id = self._db(tmp_path)
        ids = [_seed_transfer(db, bucket_id, f"k{i}.txt") for i in range(200)]
        model = TransferModel(db)

        model.add_transfers(ids)

        assert model.rowCount() == 200
        assert [r.transfer_id for r in model._rows] == ids
        assert model._id_to_row[ids[0]] == 0
        assert model._id_to_row[ids[-1]] == 199
        db.close()

    def test_skips_already_present_and_duplicates(self, qtbot, tmp_path):
        db, bucket_id = self._db(tmp_path)
        a = _seed_transfer(db, bucket_id, "a.txt")
        b = _seed_transfer(db, bucket_id, "b.txt")
        model = TransferModel(db)

        model.add_transfers([a, a, b, a])

        assert model.rowCount() == 2
        model.add_transfers([a, b])  # both already shown
        assert model.rowCount() == 2
        db.close()

    def test_add_transfer_delegates(self, qtbot, tmp_path):
        db, bucket_id = self._db(tmp_path)
        a = _seed_transfer(db, bucket_id, "a.txt")
        model = TransferModel(db)

        model.add_transfer(a)

        assert model.rowCount() == 1
        db.close()


class TestCancelAllRows:
    def test_flips_non_terminal_to_cancelled(self, qtbot, tmp_path):
        from s3ui.db.database import Database

        db = Database(tmp_path / "c.db")
        bucket_id = db.execute(
            "INSERT INTO buckets (name, region, profile) VALUES ('b', '', 'p')"
        ).lastrowid
        statuses = ["queued", "in_progress", "paused", "completed", "failed"]
        ids = {s: _seed_transfer(db, bucket_id, f"{s}.txt", status=s) for s in statuses}
        model = TransferModel(db)
        model.add_transfers(list(ids.values()))

        model.cancel_all_rows()

        by_id = {r.transfer_id: r for r in model._rows}
        for s in ("queued", "in_progress", "paused"):
            assert by_id[ids[s]].status == "cancelled"
        assert by_id[ids["completed"]].status == "completed"
        assert by_id[ids["failed"]].status == "failed"
        db.close()


class TestTransferPanel:
    def test_creates(self, qtbot):
        panel = TransferPanelWidget()
        qtbot.addWidget(panel)
        assert panel._header_label.text() == "Transfers"

    def test_pause_all_button(self, qtbot):
        panel = TransferPanelWidget()
        qtbot.addWidget(panel)
        assert panel._pause_all_btn.text() == "Pause All"

    def test_cancel_all_noop_when_idle(self, qtbot):
        """With nothing active or queued, the button does not fire the signal."""
        panel = TransferPanelWidget()
        qtbot.addWidget(panel)
        fired = []
        panel.cancel_all_requested.connect(lambda: fired.append(True))
        panel._on_cancel_all()  # no confirm dialog is raised when idle
        assert fired == []

    def test_cancel_all_emits_after_confirm(self, qtbot, monkeypatch, tmp_path):
        from PyQt6.QtWidgets import QMessageBox

        from s3ui.db.database import Database

        db = Database(tmp_path / "p.db")
        bucket_id = db.execute(
            "INSERT INTO buckets (name, region, profile) VALUES ('b', '', 'p')"
        ).lastrowid
        panel = TransferPanelWidget(db=db)
        qtbot.addWidget(panel)
        panel.add_transfers([_seed_transfer(db, bucket_id, "q.txt")])

        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
        fired = []
        panel.cancel_all_requested.connect(lambda: fired.append(True))
        panel._on_cancel_all()
        assert fired == [True]
        db.close()


class TestDeleteConfirmDialog:
    def test_creates(self, qtbot):
        dialog = DeleteConfirmDialog(["key1.txt", "key2.txt"], total_size=1024)
        qtbot.addWidget(dialog)
        assert "2" in dialog.windowTitle()

    def test_single_item(self, qtbot):
        dialog = DeleteConfirmDialog(["single.txt"])
        qtbot.addWidget(dialog)
        assert "1" in dialog.windowTitle()

    def test_many_items_truncated(self, qtbot):
        keys = [f"file{i}.txt" for i in range(20)]
        dialog = DeleteConfirmDialog(keys)
        qtbot.addWidget(dialog)
        assert "20" in dialog.windowTitle()


class TestNameConflictDialog:
    def test_creates(self, qtbot):
        dialog = NameConflictDialog("test.txt")
        qtbot.addWidget(dialog)
        assert dialog.windowTitle() == "File Already Exists"

    def test_default_resolution(self, qtbot):
        dialog = NameConflictDialog("test.txt")
        qtbot.addWidget(dialog)
        assert dialog.resolution() == ConflictResolution.REPLACE

    def test_apply_to_all_default(self, qtbot):
        dialog = NameConflictDialog("test.txt")
        qtbot.addWidget(dialog)
        assert dialog.apply_to_all() is False


class TestGetInfoDialog:
    def test_creates_for_file(self, qtbot):
        from s3ui.models.s3_objects import S3Item

        item = S3Item(name="test.txt", key="test.txt", is_prefix=False, size=1024)
        dialog = GetInfoDialog(item)
        qtbot.addWidget(dialog)
        assert dialog.windowTitle() == "Get Info"

    def test_creates_for_prefix(self, qtbot):
        from s3ui.models.s3_objects import S3Item

        item = S3Item(name="docs/", key="docs/", is_prefix=True)
        dialog = GetInfoDialog(item)
        qtbot.addWidget(dialog)
        assert dialog.windowTitle() == "Get Info"
