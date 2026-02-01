"""Name conflict resolution dialog for downloads."""

from __future__ import annotations

from enum import Enum

from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QRadioButton,
    QVBoxLayout,
)


class ConflictResolution(Enum):
    REPLACE = "replace"
    KEEP_BOTH = "keep_both"
    SKIP = "skip"


class NameConflictDialog(QDialog):
    """Dialog for resolving file name conflicts during download."""

    def __init__(self, filename: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("File Already Exists")
        self.setMinimumWidth(350)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f'"{filename}" already exists in the destination.'))
        layout.addWidget(QLabel("What would you like to do?"))

        self._replace_radio = QRadioButton("Replace existing file")
        self._keep_both_radio = QRadioButton("Keep both (rename new file)")
        self._skip_radio = QRadioButton("Skip this file")
        self._replace_radio.setChecked(True)

        group = QButtonGroup(self)
        group.addButton(self._replace_radio)
        group.addButton(self._keep_both_radio)
        group.addButton(self._skip_radio)

        layout.addWidget(self._replace_radio)
        layout.addWidget(self._keep_both_radio)
        layout.addWidget(self._skip_radio)

        self._apply_all = QCheckBox("Apply to all remaining conflicts")
        layout.addWidget(self._apply_all)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def resolution(self) -> ConflictResolution:
        if self._replace_radio.isChecked():
            return ConflictResolution.REPLACE
        if self._keep_both_radio.isChecked():
            return ConflictResolution.KEEP_BOTH
        return ConflictResolution.SKIP

    def apply_to_all(self) -> bool:
        return self._apply_all.isChecked()
