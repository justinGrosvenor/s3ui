"""Delete confirmation dialog."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QVBoxLayout,
)

from s3ui.models.s3_objects import _format_size


class DeleteConfirmDialog(QDialog):
    """Confirm deletion of S3 objects."""

    def __init__(
        self,
        keys: list[str],
        total_size: int = 0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        count = len(keys)
        self.setWindowTitle(f"Delete {count} file{'s' if count != 1 else ''}?")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)

        # Warning
        layout.addWidget(
            QLabel(f"Are you sure you want to delete {count} item{'s' if count != 1 else ''}?")
        )

        # File list (first 10)
        file_list = QListWidget()
        for key in keys[:10]:
            file_list.addItem(key)
        if count > 10:
            file_list.addItem(f"...and {count - 10} more")
        file_list.setMaximumHeight(200)
        layout.addWidget(file_list)

        # Total size
        if total_size > 0:
            layout.addWidget(QLabel(f"Total size: {_format_size(total_size)}"))

        # Warning
        layout.addWidget(QLabel("This action cannot be undone."))

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Delete")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
