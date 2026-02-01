"""Clickable breadcrumb path bar with edit mode."""

import sys
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QToolButton,
    QWidget,
)


class BreadcrumbBar(QWidget):
    """Breadcrumb navigation bar.

    Shows path segments as clickable buttons. Clicking whitespace to the right
    enters edit mode with a QLineEdit for typing a path directly.
    """

    path_clicked = pyqtSignal(str)  # emitted when a segment is clicked
    path_edited = pyqtSignal(str)  # emitted when path is typed and Enter pressed

    def __init__(self, separator: str = "/", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._separator = separator
        self._current_path = ""
        self._editing = False

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(2, 0, 2, 0)
        self._layout.setSpacing(0)

        # Edit line (hidden by default)
        self._edit = QLineEdit(self)
        self._edit.setVisible(False)
        self._edit.returnPressed.connect(self._on_edit_accepted)
        self._edit.installEventFilter(self)

        # Clickable area to enter edit mode
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.IBeamCursor)

    def set_path(self, path: str) -> None:
        """Set the displayed path, rebuilding segment buttons."""
        self._current_path = path
        if not self._editing:
            self._rebuild_segments()

    def current_path(self) -> str:
        return self._current_path

    def _rebuild_segments(self) -> None:
        # Clear existing widgets (except the edit line)
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget and widget is not self._edit:
                widget.deleteLater()

        if not self._current_path:
            return

        # Parse path into segments
        if self._separator == "/":
            parts = Path(self._current_path).parts
        else:
            parts = self._current_path.split(self._separator)
            parts = [p for p in parts if p]

        # Build segment buttons
        for i, part in enumerate(parts):
            if i > 0:
                sep = QLabel(self._separator)
                sep.setStyleSheet("color: gray; padding: 0 2px;")
                self._layout.addWidget(sep)

            btn = QToolButton()
            btn.setText(part)
            btn.setAutoRaise(True)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)

            # Build the path up to this segment
            if self._separator == "/":
                segment_path = str(Path(*parts[: i + 1]))
                # On Unix, ensure root starts with /
                if sys.platform != "win32" and not segment_path.startswith("/"):
                    segment_path = "/" + segment_path
            else:
                segment_path = self._separator.join(parts[: i + 1])
                if not segment_path.endswith(self._separator):
                    segment_path += self._separator

            btn.clicked.connect(lambda checked, p=segment_path: self.path_clicked.emit(p))
            self._layout.addWidget(btn)

        # Spacer to fill remaining width (clicking it enters edit mode)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._layout.addWidget(spacer)

    def _enter_edit_mode(self) -> None:
        self._editing = True
        # Hide all segment widgets
        for i in range(self._layout.count()):
            w = self._layout.itemAt(i).widget()
            if w:
                w.setVisible(False)

        self._edit.setText(self._current_path)
        self._edit.setVisible(True)
        self._edit.selectAll()
        self._edit.setFocus()
        self._layout.addWidget(self._edit)

    def _exit_edit_mode(self) -> None:
        self._editing = False
        self._edit.setVisible(False)
        self._rebuild_segments()

    def _on_edit_accepted(self) -> None:
        text = self._edit.text().strip()
        self._exit_edit_mode()
        if text:
            self.path_edited.emit(text)

    def mousePressEvent(self, event) -> None:
        # Click on empty area enters edit mode
        if not self._editing:
            child = self.childAt(event.pos())
            if child is None or child.objectName() == "":
                self._enter_edit_mode()
                return
        super().mousePressEvent(event)

    def eventFilter(self, obj, event) -> bool:
        if obj is self._edit:
            from PyQt6.QtCore import QEvent

            if event.type() == QEvent.Type.KeyPress:
                if event.key() == Qt.Key.Key_Escape:
                    self._exit_edit_mode()
                    return True
            elif event.type() == QEvent.Type.FocusOut:
                self._exit_edit_mode()
                return True
        return super().eventFilter(obj, event)
