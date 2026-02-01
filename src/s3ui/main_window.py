"""Main application window — dual-pane layout with toolbar, menus, status bar."""

import logging
import sys

from PyQt6.QtCore import QByteArray, Qt, QUrl
from PyQt6.QtGui import QAction, QDesktopServices, QIcon, QKeySequence
from PyQt6.QtWidgets import (
    QComboBox,
    QDockWidget,
    QLabel,
    QMainWindow,
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
from s3ui.ui.local_pane import LocalPaneWidget
from s3ui.ui.s3_pane import S3PaneWidget
from s3ui.ui.settings_dialog import SettingsDialog
from s3ui.ui.transfer_panel import TransferPanelWidget

logger = logging.getLogger("s3ui.main_window")


class MainWindow(QMainWindow):
    def __init__(self, db=None) -> None:
        super().__init__()
        self._db = db
        self._transfer_engine = None
        self._tray_icon: QSystemTrayIcon | None = None
        self._temp_files: list[str] = []

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

        # Wire double-click-to-open
        self._s3_pane.quick_open_requested.connect(self._on_quick_open)
        logger.info("Main window initialized")

    # --- Toolbar ---

    def _setup_toolbar(self) -> None:
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        self.addToolBar(toolbar)

        self._profile_combo = QComboBox()
        self._profile_combo.setToolTip("AWS Profile")
        self._profile_combo.setMinimumWidth(100)
        toolbar.addWidget(self._profile_combo)

        toolbar.addSeparator()

        self._bucket_combo = QComboBox()
        self._bucket_combo.setToolTip("S3 Bucket")
        self._bucket_combo.setMinimumWidth(150)
        toolbar.addWidget(self._bucket_combo)

        spacer = QWidget()
        spacer.setSizePolicy(
            spacer.sizePolicy().horizontalPolicy().Expanding,
            spacer.sizePolicy().verticalPolicy().Preferred,
        )
        toolbar.addWidget(spacer)

        self._settings_action = QAction("⚙", self)
        self._settings_action.setToolTip("Settings")
        self._settings_action.triggered.connect(self._open_settings)
        toolbar.addAction(self._settings_action)

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
        self._transfer_dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea)
        self._transfer_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable
        )
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
            self._tray_icon.setIcon(
                QIcon.fromTheme("folder-cloud", self.windowIcon())
            )
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
        self._transfer_engine = engine
        self._transfer_panel.set_engine(engine)

        # Wire transfer completion → optimistic S3 pane updates + notifications
        engine.transfer_finished.connect(self._on_transfer_finished)

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

        # Notification for large transfers when app is in background
        total = row["total_bytes"] or 0
        if total >= NOTIFY_SIZE_THRESHOLD:
            direction = "Upload" if row["direction"] == "upload" else "Download"
            from pathlib import Path

            filename = Path(row["local_path"]).name
            self._notify(f"{direction} complete", filename)

    # --- Quick-open (double-click file in S3 pane) ---

    def _on_quick_open(self, item) -> None:
        """Download an S3 file to temp and open with system default app."""
        if not self._s3_pane._s3_client or not self._s3_pane._bucket:
            return

        size = item.size or 0
        if size > QUICK_OPEN_THRESHOLD:
            # Large file — emit download_requested for normal transfer queue
            self._s3_pane.download_requested.emit([item])
            return

        # Small file — download inline to temp dir
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        filename = item.name or item.key.rsplit("/", 1)[-1]
        local_path = TEMP_DIR / filename

        try:
            body = self._s3_pane._s3_client.get_object(
                self._s3_pane._bucket, item.key
            )
            data = body.read()
            local_path.write_bytes(data)
            self._temp_files.append(str(local_path))
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(local_path)))
        except Exception as e:
            logger.warning("Quick-open failed for %s: %s", item.key, e)
            self.set_status(f"Failed to open: {e}")

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
        dialog = SettingsDialog(parent=self)
        dialog.exec()

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
            self._db, "transfer_dock_visible",
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
        if self._tray_icon:
            self._tray_icon.hide()
        super().closeEvent(event)

    def _cleanup_temp_files(self) -> None:
        """Remove any temp files downloaded for quick-open."""
        import contextlib
        from pathlib import Path

        for path_str in self._temp_files:
            with contextlib.suppress(OSError):
                Path(path_str).unlink()
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
        edit_menu.addAction(self._copy_action)

        self._paste_action = QAction("&Paste", self)
        self._paste_action.setShortcut(QKeySequence.StandardKey.Paste)
        self._paste_action.setEnabled(False)
        edit_menu.addAction(self._paste_action)

        edit_menu.addSeparator()

        self._delete_action = QAction("&Delete", self)
        self._delete_action.setShortcut(QKeySequence.StandardKey.Delete)
        self._delete_action.setEnabled(False)
        edit_menu.addAction(self._delete_action)

        self._rename_action = QAction("&Rename", self)
        self._rename_action.setEnabled(False)
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
        bucket_menu.addAction(self._stats_action)

        self._cost_action = QAction("&Cost Dashboard...", self)
        self._cost_action.setEnabled(False)
        bucket_menu.addAction(self._cost_action)

        # Help menu
        help_menu = menu_bar.addMenu("&Help")
        self._show_log_action = QAction("Show &Log File", self)
        self._show_log_action.triggered.connect(self._open_log_directory)
        help_menu.addAction(self._show_log_action)
