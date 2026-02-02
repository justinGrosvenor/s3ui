"""Local file system pane — left side of the dual-pane browser."""

import logging
from pathlib import Path

from PyQt6.QtCore import QDir, QModelIndex, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QFileSystemModel
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from s3ui.constants import NAV_HISTORY_MAX
from s3ui.ui.breadcrumb_bar import BreadcrumbBar

logger = logging.getLogger("s3ui.local_pane")


class LocalPaneWidget(QWidget):
    """Pane for browsing local files with QFileSystemModel."""

    directory_changed = pyqtSignal(str)  # current directory path
    upload_requested = pyqtSignal(list)  # list of local file paths

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._history_back: list[str] = []
        self._history_forward: list[str] = []
        self._current_path = str(Path.home())
        self._show_hidden = False

        self._setup_ui()
        self._setup_model()
        self.navigate_to(self._current_path, record_history=False)

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

        self._up_btn = QToolButton()
        self._up_btn.setText("▲")
        self._up_btn.setToolTip("Enclosing Folder")
        self._up_btn.setAutoRaise(True)
        self._up_btn.clicked.connect(self.go_up)
        toolbar.addWidget(self._up_btn)

        self._breadcrumb = BreadcrumbBar(separator="/")
        self._breadcrumb.path_clicked.connect(self._on_breadcrumb_clicked)
        self._breadcrumb.path_edited.connect(self._on_breadcrumb_edited)
        toolbar.addWidget(self._breadcrumb, 1)

        toolbar_widget = QWidget()
        toolbar_widget.setLayout(toolbar)
        layout.addWidget(toolbar_widget)

        # Tree view
        self._tree = QTreeView()
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._tree.setUniformRowHeights(True)
        self._tree.setSortingEnabled(True)
        self._tree.setAnimated(False)
        self._tree.setDragEnabled(True)
        self._tree.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self._tree.doubleClicked.connect(self._on_double_click)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self._tree, 1)

        # Footer
        self._footer = QLabel("0 items")
        self._footer.setContentsMargins(8, 2, 8, 2)
        self._footer.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(self._footer)

    def _setup_model(self) -> None:
        self._model = QFileSystemModel()
        self._model.setRootPath("")
        self._model.setFilter(QDir.Filter.AllDirs | QDir.Filter.Files | QDir.Filter.NoDotAndDotDot)
        self._tree.setModel(self._model)

        # Show only Name, Size, Date Modified
        self._tree.setColumnHidden(2, True)  # Hide Type column

        # Sort dirs before files, then alphabetical
        self._tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)

        # Connect directory loaded signal for footer updates
        self._model.directoryLoaded.connect(self._update_footer)

    def navigate_to(self, path: str, record_history: bool = True) -> None:
        """Navigate to a directory path."""
        p = Path(path)
        if not p.is_dir():
            return

        if record_history and self._current_path != path:
            self._history_back.append(self._current_path)
            if len(self._history_back) > NAV_HISTORY_MAX:
                self._history_back = self._history_back[-NAV_HISTORY_MAX:]
            self._history_forward.clear()

        self._current_path = str(p)
        index = self._model.index(self._current_path)
        self._tree.setRootIndex(index)
        self._breadcrumb.set_path(self._current_path)
        self._update_nav_buttons()
        self._update_footer(self._current_path)
        self.directory_changed.emit(self._current_path)
        logger.debug("Navigated to %s", self._current_path)

    def go_back(self) -> None:
        if not self._history_back:
            return
        self._history_forward.append(self._current_path)
        path = self._history_back.pop()
        self.navigate_to(path, record_history=False)

    def go_forward(self) -> None:
        if not self._history_forward:
            return
        self._history_back.append(self._current_path)
        path = self._history_forward.pop()
        self.navigate_to(path, record_history=False)

    def go_up(self) -> None:
        parent = str(Path(self._current_path).parent)
        if parent != self._current_path:
            self.navigate_to(parent)

    def set_show_hidden(self, show: bool) -> None:
        self._show_hidden = show
        filters = QDir.Filter.AllDirs | QDir.Filter.Files | QDir.Filter.NoDotAndDotDot
        if show:
            filters |= QDir.Filter.Hidden
        self._model.setFilter(filters)

    def current_path(self) -> str:
        return self._current_path

    def selected_paths(self) -> list[str]:
        """Return full paths of selected items."""
        paths = []
        for idx in self._tree.selectionModel().selectedRows():
            paths.append(self._model.filePath(idx))
        return paths

    def _on_double_click(self, index: QModelIndex) -> None:
        path = self._model.filePath(index)
        if self._model.isDir(index):
            self.navigate_to(path)
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _on_breadcrumb_clicked(self, path: str) -> None:
        self.navigate_to(path)

    def _on_breadcrumb_edited(self, path: str) -> None:
        if Path(path).is_dir():
            self.navigate_to(path)

    def _update_nav_buttons(self) -> None:
        self._back_btn.setEnabled(len(self._history_back) > 0)
        self._forward_btn.setEnabled(len(self._history_forward) > 0)

    def _update_footer(self, path: str = "") -> None:
        index = self._model.index(self._current_path)
        count = self._model.rowCount(index)
        total_size = 0
        for i in range(count):
            child = self._model.index(i, 0, index)
            if not self._model.isDir(child):
                total_size += self._model.size(child)

        size_str = _format_size(total_size)
        self._footer.setText(f"{count} items, {size_str}")

    def _on_context_menu(self, pos) -> None:
        from PyQt6.QtWidgets import QMenu

        selected = self.selected_paths()
        if not selected:
            return

        menu = QMenu(self)
        upload_action = menu.addAction("Upload to S3")
        upload_action.triggered.connect(lambda: self.upload_requested.emit(selected))
        menu.exec(self._tree.viewport().mapToGlobal(pos))


def _format_size(size_bytes: int) -> str:
    """Format bytes into human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024**3):.1f} GB"
