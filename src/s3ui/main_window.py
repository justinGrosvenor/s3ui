"""Main application window — dual-pane layout with toolbar, menus, status bar."""

import logging
import sys
import threading

from PyQt6.QtCore import QByteArray, QObject, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QAction, QDesktopServices, QKeySequence
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDockWidget,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QSystemTrayIcon,
    QToolBar,
    QWidget,
)

from s3ui.constants import (
    LOG_DIR,
    MIN_WINDOW_HEIGHT,
    MIN_WINDOW_WIDTH,
    NOTIFY_SIZE_THRESHOLD,
    QUICK_OPEN_THRESHOLD,
    TEMP_DIR,
)
from s3ui.core.cost import CostTracker
from s3ui.core.credentials import CredentialStore, Profile, discover_aws_profiles
from s3ui.core.s3_client import S3Client, S3ClientError
from s3ui.core.transfers import TransferEngine
from s3ui.ui.local_pane import LocalPaneWidget
from s3ui.ui.s3_pane import S3PaneWidget
from s3ui.ui.settings_dialog import SettingsDialog
from s3ui.ui.setup_wizard import SetupWizard
from s3ui.ui.transfer_panel import TransferPanelWidget

logger = logging.getLogger("s3ui.main_window")


class _ConnectSignals(QObject):
    connected = pyqtSignal(object, list)  # S3Client, bucket_names
    failed = pyqtSignal(str)  # error message


class _ConnectWorker(QThread):
    """Background thread for connecting to an AWS profile and listing buckets."""

    def __init__(self, profile: Profile, parent=None) -> None:
        super().__init__(parent)
        self.signals = _ConnectSignals()
        self._profile = profile

    def run(self) -> None:
        try:
            client = S3Client(self._profile)
        except S3ClientError as e:
            self.signals.failed.emit(e.user_message)
            return
        except Exception as e:
            self.signals.failed.emit(str(e))
            return

        try:
            buckets = client.list_buckets()
        except S3ClientError as e:
            if "AccessDenied" in e.detail:
                # Bucket-scoped credentials (common with R2 tokens and
                # least-privilege IAM policies) can't list buckets — still
                # connected, the user just has to name the bucket
                self.signals.connected.emit(client, [])
            else:
                self.signals.failed.emit(e.user_message)
        except Exception as e:
            self.signals.failed.emit(str(e))
        else:
            self.signals.connected.emit(client, buckets)


class _DeleteSignals(QObject):
    finished = pyqtSignal(list, list)  # deleted keys, failed keys
    failed = pyqtSignal(str)  # error message


class _DeleteWorker(QThread):
    """Background thread for deleting S3 objects, expanding folder prefixes."""

    def __init__(
        self,
        s3_client: S3Client,
        bucket: str,
        keys: list[str],
        prefixes: list[str] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.signals = _DeleteSignals()
        self._s3 = s3_client
        self._bucket = bucket
        self._keys = keys
        self._prefixes = prefixes or []

    def run(self) -> None:
        try:
            keys = list(self._keys)
            for prefix in self._prefixes:
                # Recursive listing (no delimiter) excludes the placeholder
                # key itself, so add it explicitly
                objects, _ = self._s3.list_objects(self._bucket, prefix, delimiter="")
                keys.extend(o.key for o in objects if not o.is_prefix)
                keys.append(prefix)
            keys = list(dict.fromkeys(keys))
            failed = set(self._s3.delete_objects(self._bucket, keys))
            deleted = [k for k in keys if k not in failed]
            self.signals.finished.emit(deleted, sorted(failed))
        except Exception as e:
            self.signals.failed.emit(str(e))


class _KeyOpSignals(QObject):
    finished = pyqtSignal(object)  # payload passed through on success
    failed = pyqtSignal(str)  # error message


class _KeyOpWorker(QThread):
    """Background thread running a callable of S3 key operations (copy/rename)."""

    def __init__(self, fn, payload, parent=None) -> None:
        super().__init__(parent)
        self.signals = _KeyOpSignals()
        self._fn = fn
        self._payload = payload

    def run(self) -> None:
        try:
            self._fn()
            self.signals.finished.emit(self._payload)
        except Exception as e:
            self.signals.failed.emit(str(e))


class _QuickOpenSignals(QObject):
    finished = pyqtSignal(str)  # local file path
    failed = pyqtSignal(str, str)  # key, error message


class _QuickOpenWorker(QThread):
    """Background thread downloading a small file to temp for quick-open."""

    def __init__(self, s3_client: S3Client, bucket: str, key: str, dest, parent=None) -> None:
        super().__init__(parent)
        self.signals = _QuickOpenSignals()
        self._s3 = s3_client
        self._bucket = bucket
        self._key = key
        self._dest = dest

    def run(self) -> None:
        try:
            body = self._s3.get_object(self._bucket, self._key)
            data = body.read()
            self._dest.parent.mkdir(parents=True, exist_ok=True)
            self._dest.write_bytes(data)
            self.signals.finished.emit(str(self._dest))
        except Exception as e:
            self.signals.failed.emit(self._key, str(e))


class _EnumerateSignals(QObject):
    finished = pyqtSignal(str, list)  # folder prefix, list of S3Item files
    failed = pyqtSignal(str, str)  # folder prefix, error message


class _EnumerateWorker(QThread):
    """Background thread listing all objects under a folder prefix."""

    def __init__(self, s3_client: S3Client, bucket: str, prefix: str, parent=None) -> None:
        super().__init__(parent)
        self.signals = _EnumerateSignals()
        self._s3 = s3_client
        self._bucket = bucket
        self._prefix = prefix

    def run(self) -> None:
        try:
            objects, _ = self._s3.list_objects(self._bucket, self._prefix, delimiter="")
            files = [o for o in objects if not o.is_prefix and not o.key.endswith("/")]
            self.signals.finished.emit(self._prefix, files)
        except Exception as e:
            self.signals.failed.emit(self._prefix, str(e))


class MainWindow(QMainWindow):
    def __init__(self, db=None) -> None:
        super().__init__()
        self._db = db
        self._transfer_engine = None
        self._retired_engines: list = []
        self._tray_icon: QSystemTrayIcon | None = None
        self._temp_files: list[str] = []
        self._store = CredentialStore()
        self._s3_client: S3Client | None = None
        self._connect_worker: _ConnectWorker | None = None
        self._wizard: SetupWizard | None = None
        self._bg_workers: list[QThread] = []
        self._s3_clipboard: tuple[str, list] | None = None  # (bucket, [S3Item files])
        self._orphan_cleaned_buckets: set[str] = set()
        self._cost_tracker: CostTracker | None = None
        self._aws_profile_names: set[str] = set()

        self.setWindowTitle("S3UI")
        self.setMinimumSize(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)

        self._setup_toolbar()
        self._setup_central()
        self._setup_transfer_dock()
        self._setup_status_bar()
        self._setup_menus()
        self._setup_keyboard_shortcuts()
        self._setup_tray_icon()
        self._restore_state()

        # Wire combo signals
        self._profile_combo.currentIndexChanged.connect(self._on_profile_selected)
        self._bucket_combo.currentIndexChanged.connect(self._on_bucket_selected)

        # Wire double-click-to-open
        self._s3_pane.quick_open_requested.connect(self._on_quick_open)

        # Wire upload / download / drop / delete signals
        self._local_pane.upload_requested.connect(self._on_upload_requested)
        self._s3_pane.files_dropped.connect(self._on_files_dropped)
        self._s3_pane.download_requested.connect(self._on_download_requested)
        self._s3_pane.delete_requested.connect(self._on_delete_requested)
        self._s3_pane.new_folder_requested.connect(self._on_new_folder_requested)
        self._s3_pane.rename_requested.connect(self._on_rename_requested)
        self._s3_pane.copy_requested.connect(self._on_copy_items)
        self._s3_pane.get_info_requested.connect(self._on_get_info)
        self._s3_pane.selection_changed.connect(self._update_edit_actions)

        # Wire transfer panel control signals
        self._transfer_panel.pause_requested.connect(self._on_pause_transfer)
        self._transfer_panel.resume_requested.connect(self._on_resume_transfer)
        self._transfer_panel.cancel_requested.connect(self._on_cancel_transfer)
        self._transfer_panel.retry_requested.connect(self._on_retry_transfer)

        logger.info("Main window initialized")

        # Discover profiles and connect after event loop starts
        QTimer.singleShot(0, self._init_connection)

    # --- Toolbar ---

    def _setup_toolbar(self) -> None:
        toolbar = QToolBar("Main")
        toolbar.setObjectName("MainToolBar")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        self.addToolBar(toolbar)

        self._profile_combo = QComboBox()
        self._profile_combo.setToolTip("AWS Profile")
        self._profile_combo.setMinimumWidth(200)
        toolbar.addWidget(self._profile_combo)

        toolbar.addSeparator()

        self._bucket_combo = QComboBox()
        self._bucket_combo.setToolTip("S3 Bucket")
        self._bucket_combo.setMinimumWidth(280)
        toolbar.addWidget(self._bucket_combo)

        spacer = QWidget()
        spacer.setSizePolicy(
            spacer.sizePolicy().horizontalPolicy().Expanding,
            spacer.sizePolicy().verticalPolicy().Preferred,
        )
        toolbar.addWidget(spacer)

        self._settings_btn = QPushButton("Settings")
        self._settings_btn.setToolTip("Settings")
        self._settings_btn.setFlat(True)
        self._settings_btn.clicked.connect(self._open_settings)
        toolbar.addWidget(self._settings_btn)

    # --- Connection flow ---

    def _init_connection(self) -> None:
        """Discover profiles and connect to the last-used or first available."""
        self._populate_profiles()

        if self._profile_combo.count() == 0:
            self._show_setup_wizard()
            return

        # Restore last-used profile or default to first
        target_idx = 0
        if self._db:
            from s3ui.db.database import get_pref

            last_profile = get_pref(self._db, "last_profile")
            if last_profile:
                idx = self._profile_combo.findData(last_profile)
                if idx >= 0:
                    target_idx = idx

        self._profile_combo.blockSignals(True)
        self._profile_combo.setCurrentIndex(target_idx)
        self._profile_combo.blockSignals(False)
        self._on_profile_selected(target_idx)

    def _populate_profiles(self) -> None:
        """Discover AWS CLI profiles and custom keyring profiles."""
        self._profile_combo.blockSignals(True)
        self._profile_combo.clear()
        self._aws_profile_names = set()

        # AWS CLI profiles
        aws_profiles = discover_aws_profiles()
        for name in aws_profiles:
            self._profile_combo.addItem(f"{name} (AWS)", name)
            self._aws_profile_names.add(name)

        # Custom keyring profiles
        for name in self._store.list_profiles():
            if name not in self._aws_profile_names:
                self._profile_combo.addItem(name, name)

        self._profile_combo.blockSignals(False)

    def _on_profile_selected(self, index: int) -> None:
        """Handle profile combo selection — connect to the chosen profile."""
        if index < 0:
            return

        profile_name = self._profile_combo.currentData()
        if not profile_name:
            return

        if profile_name in self._aws_profile_names:
            profile = Profile(name=profile_name, is_aws_profile=True)
        else:
            profile = self._store.get_profile(profile_name)
            if not profile:
                self.set_status(f"Profile '{profile_name}' not found")
                return

        self._connect_to_profile(profile)

    def _connect_to_profile(self, profile: Profile) -> None:
        """Create an S3 client and list buckets in a background thread.

        Any in-flight connect worker is abandoned; its signals are ignored
        via the worker identity check in the handlers, so a slow previous
        connect can't overwrite a newer profile's client.
        """
        self.set_status(f"Connecting to '{profile.name}'...")
        self._bucket_combo.blockSignals(True)
        self._bucket_combo.clear()
        self._set_bucket_combo_editable(False)
        self._bucket_combo.blockSignals(False)

        # The clipboard's source bucket belongs to the old profile's namespace
        self._s3_clipboard = None
        self._paste_action.setEnabled(False)

        worker = _ConnectWorker(profile, self)
        self._connect_worker = worker
        worker.signals.connected.connect(
            lambda client, buckets, w=worker: self._on_connected(client, buckets, w)
        )
        worker.signals.failed.connect(lambda msg, w=worker: self._on_connect_failed(msg, w))
        worker.finished.connect(lambda w=worker: self._on_connect_worker_done(w))
        self._bg_workers.append(worker)
        worker.start()

    def _on_connect_worker_done(self, worker: _ConnectWorker) -> None:
        """Clean up a connect worker after it finishes."""
        if self._connect_worker is worker:
            self._connect_worker = None
        self._discard_bg_worker(worker)

    def _on_connected(
        self, client: S3Client, buckets: list[str], worker: _ConnectWorker | None = None
    ) -> None:
        """Handle successful connection — populate bucket combo."""
        if worker is not None and worker is not self._connect_worker:
            return  # Stale result from a previously selected profile

        self._s3_client = client
        self._s3_pane.set_client(client)

        self._bucket_combo.blockSignals(True)
        self._bucket_combo.clear()
        for name in sorted(buckets):
            self._bucket_combo.addItem(name, name)
        self._bucket_combo.blockSignals(False)

        profile_name = self._profile_combo.currentData()

        # Save last-used profile
        if self._db and profile_name:
            from s3ui.db.database import set_pref

            set_pref(self._db, "last_profile", profile_name)

        if not buckets:
            self._enter_manual_bucket_mode()
            return

        self.set_status(f"Connected — {len(buckets)} bucket(s)")

        # Select last-used bucket or first available
        if self._bucket_combo.count() > 0:
            target_idx = 0
            last_bucket = self._get_last_bucket()
            if last_bucket:
                idx = self._bucket_combo.findData(last_bucket)
                if idx >= 0:
                    target_idx = idx
            self._bucket_combo.blockSignals(True)
            self._bucket_combo.setCurrentIndex(target_idx)
            self._bucket_combo.blockSignals(False)
            self._on_bucket_selected(target_idx)

    def _last_bucket_key(self) -> str:
        """Per-profile pref key — same-named buckets on other profiles are different buckets."""
        profile_name = self._profile_combo.currentData() or ""
        return f"last_bucket:{profile_name}"

    def _get_last_bucket(self) -> str | None:
        if not self._db:
            return None
        from s3ui.db.database import get_pref

        # Fall back to the legacy unscoped key for existing installs
        return get_pref(self._db, self._last_bucket_key()) or get_pref(self._db, "last_bucket")

    def _enter_manual_bucket_mode(self) -> None:
        """Credentials can't list buckets — let the user type a bucket name."""
        self._set_bucket_combo_editable(True)

        last_bucket = self._get_last_bucket()

        if last_bucket:
            self._bucket_combo.blockSignals(True)
            self._bucket_combo.addItem(last_bucket, last_bucket)
            idx = self._bucket_combo.findData(last_bucket)
            self._bucket_combo.setCurrentIndex(idx)
            self._bucket_combo.blockSignals(False)
            self._on_bucket_selected(idx)
            self.set_status(f"Connected — browsing {last_bucket}")
        else:
            self.set_status("Connected — type a bucket name and press Enter")

    def _set_bucket_combo_editable(self, editable: bool) -> None:
        if editable == self._bucket_combo.isEditable():
            return
        self._bucket_combo.setEditable(editable)
        if editable:
            self._bucket_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            self._bucket_combo.lineEdit().setPlaceholderText("Bucket name...")
            self._bucket_combo.lineEdit().returnPressed.connect(self._on_bucket_typed)

    def _on_bucket_typed(self) -> None:
        """Handle a bucket name typed into the editable combo."""
        line_edit = self._bucket_combo.lineEdit()
        if line_edit is None:
            return
        name = line_edit.text().strip()
        if not name:
            return
        idx = self._bucket_combo.findData(name)
        if idx < 0:
            self._bucket_combo.blockSignals(True)
            self._bucket_combo.addItem(name, name)
            idx = self._bucket_combo.findData(name)
            self._bucket_combo.blockSignals(False)
        if self._bucket_combo.currentIndex() == idx:
            self._on_bucket_selected(idx)
        else:
            self._bucket_combo.setCurrentIndex(idx)

    def _on_connect_failed(self, error_message: str, worker: _ConnectWorker | None = None) -> None:
        """Handle connection failure."""
        if worker is not None and worker is not self._connect_worker:
            return  # Stale result from a previously selected profile
        self.set_status(f"Connection failed: {error_message}")
        logger.warning("Connection failed: %s", error_message)

    def _on_bucket_selected(self, index: int) -> None:
        """Handle bucket combo selection — switch the S3 pane to this bucket."""
        if index < 0:
            return
        bucket_name = self._bucket_combo.currentData()
        if not bucket_name:
            return

        self._s3_pane.set_bucket(bucket_name)
        self.set_status(f"Browsing {bucket_name}")

        if self._db:
            from s3ui.db.database import set_pref

            set_pref(self._db, self._last_bucket_key(), bucket_name)

        self._create_cost_tracker()
        self._create_transfer_engine()
        self._stats_action.setEnabled(True)
        self._update_edit_actions(self._s3_pane.selected_items())

    def _show_setup_wizard(self) -> None:
        """Show the setup wizard, passing already-discovered profiles."""
        aws_profiles = list(self._aws_profile_names) if self._aws_profile_names else None
        self._wizard = SetupWizard(self._store, self, aws_profiles=aws_profiles)
        self._wizard.finished.connect(self._on_wizard_finished)
        self._wizard.open()  # Window-modal, non-blocking

    def _on_wizard_finished(self, result: int) -> None:
        """Handle wizard close — defer work to run outside QDialog::done()."""
        wizard = self._wizard
        self._wizard = None
        if result != 1:  # Not QDialog.Accepted
            return
        # Defer to avoid running inside done() stack which causes SIGABRT on exception
        QTimer.singleShot(0, lambda: self._apply_wizard_result(wizard))

    def _apply_wizard_result(self, wizard: SetupWizard) -> None:
        """Apply the wizard result after the dialog has fully closed."""
        profile = wizard.get_profile()
        bucket_name = wizard.get_bucket()

        try:
            self._store.save_profile(profile)
        except Exception:
            logger.exception("Failed to save profile from wizard")

        logger.info("Setup complete: profile='%s', bucket='%s'", profile.name, bucket_name)

        if self._db and bucket_name:
            from s3ui.db.database import set_pref

            set_pref(self._db, f"last_bucket:{profile.name}", bucket_name)

        self._populate_profiles()
        idx = self._profile_combo.findData(profile.name)
        if idx >= 0:
            self._profile_combo.setCurrentIndex(idx)
            self._on_profile_selected(idx)

    # --- Cost tracking ---

    def _create_cost_tracker(self) -> None:
        """Create a CostTracker for the current bucket and attach to S3Client."""
        bucket_id = self._ensure_bucket_id()
        if bucket_id is None or not self._db:
            self._cost_tracker = None
            return

        self._cost_tracker = CostTracker(self._db, bucket_id)
        if self._s3_client:
            self._s3_client.set_cost_tracker(self._cost_tracker)
        self._cost_action.setEnabled(True)
        self._update_cost_label()

    def _update_cost_label(self) -> None:
        """Refresh the status bar cost estimate."""
        if not self._cost_tracker:
            self._cost_label.setText("")
            return
        estimate = self._cost_tracker.get_monthly_estimate()
        self._cost_label.setText(f"Est. ${estimate:.4f}/mo")

    def _open_cost_dashboard(self) -> None:
        """Open the cost dashboard dialog."""
        from s3ui.ui.cost_dialog import CostDialog

        dialog = CostDialog(cost_tracker=self._cost_tracker, parent=self)
        dialog.exec()
        self._update_cost_label()

    # --- Upload / Download / Transfer wiring ---

    def _create_transfer_engine(self) -> None:
        """Create a TransferEngine for the current bucket + client."""
        bucket_name = self._bucket_combo.currentData()
        if not self._s3_client or not self._db or not bucket_name:
            return

        profile_name = self._profile_combo.currentData() or ""
        engine = TransferEngine(self._s3_client, self._db, bucket_name, profile=profile_name)
        self.set_transfer_engine(engine)
        engine.restore_pending()

        # Abort multipart uploads orphaned on S3 (>24h old, not in our DB) —
        # they otherwise accumulate invisible storage costs forever
        if bucket_name not in self._orphan_cleaned_buckets:
            self._orphan_cleaned_buckets.add(bucket_name)
            threading.Thread(
                target=engine.cleanup_orphaned_uploads,
                daemon=True,
                name="s3ui-orphan-cleanup",
            ).start()

    def _ensure_bucket_id(self) -> int | None:
        """Get or create the bucket record in the database, return its ID."""
        if self._db is None:
            return None
        bucket_name = self._bucket_combo.currentData()
        if not bucket_name:
            return None

        profile_name = self._profile_combo.currentData() or ""
        row = self._db.fetchone(
            "SELECT id FROM buckets WHERE name = ? AND profile = ? ORDER BY id DESC LIMIT 1",
            (bucket_name, profile_name),
        )
        if row:
            return row["id"]

        cursor = self._db.execute(
            "INSERT INTO buckets (name, region, profile) VALUES (?, ?, ?)",
            (bucket_name, "", profile_name),
        )
        return cursor.lastrowid

    def _on_upload_requested(self, paths: list[str]) -> None:
        """Handle upload request from local pane context menu."""
        self._enqueue_uploads(paths)

    def _on_files_dropped(self, paths: list[str]) -> None:
        """Handle files dropped onto S3 pane."""
        self._enqueue_uploads(paths)

    def _enqueue_uploads(self, paths: list[str]) -> None:
        """Create transfer records and enqueue uploads."""
        from pathlib import Path

        if not self._transfer_engine or not self._db:
            self.set_status("Not connected — cannot upload")
            return

        bucket_id = self._ensure_bucket_id()
        if bucket_id is None:
            self.set_status("No bucket selected")
            return

        prefix = self._s3_pane.current_prefix()
        count = 0

        for path_str in paths:
            path = Path(path_str)
            if path.is_dir():
                for file_path in path.rglob("*"):
                    if file_path.is_file():
                        rel = file_path.relative_to(path.parent)
                        key = prefix + str(rel).replace("\\", "/")
                        self._create_upload_transfer(bucket_id, key, file_path)
                        count += 1
            elif path.is_file():
                key = prefix + path.name
                self._create_upload_transfer(bucket_id, key, path)
                count += 1

        if count:
            self.set_status(f"Uploading {count} file(s)...")

    def _create_upload_transfer(self, bucket_id: int, key: str, local_path) -> None:
        """Insert a single upload transfer record and enqueue it."""
        size = local_path.stat().st_size
        tid = self._db.execute(
            "INSERT INTO transfers "
            "(bucket_id, object_key, direction, local_path, status, total_bytes, transferred) "
            "VALUES (?, ?, 'upload', ?, 'queued', ?, 0)",
            (bucket_id, key, str(local_path), size),
        ).lastrowid

        self._transfer_panel.add_transfer(tid)
        self._transfer_engine.enqueue(tid)

    def _on_download_requested(self, items: list) -> None:
        """Handle download request from S3 pane context menu."""
        from pathlib import Path

        if not self._transfer_engine or not self._db:
            self.set_status("Not connected — cannot download")
            return

        bucket_id = self._ensure_bucket_id()
        if bucket_id is None:
            self.set_status("No bucket selected")
            return

        dest_dir = Path(self._local_pane.current_path())
        count = 0
        conflict_state: dict = {}

        for item in items:
            if item.is_prefix:
                self._enqueue_folder_download(item.key, dest_dir, bucket_id)
                continue
            filename = item.name or item.key.rsplit("/", 1)[-1]
            local_path = self._safe_local_path(dest_dir, filename)
            if local_path is None:
                logger.warning("Skipping unsafe object key: %s", item.key)
                continue
            local_path = self._resolve_local_conflict(local_path, conflict_state)
            if local_path is None:
                if conflict_state.get("cancelled"):
                    break
                continue
            size = item.size or 0

            self._create_download_transfer(bucket_id, item.key, local_path, size)
            count += 1

        if count:
            self.set_status(f"Downloading {count} file(s)...")

    def _create_download_transfer(self, bucket_id: int, key: str, local_path, size: int) -> None:
        """Insert a single download transfer record and enqueue it."""
        tid = self._db.execute(
            "INSERT INTO transfers "
            "(bucket_id, object_key, direction, local_path, status, total_bytes, transferred) "
            "VALUES (?, ?, 'download', ?, 'queued', ?, 0)",
            (bucket_id, key, str(local_path), size),
        ).lastrowid

        self._transfer_panel.add_transfer(tid)
        self._transfer_engine.enqueue(tid)

    def _enqueue_folder_download(self, prefix: str, dest_dir, bucket_id: int) -> None:
        """Enumerate a folder in the background, then enqueue its files."""
        bucket = self._bucket_combo.currentData()
        self.set_status(f"Listing '{prefix}'...")

        worker = _EnumerateWorker(self._s3_client, bucket, prefix, self)
        worker.signals.finished.connect(
            lambda p, files: self._on_folder_enumerated(p, files, dest_dir, bucket, bucket_id)
        )
        worker.signals.failed.connect(
            lambda p, msg: self.set_status(f"Failed to list '{p}': {msg}")
        )
        worker.finished.connect(lambda w=worker: self._discard_bg_worker(w))
        self._bg_workers.append(worker)
        worker.start()

    def _on_folder_enumerated(
        self, prefix: str, files: list, dest_dir, bucket: str, bucket_id: int
    ) -> None:
        """Enqueue downloads for a folder's files, preserving its structure."""
        if not self._transfer_engine or self._bucket_combo.currentData() != bucket:
            self.set_status("Folder download cancelled — bucket changed")
            return
        if not files:
            self.set_status(f"'{prefix}' is empty — nothing to download")
            return

        # Recreate the folder itself in the destination: for prefix "a/b/",
        # object "a/b/c/d.txt" lands at dest_dir/"b/c/d.txt"
        parent_len = prefix.rstrip("/").rfind("/") + 1
        count = 0
        conflict_state: dict = {}

        for obj in files:
            local_path = self._safe_local_path(dest_dir, obj.key[parent_len:])
            if local_path is None:
                logger.warning("Skipping unsafe object key: %s", obj.key)
                continue
            resolved = self._resolve_local_conflict(local_path, conflict_state)
            if resolved is None:
                if conflict_state.get("cancelled"):
                    break
                continue
            resolved.parent.mkdir(parents=True, exist_ok=True)
            self._create_download_transfer(bucket_id, obj.key, resolved, obj.size or 0)
            count += 1

        self.set_status(f"Downloading {count} file(s) from '{prefix}'...")

    @staticmethod
    def _safe_local_path(dest_dir, rel: str):
        """Map an S3-relative path under dest_dir, rejecting traversal.

        S3 keys are arbitrary strings — a key containing '..' segments must
        not escape the chosen download directory.
        """
        parts = [p for p in rel.split("/") if p not in ("", ".")]
        if not parts or any(p == ".." for p in parts):
            return None
        if sys.platform == "win32" and any("\\" in p or ":" in p for p in parts):
            return None
        return dest_dir.joinpath(*parts)

    def _on_delete_requested(self, items: list) -> None:
        """Handle delete request from S3 pane context menu."""
        if not self._s3_client:
            self.set_status("Not connected — cannot delete")
            return

        bucket = self._bucket_combo.currentData()
        if not bucket:
            return

        if not items:
            return

        names = [i.name for i in items[:5]]
        if len(items) > 5:
            names.append(f"... and {len(items) - 5} more")
        listing = "\n".join(names)

        prefixes = [i.key for i in items if i.is_prefix]
        warning = ""
        if prefixes:
            warning = "\n\nFolders will be deleted with all of their contents."

        reply = QMessageBox.question(
            self,
            "Delete Objects",
            f"Delete {len(items)} item(s)?\n\n{listing}{warning}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        keys = [i.key for i in items if not i.is_prefix]
        self.set_status(f"Deleting {len(items)} item(s)...")

        worker = _DeleteWorker(self._s3_client, bucket, keys, prefixes, self)
        worker.signals.finished.connect(self._on_delete_finished)
        worker.signals.failed.connect(lambda msg: self.set_status(f"Delete failed: {msg}"))
        worker.finished.connect(lambda w=worker: self._discard_bg_worker(w))
        self._bg_workers.append(worker)
        worker.start()

    def _on_delete_finished(self, deleted_keys: list[str], failed_keys: list[str]) -> None:
        """Handle completed deletion — update S3 pane and status."""
        self._s3_pane.notify_delete_complete(deleted_keys)
        if failed_keys:
            self.set_status(f"Deleted {len(deleted_keys)} object(s) — {len(failed_keys)} failed")
        else:
            self.set_status(f"Deleted {len(deleted_keys)} object(s)")

    def _on_new_folder_requested(self) -> None:
        """Prompt for folder name and create it as an empty S3 object."""
        if not self._s3_client:
            self.set_status("Not connected — cannot create folder")
            return

        bucket = self._bucket_combo.currentData()
        if not bucket:
            return

        name, ok = QInputDialog.getText(self, "New Folder", "Folder name:")
        if not ok or not name.strip():
            return

        name = name.strip().rstrip("/")
        prefix = self._s3_pane.current_prefix()
        key = f"{prefix}{name}/"

        try:
            self._s3_client.put_object(bucket, key, b"")
            self._s3_pane.notify_new_folder(key, name)
            self.set_status(f"Created folder '{name}'")
        except Exception as e:
            logger.warning("Failed to create folder '%s': %s", key, e)
            self.set_status(f"Failed to create folder: {e}")

    # --- Copy / Paste / Rename / Get Info / Stats ---

    def _update_edit_actions(self, selected: list) -> None:
        """Enable Edit menu actions based on the S3 pane selection."""
        connected = self._s3_client is not None
        files = [i for i in selected if not i.is_prefix]
        self._copy_action.setEnabled(connected and bool(files))
        self._paste_action.setEnabled(connected and self._s3_clipboard is not None)
        self._delete_action.setEnabled(connected and bool(selected))
        self._rename_action.setEnabled(connected and len(selected) == 1 and len(files) == 1)

    def _on_copy_items(self, items: list) -> None:
        """Store copied S3 files on the internal clipboard."""
        bucket = self._bucket_combo.currentData()
        files = [i for i in items if not i.is_prefix]
        if not bucket or not files:
            return
        self._s3_clipboard = (bucket, files)
        self._paste_action.setEnabled(True)
        self.set_status(f"Copied {len(files)} file(s)")

    def _on_copy_selected(self) -> None:
        self._on_copy_items(self._s3_pane.selected_items())

    def _on_paste(self) -> None:
        """Server-side copy of clipboard files into the current prefix."""
        if not self._s3_client or not self._s3_clipboard:
            return
        dst_bucket = self._bucket_combo.currentData()
        if not dst_bucket:
            return
        src_bucket, items = self._s3_clipboard
        prefix = self._s3_pane.current_prefix()
        client = self._s3_client
        ops: list = []  # filled by the worker with the final (item, dst_key) pairs

        def do_copies() -> None:
            for item in items:
                # Never overwrite: pick a free destination name ("x copy.txt",
                # "x copy 2.txt", ...) if the key is taken
                dst_key = self._free_dest_key(client, dst_bucket, prefix, item.name)
                client.copy_object(src_bucket, item.key, dst_bucket, dst_key)
                ops.append((item, dst_key))

        self.set_status(f"Copying {len(items)} file(s)...")
        self._run_key_op(do_copies, ops, self._on_paste_finished)

    def _on_paste_finished(self, ops: list) -> None:
        for item, dst_key in ops:
            self._s3_pane.notify_copy_complete(dst_key, item.size or 0)
        self._update_cost_label()
        self.set_status(f"Copied {len(ops)} file(s)")

    @staticmethod
    def _key_exists(client, bucket: str, key: str) -> bool:
        try:
            client.head_object(bucket, key)
        except Exception:
            return False
        return True

    @staticmethod
    def _free_dest_key(client, bucket: str, prefix: str, name: str) -> str:
        """Return prefix+name, or a 'name copy (n)' variant if that key is taken."""
        from pathlib import PurePosixPath

        candidate = prefix + name
        if not MainWindow._key_exists(client, bucket, candidate):
            return candidate
        p = PurePosixPath(name)
        n = 1
        while True:
            suffix = " copy" if n == 1 else f" copy {n}"
            candidate = f"{prefix}{p.stem}{suffix}{p.suffix}"
            if not MainWindow._key_exists(client, bucket, candidate):
                return candidate
            n += 1

    def _on_rename_action(self) -> None:
        selected = self._s3_pane.selected_items()
        if len(selected) == 1 and not selected[0].is_prefix:
            self._on_rename_requested(selected[0])

    def _on_rename_requested(self, item) -> None:
        """Rename a single S3 object via server-side copy + delete."""
        if not self._s3_client:
            return
        bucket = self._bucket_combo.currentData()
        if not bucket:
            return

        new_name, ok = QInputDialog.getText(self, "Rename", "New name:", text=item.name)
        new_name = new_name.strip()
        if not ok or not new_name or new_name == item.name:
            return
        if "/" in new_name:
            self.set_status("Rename failed: name cannot contain '/'")
            return

        old_key = item.key
        new_key = old_key[: len(old_key) - len(item.name)] + new_name
        client = self._s3_client

        def do_rename() -> None:
            if self._key_exists(client, bucket, new_key):
                raise RuntimeError(f"'{new_name}' already exists")
            client.copy_object(bucket, old_key, bucket, new_key)
            client.delete_object(bucket, old_key)

        self.set_status(f"Renaming '{item.name}'...")
        self._run_key_op(do_rename, (old_key, new_key, new_name), self._on_rename_finished)

    def _on_rename_finished(self, payload: tuple) -> None:
        old_key, new_key, new_name = payload
        self._s3_pane.notify_rename_complete(old_key, new_key, new_name)
        self.set_status(f"Renamed to '{new_name}'")

    def _run_key_op(self, fn, payload, on_finished) -> None:
        """Run an S3 key operation in a background worker."""
        worker = _KeyOpWorker(fn, payload, self)
        worker.signals.finished.connect(on_finished)
        worker.signals.failed.connect(lambda msg: self.set_status(f"Operation failed: {msg}"))
        worker.finished.connect(lambda w=worker: self._discard_bg_worker(w))
        self._bg_workers.append(worker)
        worker.start()

    def _discard_bg_worker(self, worker) -> None:
        if worker in self._bg_workers:
            self._bg_workers.remove(worker)
        worker.deleteLater()

    def _on_get_info(self, item) -> None:
        from s3ui.ui.get_info import GetInfoDialog

        GetInfoDialog(item, self).exec()

    def _open_stats_dialog(self) -> None:
        from s3ui.ui.stats_dialog import StatsDialog

        bucket = self._bucket_combo.currentData()
        dialog = StatsDialog(s3_client=self._s3_client, bucket=bucket, db=self._db, parent=self)
        dialog.exec()

    def _resolve_local_conflict(self, local_path, state: dict):
        """Resolve a destination collision. Returns the path to use, or None to skip.

        `state` persists an apply-to-all choice (and cancellation) across one
        download batch.
        """
        if state.get("cancelled"):
            return None
        if not local_path.exists():
            return local_path

        resolution = state.get("apply_all")
        if resolution is None:
            from s3ui.ui.name_conflict import NameConflictDialog

            dialog = NameConflictDialog(local_path.name, self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                state["cancelled"] = True
                return None
            resolution = dialog.resolution()
            if dialog.apply_to_all():
                state["apply_all"] = resolution

        from s3ui.ui.name_conflict import ConflictResolution

        if resolution == ConflictResolution.REPLACE:
            return local_path
        if resolution == ConflictResolution.SKIP:
            return None
        # KEEP_BOTH — find a free "name (n).ext"
        for n in range(1, 1000):
            candidate = local_path.with_name(f"{local_path.stem} ({n}){local_path.suffix}")
            if not candidate.exists():
                return candidate
        return None

    def _engine_for(self, tid: int):
        """The engine with a live worker for this transfer, else the current one.

        Retired engines' workers keep running after a bucket switch, so
        pause/cancel must reach the engine that actually holds the events.
        """
        for engine in [self._transfer_engine, *self._retired_engines]:
            if engine is not None and engine.owns(tid):
                return engine
        return self._transfer_engine

    def _on_pause_transfer(self, tid: int) -> None:
        engine = self._engine_for(tid)
        if engine:
            engine.pause(tid)

    def _on_resume_transfer(self, tid: int) -> None:
        # Resume always goes through the current engine — a retired one
        # no longer accepts work
        if self._transfer_engine:
            self._transfer_engine.resume(tid)

    def _on_cancel_transfer(self, tid: int) -> None:
        engine = self._engine_for(tid)
        if engine:
            engine.cancel(tid)

    def _on_retry_transfer(self, tid: int) -> None:
        if self._transfer_engine:
            self._transfer_engine.retry(tid)

    # --- Central widget: splitter with local + S3 panes ---

    def _setup_central(self) -> None:
        self._splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left pane: local files
        self._local_pane = LocalPaneWidget()
        self._splitter.addWidget(self._local_pane)

        # Right pane: S3
        self._s3_pane = S3PaneWidget()
        self._s3_pane.status_message.connect(self.set_status)
        self._splitter.addWidget(self._s3_pane)

        self._splitter.setSizes([450, 450])
        self.setCentralWidget(self._splitter)

    # --- Transfer panel dock ---

    def _setup_transfer_dock(self) -> None:
        self._transfer_dock = QDockWidget("Transfers", self)
        self._transfer_dock.setObjectName("TransfersDock")
        self._transfer_dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea)
        self._transfer_dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable)
        self._transfer_panel = TransferPanelWidget(db=self._db)
        self._transfer_panel.setMinimumHeight(80)
        self._transfer_dock.setWidget(self._transfer_panel)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self._transfer_dock)

    @property
    def transfer_panel(self) -> TransferPanelWidget:
        return self._transfer_panel

    # --- System tray icon (for notifications) ---

    def _setup_tray_icon(self) -> None:
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray_icon = QSystemTrayIcon(self)
            self._tray_icon.setIcon(self.windowIcon())
            self._tray_icon.setToolTip("S3UI")
            # Don't show in tray by default — just use it for notifications
        else:
            self._tray_icon = None

    def _notify(self, title: str, message: str) -> None:
        """Show a system notification if the app is not in the foreground."""
        if self._tray_icon is None:
            return
        if self.isActiveWindow():
            return
        # Temporarily show to deliver the message, then hide
        self._tray_icon.show()
        self._tray_icon.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 5000)

    # --- Transfer engine integration ---

    def set_transfer_engine(self, engine) -> None:
        """Wire a TransferEngine to the panel and optimistic update signals."""
        old = self._transfer_engine
        if old is not None:
            # Optimistic pane updates would target the newly selected bucket
            old.transfer_finished.disconnect(self._on_transfer_finished)
            # Keep the old engine alive until its workers exit — dropping the
            # last reference would destroy its QThreadPool under live threads
            self._retired_engines.append(old)
            old.drained.connect(lambda o=old: self._discard_retired_engine(o))
            old.shutdown()

        self._transfer_engine = engine
        self._transfer_panel.set_engine(engine)

        # Wire transfer completion → optimistic S3 pane updates + notifications
        engine.transfer_finished.connect(self._on_transfer_finished)

    def _discard_retired_engine(self, engine) -> None:
        if engine in self._retired_engines:
            self._retired_engines.remove(engine)

    def _on_transfer_finished(self, transfer_id: int) -> None:
        """Handle transfer completion: optimistic update + notification."""
        if self._db is None:
            return

        row = self._db.fetchone("SELECT * FROM transfers WHERE id = ?", (transfer_id,))
        if not row:
            return

        if row["direction"] == "upload":
            key = row["object_key"]
            size = row["total_bytes"] or 0
            self._s3_pane.notify_upload_complete(key, size)

        # Refresh cost estimate after transfer
        self._update_cost_label()

        # Notification for large transfers when app is in background
        total = row["total_bytes"] or 0
        if total >= NOTIFY_SIZE_THRESHOLD:
            direction = "Upload" if row["direction"] == "upload" else "Download"
            from pathlib import Path

            filename = Path(row["local_path"]).name
            self._notify(f"{direction} complete", filename)

    # --- Quick-open (double-click file in S3 pane) ---

    def _on_quick_open(self, item) -> None:
        """Download an S3 file to temp in the background and open it."""
        if not self._s3_pane._s3_client or not self._s3_pane._bucket:
            return

        size = item.size or 0
        if size > QUICK_OPEN_THRESHOLD:
            # Large file — emit download_requested for normal transfer queue
            self._s3_pane.download_requested.emit([item])
            return

        import hashlib

        bucket = self._s3_pane._bucket
        filename = item.name or item.key.rsplit("/", 1)[-1]
        # Unique subdir per bucket+key so same-named files from different
        # prefixes don't open stale content
        subdir = hashlib.sha1(f"{bucket}/{item.key}".encode()).hexdigest()[:12]
        local_path = TEMP_DIR / subdir / filename

        self.set_status(f"Opening '{filename}'...")
        worker = _QuickOpenWorker(self._s3_pane._s3_client, bucket, item.key, local_path, self)
        worker.signals.finished.connect(self._on_quick_open_ready)
        worker.signals.failed.connect(self._on_quick_open_failed)
        worker.finished.connect(lambda w=worker: self._discard_bg_worker(w))
        self._bg_workers.append(worker)
        worker.start()

    def _on_quick_open_ready(self, path: str) -> None:
        self._temp_files.append(path)
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        self.set_status("Ready")

    def _on_quick_open_failed(self, key: str, msg: str) -> None:
        logger.warning("Quick-open failed for %s: %s", key, msg)
        self.set_status(f"Failed to open: {msg}")

    # --- Status bar ---

    def _setup_status_bar(self) -> None:
        sb = self.statusBar()
        self._status_label = QLabel("Ready")
        self._object_count_label = QLabel("")
        self._total_size_label = QLabel("")
        self._cost_label = QLabel("")

        sb.addWidget(self._status_label, 1)
        sb.addPermanentWidget(self._object_count_label)
        sb.addPermanentWidget(self._total_size_label)
        sb.addPermanentWidget(self._cost_label)

    def set_status(self, text: str) -> None:
        self._status_label.setText(text)

    @property
    def s3_pane(self) -> S3PaneWidget:
        return self._s3_pane

    @property
    def local_pane(self) -> LocalPaneWidget:
        return self._local_pane

    @property
    def profile_combo(self) -> QComboBox:
        return self._profile_combo

    @property
    def bucket_combo(self) -> QComboBox:
        return self._bucket_combo

    def _open_settings(self) -> None:
        current_profile = self._profile_combo.currentData()
        dialog = SettingsDialog(store=self._store, db=self._db, parent=self)
        dialog.exec()
        # Refresh profile list in case credentials were added/removed
        self._populate_profiles()
        # Switch to newly added profile, otherwise restore previous selection
        new_profile = dialog.last_added_profile
        target = new_profile or current_profile
        if target:
            idx = self._profile_combo.findData(target)
            if idx >= 0:
                self._profile_combo.blockSignals(True)
                self._profile_combo.setCurrentIndex(idx)
                self._profile_combo.blockSignals(False)
                if new_profile:
                    self._on_profile_selected(idx)

    # --- Keyboard shortcuts ---

    def _setup_keyboard_shortcuts(self) -> None:
        # Focus switching: Ctrl+1 → local pane, Ctrl+2 → S3 pane
        focus_local = QAction("Focus Local Pane", self)
        focus_local.setShortcut(QKeySequence("Ctrl+1"))
        focus_local.triggered.connect(self._focus_local_pane)
        self.addAction(focus_local)

        focus_s3 = QAction("Focus S3 Pane", self)
        focus_s3.setShortcut(QKeySequence("Ctrl+2"))
        focus_s3.triggered.connect(self._focus_s3_pane)
        self.addAction(focus_s3)

    def _focus_local_pane(self) -> None:
        self._local_pane.setFocus()

    def _focus_s3_pane(self) -> None:
        self._s3_pane.setFocus()

    # --- Window state save/restore ---

    def _save_state(self) -> None:
        """Save window geometry, splitter position, and dock state to preferences."""
        if self._db is None:
            return

        from s3ui.db.database import set_pref

        set_pref(self._db, "window_geometry", self.saveGeometry().toBase64().data().decode())
        set_pref(self._db, "window_state", self.saveState().toBase64().data().decode())
        set_pref(self._db, "splitter_state", self._splitter.saveState().toBase64().data().decode())
        set_pref(
            self._db,
            "transfer_dock_visible",
            "true" if self._transfer_dock.isVisible() else "false",
        )
        set_pref(self._db, "local_pane_path", self._local_pane.current_path())

    def _restore_state(self) -> None:
        """Restore window geometry, splitter position, and dock state."""
        if self._db is None:
            return

        from s3ui.db.database import get_bool_pref, get_pref

        geom = get_pref(self._db, "window_geometry")
        if geom:
            self.restoreGeometry(QByteArray.fromBase64(geom.encode()))

        state = get_pref(self._db, "window_state")
        if state:
            self.restoreState(QByteArray.fromBase64(state.encode()))

        splitter = get_pref(self._db, "splitter_state")
        if splitter:
            self._splitter.restoreState(QByteArray.fromBase64(splitter.encode()))

        dock_vis = get_bool_pref(self._db, "transfer_dock_visible", default=True)
        self._transfer_dock.setVisible(dock_vis)

        local_path = get_pref(self._db, "local_pane_path")
        if local_path:
            from pathlib import Path

            if Path(local_path).is_dir():
                self._local_pane.navigate_to(local_path, record_history=False)

    def closeEvent(self, event) -> None:
        self._save_state()
        self._cleanup_temp_files()
        # Join outstanding QThreads — destroying a running QThread aborts Qt
        for worker in list(self._bg_workers):
            worker.wait(2000)
        if self._tray_icon:
            self._tray_icon.hide()
        super().closeEvent(event)

    def _cleanup_temp_files(self) -> None:
        """Remove temp files (and their per-key subdirs) from quick-open."""
        import contextlib
        from pathlib import Path

        for path_str in self._temp_files:
            path = Path(path_str)
            with contextlib.suppress(OSError):
                path.unlink()
            # Quick-open files live in a hash subdir under TEMP_DIR
            if path.parent != TEMP_DIR and path.parent.parent == TEMP_DIR:
                with contextlib.suppress(OSError):
                    path.parent.rmdir()
        self._temp_files.clear()

    # --- Show Log File ---

    def _open_log_directory(self) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(LOG_DIR)))

    # --- Menus ---

    def _setup_menus(self) -> None:
        menu_bar = self.menuBar()

        # File menu
        file_menu = menu_bar.addMenu("&File")

        settings_action = QAction("&Settings...", self)
        settings_action.triggered.connect(self._open_settings)
        if sys.platform == "darwin":
            settings_action.setShortcut(QKeySequence("Ctrl+,"))
            settings_action.setMenuRole(QAction.MenuRole.PreferencesRole)
        file_menu.addAction(settings_action)

        wizard_action = QAction("Setup &Wizard...", self)
        wizard_action.triggered.connect(self._show_setup_wizard)
        file_menu.addAction(wizard_action)

        file_menu.addSeparator()

        quit_action = QAction("&Quit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        if sys.platform == "darwin":
            quit_action.setMenuRole(QAction.MenuRole.QuitRole)
        file_menu.addAction(quit_action)

        # Edit menu
        edit_menu = menu_bar.addMenu("&Edit")
        self._copy_action = QAction("&Copy", self)
        self._copy_action.setShortcut(QKeySequence.StandardKey.Copy)
        self._copy_action.setEnabled(False)
        self._copy_action.triggered.connect(self._on_copy_selected)
        edit_menu.addAction(self._copy_action)

        self._paste_action = QAction("&Paste", self)
        self._paste_action.setShortcut(QKeySequence.StandardKey.Paste)
        self._paste_action.setEnabled(False)
        self._paste_action.triggered.connect(self._on_paste)
        edit_menu.addAction(self._paste_action)

        edit_menu.addSeparator()

        self._delete_action = QAction("&Delete", self)
        self._delete_action.setShortcut(QKeySequence.StandardKey.Delete)
        self._delete_action.setEnabled(False)
        self._delete_action.triggered.connect(
            lambda: self._on_delete_requested(self._s3_pane.selected_items())
        )
        edit_menu.addAction(self._delete_action)

        self._rename_action = QAction("&Rename", self)
        self._rename_action.setEnabled(False)
        self._rename_action.triggered.connect(self._on_rename_action)
        edit_menu.addAction(self._rename_action)

        # View menu
        view_menu = menu_bar.addMenu("&View")
        self._hidden_files_action = QAction("Show &Hidden Files", self)
        self._hidden_files_action.setCheckable(True)
        self._hidden_files_action.toggled.connect(
            lambda checked: self._local_pane.set_show_hidden(checked)
        )
        view_menu.addAction(self._hidden_files_action)

        self._toggle_transfers_action = QAction("Show &Transfers", self)
        self._toggle_transfers_action.setCheckable(True)
        self._toggle_transfers_action.setChecked(True)
        self._toggle_transfers_action.toggled.connect(self._transfer_dock.setVisible)
        view_menu.addAction(self._toggle_transfers_action)

        # Go menu
        go_menu = menu_bar.addMenu("&Go")
        back_action = QAction("&Back", self)
        back_action.setShortcut(QKeySequence("Alt+Left"))
        back_action.triggered.connect(self._local_pane.go_back)
        go_menu.addAction(back_action)

        forward_action = QAction("&Forward", self)
        forward_action.setShortcut(QKeySequence("Alt+Right"))
        forward_action.triggered.connect(self._local_pane.go_forward)
        go_menu.addAction(forward_action)

        up_action = QAction("Enclosing &Folder", self)
        up_action.setShortcut(QKeySequence("Alt+Up"))
        up_action.triggered.connect(self._local_pane.go_up)
        go_menu.addAction(up_action)

        # Bucket menu
        bucket_menu = menu_bar.addMenu("&Bucket")
        self._refresh_action = QAction("&Refresh", self)
        self._refresh_action.setShortcut(QKeySequence("Ctrl+R"))
        self._refresh_action.triggered.connect(self._s3_pane.refresh)
        bucket_menu.addAction(self._refresh_action)

        bucket_menu.addSeparator()

        self._stats_action = QAction("Bucket &Stats...", self)
        self._stats_action.setEnabled(False)
        self._stats_action.triggered.connect(self._open_stats_dialog)
        bucket_menu.addAction(self._stats_action)

        self._cost_action = QAction("&Cost Dashboard...", self)
        self._cost_action.setEnabled(False)
        self._cost_action.triggered.connect(self._open_cost_dashboard)
        bucket_menu.addAction(self._cost_action)

        # Help menu
        help_menu = menu_bar.addMenu("&Help")
        self._show_log_action = QAction("Show &Log File", self)
        self._show_log_action.triggered.connect(self._open_log_directory)
        help_menu.addAction(self._show_log_action)
