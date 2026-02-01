"""Tests for Phase 10-11 features: notifications, window state, keyboard shortcuts, quick-open."""

from pathlib import Path

import pytest

from s3ui.db.database import Database, get_pref, set_pref
from s3ui.main_window import MainWindow


@pytest.fixture
def db(tmp_path: Path) -> Database:
    d = Database(tmp_path / "test.db")
    yield d
    d.close()


class TestMainWindowWithDB:
    def test_creates_with_db(self, qtbot, db):
        window = MainWindow(db=db)
        qtbot.addWidget(window)
        assert window.windowTitle() == "S3UI"
        assert window._db is db

    def test_show_log_action_exists(self, qtbot, db):
        window = MainWindow(db=db)
        qtbot.addWidget(window)
        assert window._show_log_action.text() == "Show &Log File"

    def test_show_log_action_connected(self, qtbot, db):
        window = MainWindow(db=db)
        qtbot.addWidget(window)
        # Just verify the action is connected (no crash)
        assert window._show_log_action.receivers(window._show_log_action.triggered) > 0

    def test_tray_icon_setup(self, qtbot, db):
        window = MainWindow(db=db)
        qtbot.addWidget(window)
        # tray_icon may be None if system tray not available in test env
        # but the method should not crash
        assert hasattr(window, "_tray_icon")


class TestWindowState:
    def test_save_and_restore_state(self, qtbot, db):
        # Create window and save state
        window1 = MainWindow(db=db)
        qtbot.addWidget(window1)
        window1.resize(1000, 700)
        window1._save_state()

        # Verify preferences were written
        assert get_pref(db, "window_geometry") is not None
        assert get_pref(db, "window_state") is not None
        assert get_pref(db, "splitter_state") is not None

    def test_transfer_dock_visibility_persisted(self, qtbot, db):
        window1 = MainWindow(db=db)
        qtbot.addWidget(window1)

        # Hide transfer dock
        window1._transfer_dock.setVisible(False)
        window1._save_state()

        assert get_pref(db, "transfer_dock_visible") == "false"

    def test_local_pane_path_persisted(self, qtbot, db, tmp_path):
        window = MainWindow(db=db)
        qtbot.addWidget(window)

        test_dir = tmp_path / "test_dir"
        test_dir.mkdir()
        window._local_pane.navigate_to(str(test_dir), record_history=False)
        window._save_state()

        assert get_pref(db, "local_pane_path") == str(test_dir)

    def test_restore_local_path(self, qtbot, db, tmp_path):
        test_dir = tmp_path / "persist_dir"
        test_dir.mkdir()
        set_pref(db, "local_pane_path", str(test_dir))

        window = MainWindow(db=db)
        qtbot.addWidget(window)
        assert window._local_pane.current_path() == str(test_dir)


class TestKeyboardShortcuts:
    def test_focus_shortcuts_exist(self, qtbot, db):
        window = MainWindow(db=db)
        qtbot.addWidget(window)

        actions = {a.text(): a for a in window.actions()}
        assert "Focus Local Pane" in actions
        assert "Focus S3 Pane" in actions

    def test_focus_local_pane(self, qtbot, db):
        window = MainWindow(db=db)
        qtbot.addWidget(window)
        window.show()

        window._focus_s3_pane()
        window._focus_local_pane()
        # Just verify no crash — actual focus depends on platform window manager


class TestQuickOpen:
    def test_quick_open_signal_exists(self, qtbot):
        from s3ui.ui.s3_pane import S3PaneWidget

        pane = S3PaneWidget()
        qtbot.addWidget(pane)
        assert hasattr(pane, "quick_open_requested")

    def test_temp_cleanup(self, qtbot, db, tmp_path):
        window = MainWindow(db=db)
        qtbot.addWidget(window)

        # Create a fake temp file
        temp = tmp_path / "temp_download.txt"
        temp.write_text("test")
        window._temp_files.append(str(temp))

        window._cleanup_temp_files()
        assert not temp.exists()
        assert len(window._temp_files) == 0


class TestTransferEngineWiring:
    def test_set_transfer_engine(self, qtbot, db):
        from unittest.mock import MagicMock

        window = MainWindow(db=db)
        qtbot.addWidget(window)

        engine = MagicMock()
        engine.transfer_finished = MagicMock()
        engine.transfer_progress = MagicMock()
        engine.transfer_speed = MagicMock()
        engine.transfer_status_changed = MagicMock()
        engine.transfer_error = MagicMock()

        window.set_transfer_engine(engine)
        assert window._transfer_engine is engine

    def test_on_transfer_finished_upload(self, qtbot, db, tmp_path):
        window = MainWindow(db=db)
        qtbot.addWidget(window)

        # Create a bucket and transfer record
        bucket_id = db.execute(
            "INSERT INTO buckets (name, region, profile) VALUES (?, ?, ?)",
            ("test-bucket", "us-east-1", "test"),
        ).lastrowid
        src = tmp_path / "uploaded.txt"
        src.write_text("data")
        tid = db.execute(
            "INSERT INTO transfers "
            "(bucket_id, object_key, direction, local_path, status, total_bytes) "
            "VALUES (?, ?, 'upload', ?, 'completed', ?)",
            (bucket_id, "uploaded.txt", str(src), 100),
        ).lastrowid

        # Call handler directly
        window._on_transfer_finished(tid)
        # Should not crash, and notification is skipped since <100MB


class TestNotifications:
    def test_notify_skips_when_active(self, qtbot, db):
        window = MainWindow(db=db)
        qtbot.addWidget(window)
        window.show()

        # No crash when calling _notify while window is active
        window._notify("Test", "Message")


class TestConstants:
    def test_new_constants_defined(self):
        from s3ui.constants import (
            NOTIFY_SIZE_THRESHOLD,
            QUICK_OPEN_THRESHOLD,
            TEMP_DIR,
        )

        assert NOTIFY_SIZE_THRESHOLD == 100 * 1024 * 1024
        assert QUICK_OPEN_THRESHOLD == 10 * 1024 * 1024
        assert TEMP_DIR.name == "temp"


class TestTransferModelColumnFix:
    def test_column_names_match_schema(self, db):
        """Verify the TransferModel reads correct column names from DB."""
        bucket_id = db.execute(
            "INSERT INTO buckets (name, region, profile) VALUES (?, ?, ?)",
            ("test-bucket", "us-east-1", "test"),
        ).lastrowid

        tid = db.execute(
            "INSERT INTO transfers "
            "(bucket_id, object_key, direction, local_path, status, total_bytes, transferred) "
            "VALUES (?, ?, 'upload', ?, 'queued', 1000, 500)",
            (bucket_id, "test.txt", "/tmp/test.txt"),
        ).lastrowid

        from s3ui.models.transfer_model import TransferModel

        model = TransferModel(db=db)
        model.add_transfer(tid)

        row = model.get_transfer_row(tid)
        assert row is not None
        assert row.s3_key == "test.txt"
        assert row.total_bytes == 1000
        assert row.transferred_bytes == 500
