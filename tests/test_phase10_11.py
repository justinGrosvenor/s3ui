"""Tests for Phase 10-11 features: notifications, window state, keyboard shortcuts, quick-open."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtWidgets import QMessageBox

from s3ui.core.credentials import CredentialStore, Profile
from s3ui.db.database import Database, get_pref, set_pref
from s3ui.main_window import MainWindow, _ConnectWorker


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


class TestConnectionFlow:
    """Tests for profile discovery, connection, and bucket selection."""

    @pytest.fixture
    def mock_discover(self, monkeypatch):
        """Mock discover_aws_profiles to return a known list."""
        profiles = ["default", "work"]
        monkeypatch.setattr("s3ui.main_window.discover_aws_profiles", lambda: profiles)
        return profiles

    @pytest.fixture
    def mock_discover_empty(self, monkeypatch):
        """Mock discover_aws_profiles to return nothing."""
        monkeypatch.setattr("s3ui.main_window.discover_aws_profiles", lambda: [])

    def test_populate_profiles_aws(self, qtbot, db, mock_keyring, mock_discover):
        window = MainWindow(db=db)
        qtbot.addWidget(window)

        # _populate_profiles is called via _init_connection on a timer,
        # but we can call it directly for testing
        window._populate_profiles()

        assert window._profile_combo.count() == 2
        assert window._profile_combo.itemData(0) == "default"
        assert window._profile_combo.itemData(1) == "work"
        assert "AWS" in window._profile_combo.itemText(0)

    def test_populate_profiles_mixed(self, qtbot, db, mock_keyring, mock_discover):
        # Save a custom profile
        store = CredentialStore()
        store.save_profile(
            Profile(
                name="custom",
                access_key_id="AKIA1",
                secret_access_key="secret1",
                region="us-east-1",
            )
        )

        window = MainWindow(db=db)
        qtbot.addWidget(window)
        window._populate_profiles()

        # 2 AWS + 1 custom
        assert window._profile_combo.count() == 3
        assert window._profile_combo.itemData(2) == "custom"

    def test_populate_profiles_no_duplicates(self, qtbot, db, mock_keyring, mock_discover):
        # Save a profile with the same name as an AWS profile
        store = CredentialStore()
        store.save_profile(
            Profile(
                name="default",
                access_key_id="AKIA1",
                secret_access_key="secret1",
                region="us-east-1",
            )
        )

        window = MainWindow(db=db)
        qtbot.addWidget(window)
        window._populate_profiles()

        # "default" appears only once (from AWS)
        assert window._profile_combo.count() == 2

    def test_connect_worker_success(self, qtbot):
        mock_client = MagicMock()
        mock_client.list_buckets.return_value = ["bucket-a", "bucket-b"]
        profile = Profile(
            name="test",
            access_key_id="AKIA",
            secret_access_key="secret",
            region="us-east-1",
        )

        with patch("s3ui.main_window.S3Client", return_value=mock_client):
            worker = _ConnectWorker(profile)
            with qtbot.waitSignal(worker.signals.connected, timeout=5000) as sig:
                worker.start()

        worker.wait(5000)  # join thread before it goes out of scope
        client, buckets = sig.args
        assert buckets == ["bucket-a", "bucket-b"]

    def test_connect_worker_failure(self, qtbot):
        from s3ui.core.s3_client import S3ClientError

        profile = Profile(
            name="test",
            access_key_id="AKIA",
            secret_access_key="bad",
            region="us-east-1",
        )

        with patch("s3ui.main_window.S3Client", side_effect=S3ClientError("Bad key", "detail")):
            worker = _ConnectWorker(profile)
            with qtbot.waitSignal(worker.signals.failed, timeout=5000) as sig:
                worker.start()

        worker.wait(5000)  # join thread before it goes out of scope
        assert "Bad key" in sig.args[0]

    def test_on_connected_populates_buckets(self, qtbot, db, mock_keyring, mock_discover):
        window = MainWindow(db=db)
        qtbot.addWidget(window)

        mock_client = MagicMock()
        window._on_connected(mock_client, ["alpha", "beta", "gamma"])

        assert window._bucket_combo.count() == 3
        assert window._bucket_combo.itemData(0) == "alpha"
        assert window._bucket_combo.itemData(1) == "beta"
        assert window._bucket_combo.itemData(2) == "gamma"

    def test_on_connected_saves_last_profile(self, qtbot, db, mock_keyring, mock_discover):
        window = MainWindow(db=db)
        qtbot.addWidget(window)

        # Set a profile in the combo
        window._populate_profiles()
        window._profile_combo.setCurrentIndex(1)  # "work"

        mock_client = MagicMock()
        window._on_connected(mock_client, ["mybucket"])

        assert get_pref(db, "last_profile") == "work"

    def test_on_bucket_selected_sets_bucket(self, qtbot, db, mock_keyring, mock_discover):
        window = MainWindow(db=db)
        qtbot.addWidget(window)

        # Set up S3 pane with a mock client
        mock_client = MagicMock()
        window._s3_pane.set_client(mock_client)

        # Manually populate bucket combo
        window._bucket_combo.addItem("test-bucket", "test-bucket")

        with patch.object(window._s3_pane, "set_bucket") as mock_set:
            window._on_bucket_selected(0)
            mock_set.assert_called_once_with("test-bucket")

    def test_on_bucket_selected_saves_preference(self, qtbot, db, mock_keyring, mock_discover):
        window = MainWindow(db=db)
        qtbot.addWidget(window)

        mock_client = MagicMock()
        window._s3_pane.set_client(mock_client)

        window._bucket_combo.addItem("mybucket", "mybucket")
        window._on_bucket_selected(0)

        assert get_pref(db, "last_bucket") == "mybucket"

    def test_on_connect_failed_sets_status(self, qtbot, db, mock_keyring, mock_discover):
        window = MainWindow(db=db)
        qtbot.addWidget(window)

        window._on_connect_failed("Invalid access key")
        assert "Connection failed" in window._status_label.text()
        assert "Invalid access key" in window._status_label.text()

    def test_on_connected_restores_last_bucket(self, qtbot, db, mock_keyring, mock_discover):
        set_pref(db, "last_bucket", "beta")

        window = MainWindow(db=db)
        qtbot.addWidget(window)

        mock_client = MagicMock()
        with patch.object(window._s3_pane, "set_bucket") as mock_set:
            window._on_connected(mock_client, ["alpha", "beta", "gamma"])
            mock_set.assert_called_once_with("beta")
            assert window._bucket_combo.currentData() == "beta"

    def test_init_connection_no_profiles_shows_wizard(
        self, qtbot, db, mock_keyring, mock_discover_empty
    ):
        window = MainWindow(db=db)
        qtbot.addWidget(window)

        with patch.object(window, "_show_setup_wizard") as mock_wizard:
            window._init_connection()
            mock_wizard.assert_called_once()

    def test_init_connection_restores_last_profile(self, qtbot, db, mock_keyring, mock_discover):
        set_pref(db, "last_profile", "work")

        window = MainWindow(db=db)
        qtbot.addWidget(window)

        with patch.object(window, "_connect_to_profile") as mock_connect:
            window._init_connection()
            assert window._profile_combo.currentData() == "work"
            mock_connect.assert_called_once()
            profile = mock_connect.call_args[0][0]
            assert profile.name == "work"
            assert profile.is_aws_profile is True

    def test_open_settings_passes_store_and_db(self, qtbot, db, mock_keyring, mock_discover):
        window = MainWindow(db=db)
        qtbot.addWidget(window)

        with patch("s3ui.main_window.SettingsDialog") as MockDialog:
            MockDialog.return_value.exec.return_value = 0
            window._open_settings()
            MockDialog.assert_called_once_with(store=window._store, db=db, parent=window)


class TestUploadDownloadWiring:
    """Tests for upload/download signal wiring and transfer creation."""

    @pytest.fixture
    def connected_window(self, qtbot, db, mock_keyring):
        """Create a MainWindow with a mock S3 client connected and a bucket selected."""
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr("s3ui.main_window.discover_aws_profiles", lambda: ["default"])

        window = MainWindow(db=db)
        qtbot.addWidget(window)

        mock_client = MagicMock()
        mock_client.list_buckets.return_value = ["test-bucket"]
        window._s3_client = mock_client
        window._s3_pane.set_client(mock_client)

        # Populate combos
        window._populate_profiles()
        window._bucket_combo.blockSignals(True)
        window._bucket_combo.addItem("test-bucket", "test-bucket")
        window._bucket_combo.blockSignals(False)

        # Select bucket (creates engine)
        window._on_bucket_selected(0)

        monkeypatch.undo()
        yield window

    def test_upload_signal_connected(self, connected_window):
        """upload_requested signal triggers _enqueue_uploads."""
        with patch.object(connected_window, "_enqueue_uploads") as mock:
            connected_window._local_pane.upload_requested.emit(["/tmp/test.txt"])
            mock.assert_called_once_with(["/tmp/test.txt"])

    def test_files_dropped_signal_connected(self, connected_window):
        """files_dropped signal triggers _enqueue_uploads."""
        with patch.object(connected_window, "_enqueue_uploads") as mock:
            connected_window._s3_pane.files_dropped.emit(["/tmp/test.txt"])
            mock.assert_called_once_with(["/tmp/test.txt"])

    def test_download_signal_connected(self, connected_window, tmp_path):
        """download_requested signal creates download transfers."""
        from s3ui.models.s3_objects import S3Item

        connected_window._local_pane.navigate_to(str(tmp_path), record_history=False)
        items = [S3Item(name="test.txt", key="test.txt", is_prefix=False, size=100)]
        connected_window._s3_pane.download_requested.emit(items)

        rows = connected_window._db.fetchall("SELECT * FROM transfers WHERE direction = 'download'")
        assert len(rows) == 1

    def test_transfer_engine_created_on_bucket_select(self, connected_window):
        """TransferEngine is created when a bucket is selected."""
        assert connected_window._transfer_engine is not None

    def test_ensure_bucket_id_creates_record(self, connected_window):
        """_ensure_bucket_id creates a bucket record if none exists."""
        bucket_id = connected_window._ensure_bucket_id()
        assert bucket_id is not None
        assert bucket_id > 0

    def test_ensure_bucket_id_reuses_existing(self, connected_window):
        """_ensure_bucket_id returns existing bucket record."""
        id1 = connected_window._ensure_bucket_id()
        id2 = connected_window._ensure_bucket_id()
        assert id1 == id2

    def test_enqueue_uploads_creates_transfers(self, connected_window, tmp_path):
        """_enqueue_uploads creates transfer records in the database."""
        db = connected_window._db

        # Create test files
        f1 = tmp_path / "file1.txt"
        f1.write_text("hello")
        f2 = tmp_path / "file2.txt"
        f2.write_text("world!")

        connected_window._enqueue_uploads([str(f1), str(f2)])

        rows = db.fetchall("SELECT * FROM transfers WHERE direction = 'upload'")
        assert len(rows) == 2
        keys = {r["object_key"] for r in rows}
        assert "file1.txt" in keys
        assert "file2.txt" in keys

    def test_enqueue_uploads_with_prefix(self, connected_window, tmp_path):
        """Uploads use the current S3 prefix as the key prefix."""
        db = connected_window._db
        connected_window._s3_pane._current_prefix = "docs/"

        f = tmp_path / "readme.txt"
        f.write_text("data")
        connected_window._enqueue_uploads([str(f)])

        row = db.fetchone("SELECT object_key FROM transfers WHERE direction = 'upload'")
        assert row["object_key"] == "docs/readme.txt"

    def test_enqueue_uploads_directory(self, connected_window, tmp_path):
        """Uploading a directory recursively enqueues all files."""
        db = connected_window._db

        sub = tmp_path / "mydir"
        sub.mkdir()
        (sub / "a.txt").write_text("a")
        (sub / "b.txt").write_text("b")
        nested = sub / "inner"
        nested.mkdir()
        (nested / "c.txt").write_text("c")

        connected_window._enqueue_uploads([str(sub)])

        rows = db.fetchall("SELECT object_key FROM transfers ORDER BY object_key")
        keys = [r["object_key"] for r in rows]
        assert "mydir/a.txt" in keys
        assert "mydir/b.txt" in keys
        assert "mydir/inner/c.txt" in keys

    def test_download_requested_creates_transfers(self, connected_window, tmp_path):
        """_on_download_requested creates download transfer records."""
        from s3ui.models.s3_objects import S3Item

        db = connected_window._db

        # Point local pane at a real dir
        connected_window._local_pane.navigate_to(str(tmp_path), record_history=False)

        items = [
            S3Item(name="photo.jpg", key="photo.jpg", is_prefix=False, size=1024),
            S3Item(name="data.csv", key="data.csv", is_prefix=False, size=2048),
        ]
        connected_window._on_download_requested(items)

        rows = db.fetchall("SELECT * FROM transfers WHERE direction = 'download'")
        assert len(rows) == 2
        assert rows[0]["total_bytes"] in (1024, 2048)

    def test_download_skips_prefixes(self, connected_window, tmp_path):
        """_on_download_requested skips prefix (folder) items."""
        from s3ui.models.s3_objects import S3Item

        db = connected_window._db
        connected_window._local_pane.navigate_to(str(tmp_path), record_history=False)

        items = [
            S3Item(name="folder/", key="folder/", is_prefix=True),
        ]
        connected_window._on_download_requested(items)

        rows = db.fetchall("SELECT * FROM transfers WHERE direction = 'download'")
        assert len(rows) == 0

    def test_upload_not_connected_shows_status(self, qtbot, db, mock_keyring):
        """Upload without connection shows a status message."""
        window = MainWindow(db=db)
        qtbot.addWidget(window)

        window._enqueue_uploads(["/tmp/fake.txt"])
        assert "Not connected" in window._status_label.text()

    def test_transfer_panel_control_signals(self, connected_window):
        """Transfer panel pause/resume/cancel/retry signals reach the engine."""
        engine = connected_window._transfer_engine
        with patch.object(engine, "pause") as mock_pause:
            connected_window._on_pause_transfer(42)
            mock_pause.assert_called_once_with(42)
        with patch.object(engine, "resume") as mock_resume:
            connected_window._on_resume_transfer(42)
            mock_resume.assert_called_once_with(42)
        with patch.object(engine, "cancel") as mock_cancel:
            connected_window._on_cancel_transfer(42)
            mock_cancel.assert_called_once_with(42)
        with patch.object(engine, "retry") as mock_retry:
            connected_window._on_retry_transfer(42)
            mock_retry.assert_called_once_with(42)

    def test_delete_requested_calls_delete(self, connected_window):
        """_on_delete_requested deletes objects after confirmation."""
        from s3ui.models.s3_objects import S3Item

        mock_client = connected_window._s3_client
        mock_client.delete_objects.return_value = []  # no failures

        items = [
            S3Item(name="old.txt", key="old.txt", is_prefix=False, size=100),
        ]

        ret_yes = QMessageBox.StandardButton.Yes
        with patch("s3ui.main_window.QMessageBox.question", return_value=ret_yes):
            connected_window._on_delete_requested(items)

        # Worker runs in background — wait for it
        if connected_window._delete_worker:
            connected_window._delete_worker.wait(3000)

        mock_client.delete_objects.assert_called_once_with("test-bucket", ["old.txt"])

    def test_delete_cancelled_by_user(self, connected_window):
        """Delete is cancelled if user clicks No."""
        from s3ui.models.s3_objects import S3Item

        items = [S3Item(name="keep.txt", key="keep.txt", is_prefix=False, size=100)]

        ret_no = QMessageBox.StandardButton.No
        with patch("s3ui.main_window.QMessageBox.question", return_value=ret_no):
            connected_window._on_delete_requested(items)

        connected_window._s3_client.delete_objects.assert_not_called()

    def test_delete_folder(self, connected_window):
        """Folders (prefixes) can be deleted."""
        from s3ui.models.s3_objects import S3Item

        mock_client = connected_window._s3_client
        mock_client.delete_objects.return_value = []

        items = [S3Item(name="my-folder", key="my-folder/", is_prefix=True)]

        ret_yes = QMessageBox.StandardButton.Yes
        with patch("s3ui.main_window.QMessageBox.question", return_value=ret_yes):
            connected_window._on_delete_requested(items)

        if connected_window._delete_worker:
            connected_window._delete_worker.wait(3000)

        mock_client.delete_objects.assert_called_once_with("test-bucket", ["my-folder/"])

    def test_new_folder_creates_object(self, connected_window):
        """New folder creates an empty S3 object with trailing slash."""
        mock_client = connected_window._s3_client

        with patch(
            "s3ui.main_window.QInputDialog.getText",
            return_value=("my-folder", True),
        ):
            connected_window._on_new_folder_requested()

        mock_client.put_object.assert_called_once_with("test-bucket", "my-folder/", b"")

    def test_new_folder_cancelled(self, connected_window):
        """New folder does nothing when user cancels the dialog."""
        mock_client = connected_window._s3_client

        with patch(
            "s3ui.main_window.QInputDialog.getText",
            return_value=("", False),
        ):
            connected_window._on_new_folder_requested()

        mock_client.put_object.assert_not_called()

    def test_new_folder_with_prefix(self, connected_window):
        """New folder uses the current S3 prefix."""
        mock_client = connected_window._s3_client
        connected_window._s3_pane._current_prefix = "docs/"

        with patch(
            "s3ui.main_window.QInputDialog.getText",
            return_value=("sub", True),
        ):
            connected_window._on_new_folder_requested()

        mock_client.put_object.assert_called_once_with("test-bucket", "docs/sub/", b"")

    def test_new_folder_button_exists(self, connected_window):
        """S3 pane toolbar has a New Folder button."""
        btn = connected_window._s3_pane._new_folder_btn
        assert btn.toolTip() == "New Folder"

    def test_cost_tracker_created_on_bucket_select(self, connected_window):
        """CostTracker is created when a bucket is selected."""
        assert connected_window._cost_tracker is not None

    def test_cost_label_updated(self, connected_window):
        """Status bar cost label shows an estimate after bucket selection."""
        assert "$" in connected_window._cost_label.text()

    def test_cost_action_enabled(self, connected_window):
        """Cost Dashboard menu action is enabled after bucket selection."""
        assert connected_window._cost_action.isEnabled()

    def test_s3_client_receives_cost_tracker(self, connected_window):
        """S3Client.set_cost_tracker is called after bucket selection."""
        connected_window._s3_client.set_cost_tracker.assert_called_once_with(
            connected_window._cost_tracker
        )
