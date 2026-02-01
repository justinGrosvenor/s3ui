"""S3 file browser pane — right side of the dual-pane browser."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PyQt6.QtCore import (
    QModelIndex,
    QObject,
    QSortFilterProxyModel,
    Qt,
    QThread,
    pyqtSignal,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from s3ui.constants import NAV_HISTORY_MAX
from s3ui.core.listing_cache import ListingCache
from s3ui.models.s3_objects import S3Item, S3ObjectModel, _format_size
from s3ui.ui.breadcrumb_bar import BreadcrumbBar

if TYPE_CHECKING:
    from s3ui.core.s3_client import S3Client

logger = logging.getLogger("s3ui.s3_pane")


class _FetchSignals(QObject):
    """Signals emitted by the fetch worker."""

    page_ready = pyqtSignal(str, list, bool, int)  # prefix, items, is_first_page, fetch_id
    listing_complete = pyqtSignal(str, list, int)  # prefix, all_items, fetch_id
    error = pyqtSignal(str, str, int)  # prefix, error_message, fetch_id


class _FetchWorker(QThread):
    """Background thread for fetching S3 listings."""

    def __init__(
        self,
        s3_client: S3Client,
        bucket: str,
        prefix: str,
        fetch_id: int,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.signals = _FetchSignals()
        self._s3 = s3_client
        self._bucket = bucket
        self._prefix = prefix
        self._fetch_id = fetch_id

    def run(self) -> None:
        try:
            items, _ = self._s3.list_objects(self._bucket, self._prefix)
            self.signals.listing_complete.emit(self._prefix, items, self._fetch_id)
        except Exception as e:
            logger.error("Fetch failed for prefix '%s': %s", self._prefix, e)
            self.signals.error.emit(self._prefix, str(e), self._fetch_id)


class S3PaneWidget(QWidget):
    """Pane for browsing S3 bucket contents."""

    directory_changed = pyqtSignal(str)  # current prefix
    status_message = pyqtSignal(str)  # for status bar updates
    download_requested = pyqtSignal(list)  # list of S3Item
    delete_requested = pyqtSignal(list)  # list of S3Item
    new_folder_requested = pyqtSignal()
    get_info_requested = pyqtSignal(object)  # S3Item
    files_dropped = pyqtSignal(list)  # list of local file paths (str) dropped onto S3 pane
    quick_open_requested = pyqtSignal(object)  # S3Item — double-click file opens it

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._s3_client: S3Client | None = None
        self._bucket: str = ""
        self._current_prefix: str = ""
        self._history_back: list[str] = []
        self._history_forward: list[str] = []
        self._fetch_id: int = 0
        self._fetch_worker: _FetchWorker | None = None
        self._cache = ListingCache()
        self._connected = False
        self._operation_locks: dict[str, str] = {}

        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Mini toolbar
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(4, 2, 4, 2)
        toolbar.setSpacing(2)

        self._back_btn = QToolButton()
        self._back_btn.setText("◀")
        self._back_btn.setToolTip("Back")
        self._back_btn.setAutoRaise(True)
        self._back_btn.clicked.connect(self.go_back)
        self._back_btn.setEnabled(False)
        toolbar.addWidget(self._back_btn)

        self._forward_btn = QToolButton()
        self._forward_btn.setText("▶")
        self._forward_btn.setToolTip("Forward")
        self._forward_btn.setAutoRaise(True)
        self._forward_btn.clicked.connect(self.go_forward)
        self._forward_btn.setEnabled(False)
        toolbar.addWidget(self._forward_btn)

        self._search_btn = QToolButton()
        self._search_btn.setText("🔍")
        self._search_btn.setToolTip("Filter (Ctrl+F)")
        self._search_btn.setAutoRaise(True)
        self._search_btn.setCheckable(True)
        self._search_btn.toggled.connect(self._toggle_filter)
        toolbar.addWidget(self._search_btn)

        self._breadcrumb = BreadcrumbBar(separator="/")
        self._breadcrumb.path_clicked.connect(self._on_breadcrumb_clicked)
        self._breadcrumb.path_edited.connect(self._on_breadcrumb_edited)
        toolbar.addWidget(self._breadcrumb, 1)

        toolbar_widget = QWidget()
        toolbar_widget.setLayout(toolbar)
        layout.addWidget(toolbar_widget)

        # Filter bar (hidden by default)
        self._filter_bar = QLineEdit()
        self._filter_bar.setPlaceholderText("Filter by name...")
        self._filter_bar.setClearButtonEnabled(True)
        self._filter_bar.setVisible(False)
        self._filter_bar.textChanged.connect(self._on_filter_changed)
        layout.addWidget(self._filter_bar)

        # Table view
        self._model = S3ObjectModel()
        self._proxy = QSortFilterProxyModel()
        self._proxy.setSourceModel(self._model)
        self._proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._proxy.setFilterKeyColumn(0)  # Filter on Name column

        self._table = QTableView()
        self._table.setModel(self._proxy)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setShowGrid(False)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setSortingEnabled(True)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self._table.doubleClicked.connect(self._on_double_click)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        self._table.setAcceptDrops(True)
        self._table.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self._table.viewport().setAcceptDrops(True)
        layout.addWidget(self._table, 1)

        # Status/error label (hidden by default)
        self._status_label = QLabel()
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setStyleSheet("color: gray; padding: 20px;")
        self._status_label.setVisible(False)
        layout.addWidget(self._status_label)

        # Placeholder (shown when not connected)
        self._placeholder = QLabel("Connect to S3 to browse files")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet("color: gray;")
        layout.addWidget(self._placeholder)
        self._table.setVisible(False)

        # Footer
        self._footer = QLabel("0 items")
        self._footer.setContentsMargins(8, 2, 8, 2)
        self._footer.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(self._footer)

    # --- Public API ---

    def set_client(self, s3_client: S3Client) -> None:
        """Set the S3 client to use for fetching."""
        self._s3_client = s3_client
        self._connected = True
        self._placeholder.setVisible(False)
        self._table.setVisible(True)

    def set_bucket(self, bucket_name: str) -> None:
        """Switch to a different bucket."""
        self._bucket = bucket_name
        self._cache.invalidate_all()
        self._history_back.clear()
        self._history_forward.clear()
        self.navigate_to("")

    def navigate_to(self, prefix: str, record_history: bool = True) -> None:
        """Navigate to an S3 prefix."""
        if not self._s3_client or not self._bucket:
            return

        if record_history and self._current_prefix != prefix:
            self._history_back.append(self._current_prefix)
            if len(self._history_back) > NAV_HISTORY_MAX:
                self._history_back = self._history_back[-NAV_HISTORY_MAX:]
            self._history_forward.clear()

        self._current_prefix = prefix
        self._update_breadcrumb()
        self._update_nav_buttons()

        # Check cache
        cached = self._cache.get(prefix)
        if cached is not None:
            self._model.set_items(cached.items)
            self._update_footer()
            self._status_label.setVisible(False)
            self.directory_changed.emit(prefix)

            # Launch background revalidation if stale
            if self._cache.is_stale(prefix):
                counter = self._cache.get_mutation_counter(prefix)
                self._launch_fetch(prefix, revalidate=True, counter=counter)
            return

        # Cache miss — show loading state and fetch
        self._model.clear()
        self._show_loading()
        self._launch_fetch(prefix)
        self.directory_changed.emit(prefix)

    def go_back(self) -> None:
        if not self._history_back:
            return
        self._history_forward.append(self._current_prefix)
        prefix = self._history_back.pop()
        self.navigate_to(prefix, record_history=False)

    def go_forward(self) -> None:
        if not self._history_forward:
            return
        self._history_back.append(self._current_prefix)
        prefix = self._history_forward.pop()
        self.navigate_to(prefix, record_history=False)

    def refresh(self) -> None:
        """Force refresh current prefix."""
        self._cache.invalidate(self._current_prefix)
        self.navigate_to(self._current_prefix, record_history=False)

    def current_prefix(self) -> str:
        return self._current_prefix

    def selected_items(self) -> list[S3Item]:
        """Return S3Items for selected rows."""
        items = []
        for idx in self._table.selectionModel().selectedRows():
            source_idx = self._proxy.mapToSource(idx)
            item = self._model.get_item(source_idx.row())
            if item:
                items.append(item)
        return items

    # --- Optimistic mutation interface ---

    def notify_upload_complete(self, key: str, size: int) -> None:
        """Optimistic: insert uploaded object into current listing."""
        prefix = self._current_prefix
        name = key[len(prefix):] if prefix else key
        if "/" in name:
            return  # Not in current directory level
        item = S3Item(name=name, key=key, is_prefix=False, size=size)
        self._model.insert_item(item)
        self._cache.apply_mutation(prefix, lambda items: items.append(item))
        self._update_footer()

    def notify_delete_complete(self, keys: list[str]) -> None:
        """Optimistic: remove deleted objects from current listing."""
        key_set = set(keys)
        self._model.remove_items(key_set)
        self._cache.apply_mutation(
            self._current_prefix,
            lambda items: self._remove_from_list(items, key_set),
        )
        self._update_footer()

    def notify_rename_complete(self, old_key: str, new_key: str, new_name: str) -> None:
        """Optimistic: update a renamed item."""
        self._model.update_item(old_key, key=new_key, name=new_name)
        self._cache.apply_mutation(
            self._current_prefix,
            lambda items: self._rename_in_list(items, old_key, new_key, new_name),
        )

    def notify_new_folder(self, key: str, name: str) -> None:
        """Optimistic: insert a new prefix (folder)."""
        item = S3Item(name=name, key=key, is_prefix=True)
        self._model.insert_item(item)
        self._cache.apply_mutation(self._current_prefix, lambda items: items.append(item))
        self._update_footer()

    def notify_copy_complete(self, key: str, size: int) -> None:
        """Optimistic: insert a copied object."""
        self.notify_upload_complete(key, size)

    # --- Filter ---

    def _toggle_filter(self, checked: bool) -> None:
        self._filter_bar.setVisible(checked)
        if checked:
            self._filter_bar.setFocus()
        else:
            self._filter_bar.clear()

    def _on_filter_changed(self, text: str) -> None:
        self._proxy.setFilterFixedString(text)
        self._update_footer()

    # --- Internal ---

    def _launch_fetch(
        self, prefix: str, revalidate: bool = False, counter: int = 0
    ) -> None:
        """Launch a background fetch for the given prefix."""
        self._fetch_id += 1
        fetch_id = self._fetch_id

        worker = _FetchWorker(self._s3_client, self._bucket, prefix, fetch_id, self)

        if revalidate:
            worker.signals.listing_complete.connect(
                lambda p, items, fid: self._on_revalidation_complete(p, items, fid, counter)
            )
        else:
            worker.signals.listing_complete.connect(self._on_listing_complete)

        worker.signals.error.connect(self._on_fetch_error)
        worker.finished.connect(worker.deleteLater)
        self._fetch_worker = worker
        worker.start()

    def _on_listing_complete(self, prefix: str, items: list[S3Item], fetch_id: int) -> None:
        """Handle completion of a fresh fetch."""
        if fetch_id != self._fetch_id:
            # Stale fetch — cache the result but don't update UI
            self._cache.put(prefix, items)
            return

        self._cache.put(prefix, items)
        self._model.set_items(items)
        self._status_label.setVisible(False)
        self._update_footer()
        self.status_message.emit(f"Loaded {len(items)} items")

    def _on_revalidation_complete(
        self, prefix: str, items: list[S3Item], fetch_id: int, counter: int
    ) -> None:
        """Handle completion of a background revalidation."""
        self._cache.safe_revalidate(prefix, items, counter)

        if fetch_id != self._fetch_id:
            return  # User navigated away

        if prefix == self._current_prefix:
            cached = self._cache.get(prefix)
            if cached:
                self._model.diff_apply(cached.items)
                self._update_footer()

    def _on_fetch_error(self, prefix: str, error_msg: str, fetch_id: int) -> None:
        """Handle fetch failure."""
        if fetch_id != self._fetch_id:
            return
        self._status_label.setText(f"Error loading: {error_msg}\nClick Refresh to retry.")
        self._status_label.setVisible(True)
        self.status_message.emit(f"Error: {error_msg}")

    def _show_loading(self) -> None:
        self._status_label.setText("Loading...")
        self._status_label.setVisible(True)

    def _update_breadcrumb(self) -> None:
        display_path = f"{self._bucket}/{self._current_prefix}" if self._bucket else ""
        self._breadcrumb.set_path(display_path)

    def _on_breadcrumb_clicked(self, path: str) -> None:
        # Strip bucket name from the front to get the prefix
        if self._bucket and path.startswith(self._bucket):
            prefix = path[len(self._bucket):]
            if prefix.startswith("/"):
                prefix = prefix[1:]
            if prefix and not prefix.endswith("/"):
                prefix += "/"
            # Root is empty string, not "/"
            if prefix == "/":
                prefix = ""
            self.navigate_to(prefix)

    def _on_breadcrumb_edited(self, path: str) -> None:
        # User typed a path — interpret as prefix
        if self._bucket and path.startswith(self._bucket):
            prefix = path[len(self._bucket):]
            if prefix.startswith("/"):
                prefix = prefix[1:]
        else:
            prefix = path
        if prefix and not prefix.endswith("/"):
            prefix += "/"
        self.navigate_to(prefix)

    def _on_double_click(self, index: QModelIndex) -> None:
        source_idx = self._proxy.mapToSource(index)
        item = self._model.get_item(source_idx.row())
        if not item:
            return
        if item.is_prefix:
            self.navigate_to(item.key)
        else:
            self.quick_open_requested.emit(item)

    def _update_nav_buttons(self) -> None:
        self._back_btn.setEnabled(len(self._history_back) > 0)
        self._forward_btn.setEnabled(len(self._history_forward) > 0)

    def _update_footer(self) -> None:
        total = self._model.item_count()
        visible = self._proxy.rowCount()
        size_str = _format_size(self._model.total_size())

        if self._filter_bar.isVisible() and self._filter_bar.text():
            self._footer.setText(f"{visible} of {total} items, {size_str}")
        else:
            self._footer.setText(f"{total} items, {size_str}")

    @staticmethod
    def _remove_from_list(items: list[S3Item], keys: set[str]) -> None:
        items[:] = [i for i in items if i.key not in keys]

    def _on_context_menu(self, pos) -> None:
        from PyQt6.QtWidgets import QMenu

        menu = QMenu(self)
        selected = self.selected_items()

        if selected:
            download_action = menu.addAction("Download")
            download_action.triggered.connect(lambda: self.download_requested.emit(selected))

            menu.addSeparator()

            delete_action = menu.addAction("Delete")
            delete_action.triggered.connect(lambda: self.delete_requested.emit(selected))

            if len(selected) == 1:
                menu.addSeparator()
                info_action = menu.addAction("Get Info")
                info_action.triggered.connect(
                    lambda: self.get_info_requested.emit(selected[0])
                )
        else:
            new_folder_action = menu.addAction("New Folder")
            new_folder_action.triggered.connect(self.new_folder_requested.emit)

            refresh_action = menu.addAction("Refresh")
            refresh_action.triggered.connect(self.refresh)

        menu.exec(self._table.viewport().mapToGlobal(pos))

    # --- Operation lock manager ---

    def acquire_lock(self, keys: list[str], description: str) -> bool:
        """Attempt to lock keys for an operation. Returns False if conflict."""
        for key in keys:
            for locked_key, locked_desc in self._operation_locks.items():
                if key.startswith(locked_key) or locked_key.startswith(key):
                    logger.warning(
                        "Lock conflict: '%s' blocked by '%s' (%s)",
                        key, locked_key, locked_desc,
                    )
                    return False
        for key in keys:
            self._operation_locks[key] = description
        return True

    def release_lock(self, keys: list[str]) -> None:
        """Release locks for the given keys."""
        for key in keys:
            self._operation_locks.pop(key, None)

    # --- Drag and drop ---

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            paths = []
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    paths.append(url.toLocalFile())
            if paths:
                self.files_dropped.emit(paths)
                event.acceptProposedAction()
                return
        super().dropEvent(event)

    @staticmethod
    def _rename_in_list(
        items: list[S3Item], old_key: str, new_key: str, new_name: str
    ) -> None:
        for item in items:
            if item.key == old_key:
                item.key = new_key
                item.name = new_name
                break
