"""Settings dialog with tabs for credentials, transfers, costs, and general."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from s3ui.core.credentials import CredentialStore, Profile, discover_aws_profiles
from s3ui.db.database import get_bool_pref, get_int_pref, get_pref, set_pref
from s3ui.ui.setup_wizard import AWS_REGIONS

if TYPE_CHECKING:
    from s3ui.db.database import Database

logger = logging.getLogger("s3ui.settings_dialog")


class CredentialsTab(QWidget):
    """Tab for managing AWS credential profiles."""

    profile_changed = pyqtSignal()

    def __init__(self, store: CredentialStore, parent=None) -> None:
        super().__init__(parent)
        self._store = store

        layout = QVBoxLayout(self)

        # Profile list
        layout.addWidget(QLabel("Saved Profiles:"))
        self._profile_list = QListWidget()
        layout.addWidget(self._profile_list)

        # Buttons
        btn_row = QHBoxLayout()
        self._add_btn = QPushButton("Add Profile...")
        self._add_btn.clicked.connect(self._on_add)
        btn_row.addWidget(self._add_btn)

        self._edit_btn = QPushButton("Edit...")
        self._edit_btn.clicked.connect(self._on_edit)
        self._edit_btn.setEnabled(False)
        btn_row.addWidget(self._edit_btn)

        self._delete_btn = QPushButton("Delete")
        self._delete_btn.clicked.connect(self._on_delete)
        self._delete_btn.setEnabled(False)
        btn_row.addWidget(self._delete_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._profile_list.currentItemChanged.connect(self._on_selection_changed)
        self._refresh_list()

    def _refresh_list(self) -> None:
        self._profile_list.clear()
        # Show AWS CLI profiles first
        aws_profiles = discover_aws_profiles()
        for name in aws_profiles:
            self._profile_list.addItem(f"{name} (AWS CLI)")
        # Then custom profiles from keyring
        for name in self._store.list_profiles():
            self._profile_list.addItem(name)

    def _on_selection_changed(self) -> None:
        has_selection = self._profile_list.currentItem() is not None
        self._edit_btn.setEnabled(has_selection)
        self._delete_btn.setEnabled(has_selection)

    def _on_add(self) -> None:
        dialog = _ProfileEditDialog(self._store, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._refresh_list()
            self.profile_changed.emit()

    def _on_edit(self) -> None:
        item = self._profile_list.currentItem()
        if not item:
            return
        profile = self._store.get_profile(item.text())
        if profile:
            dialog = _ProfileEditDialog(self._store, profile=profile, parent=self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self._refresh_list()
                self.profile_changed.emit()

    def _on_delete(self) -> None:
        item = self._profile_list.currentItem()
        if not item:
            return
        name = item.text()
        reply = QMessageBox.question(
            self,
            "Delete Profile",
            f"Remove profile '{name}'?\n\n"
            "Credentials will be removed from the system keychain.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._store.delete_profile(name)
            self._refresh_list()
            self.profile_changed.emit()


class _ProfileEditDialog(QDialog):
    """Dialog for adding or editing a credential profile."""

    def __init__(
        self,
        store: CredentialStore,
        profile: Profile | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._editing = profile is not None

        self.setWindowTitle("Edit Profile" if self._editing else "Add Profile")
        self.setMinimumWidth(400)

        layout = QFormLayout(self)

        self._name_edit = QLineEdit()
        if profile:
            self._name_edit.setText(profile.name)
            self._name_edit.setReadOnly(True)
        layout.addRow("Profile Name:", self._name_edit)

        self._access_key_edit = QLineEdit()
        if profile:
            self._access_key_edit.setText(profile.access_key_id)
        layout.addRow("Access Key ID:", self._access_key_edit)

        self._secret_key_edit = QLineEdit()
        self._secret_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        if profile:
            self._secret_key_edit.setText(profile.secret_access_key)
        layout.addRow("Secret Access Key:", self._secret_key_edit)

        self._region_combo = QComboBox()
        for display_name, region_code in AWS_REGIONS:
            self._region_combo.addItem(f"{display_name} ({region_code})", region_code)
        if profile:
            for i in range(self._region_combo.count()):
                if self._region_combo.itemData(i) == profile.region:
                    self._region_combo.setCurrentIndex(i)
                    break
        layout.addRow("Region:", self._region_combo)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def _on_accept(self) -> None:
        name = self._name_edit.text().strip()
        access_key = self._access_key_edit.text().strip()
        secret_key = self._secret_key_edit.text().strip()
        region = self._region_combo.currentData()

        if not name or not access_key or not secret_key:
            QMessageBox.warning(self, "Missing Fields", "Please fill in all fields.")
            return

        profile = Profile(
            name=name,
            access_key_id=access_key,
            secret_access_key=secret_key,
            region=region,
        )
        self._store.save_profile(profile)
        self.accept()


class TransfersTab(QWidget):
    """Tab for transfer settings."""

    def __init__(self, db: Database | None = None, parent=None) -> None:
        super().__init__(parent)
        self._db = db

        layout = QFormLayout(self)

        self._max_concurrent = QSpinBox()
        self._max_concurrent.setRange(1, 16)
        self._max_concurrent.setValue(4)
        layout.addRow("Max concurrent transfers:", self._max_concurrent)

        self._retention_combo = QComboBox()
        self._retention_combo.addItems([
            "Clear after session",
            "Keep for 24 hours",
            "Keep forever",
        ])
        layout.addRow("Completed transfer retention:", self._retention_combo)

        if db:
            val = get_int_pref(db, "max_concurrent_transfers", 4)
            self._max_concurrent.setValue(val)
            ret = get_pref(db, "transfer_retention", "Clear after session")
            idx = self._retention_combo.findText(ret)
            if idx >= 0:
                self._retention_combo.setCurrentIndex(idx)

    def apply_settings(self) -> None:
        if self._db:
            set_pref(self._db, "max_concurrent_transfers", str(self._max_concurrent.value()))
            set_pref(self._db, "transfer_retention", self._retention_combo.currentText())


class GeneralTab(QWidget):
    """Tab for general settings."""

    def __init__(self, db: Database | None = None, parent=None) -> None:
        super().__init__(parent)
        self._db = db

        layout = QFormLayout(self)

        # Default local directory
        dir_row = QHBoxLayout()
        self._dir_edit = QLineEdit()
        dir_row.addWidget(self._dir_edit)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_directory)
        dir_row.addWidget(browse_btn)
        layout.addRow("Default local directory:", dir_row)

        # Show hidden files
        self._show_hidden = QCheckBox("Show hidden files by default")
        layout.addRow(self._show_hidden)

        if db:
            from pathlib import Path

            self._dir_edit.setText(get_pref(db, "default_local_dir", str(Path.home())))
            self._show_hidden.setChecked(get_bool_pref(db, "show_hidden_files", False))

    def _browse_directory(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Select Default Directory", self._dir_edit.text()
        )
        if path:
            self._dir_edit.setText(path)

    def apply_settings(self) -> None:
        if self._db:
            set_pref(self._db, "default_local_dir", self._dir_edit.text())
            set_pref(self._db, "show_hidden_files", str(self._show_hidden.isChecked()))


class SettingsDialog(QDialog):
    """Application settings dialog."""

    settings_changed = pyqtSignal()

    def __init__(
        self,
        store: CredentialStore | None = None,
        db: Database | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._store = store or CredentialStore()
        self._db = db
        self.setWindowTitle("Settings")
        self.setMinimumSize(500, 400)

        layout = QVBoxLayout(self)

        self._tabs = QTabWidget()

        self._cred_tab = CredentialsTab(self._store)
        self._tabs.addTab(self._cred_tab, "Credentials")

        self._transfers_tab = TransfersTab(db)
        self._tabs.addTab(self._transfers_tab, "Transfers")

        self._general_tab = GeneralTab(db)
        self._tabs.addTab(self._general_tab, "General")

        layout.addWidget(self._tabs)

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        self._transfers_tab.apply_settings()
        self._general_tab.apply_settings()
        self.settings_changed.emit()
        self.accept()
