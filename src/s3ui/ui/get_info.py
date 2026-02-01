"""Get Info dialog showing S3 object metadata."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
)

from s3ui.models.s3_objects import S3Item, _format_date, _format_size


class GetInfoDialog(QDialog):
    """Shows detailed metadata for an S3 object or prefix."""

    def __init__(self, item: S3Item, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Get Info")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)

        # File name (large)
        name_label = QLabel(item.name)
        name_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(name_label)

        # Details
        form = QFormLayout()

        form.addRow("S3 Key:", QLabel(item.key))

        if not item.is_prefix:
            form.addRow("Size:", QLabel(_format_size(item.size)))
            form.addRow("Last Modified:", QLabel(_format_date(item.last_modified)))
            if item.storage_class:
                form.addRow("Storage Class:", QLabel(item.storage_class))
            if item.etag:
                form.addRow("ETag:", QLabel(item.etag))
        else:
            form.addRow("Type:", QLabel("Folder (prefix)"))

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
