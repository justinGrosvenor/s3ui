"""First-run setup wizard for configuring AWS credentials."""

from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from s3ui.core.credentials import CredentialStore, Profile, TestResult

logger = logging.getLogger("s3ui.setup_wizard")

# AWS regions (common subset)
AWS_REGIONS = [
    ("US East (N. Virginia)", "us-east-1"),
    ("US East (Ohio)", "us-east-2"),
    ("US West (N. California)", "us-west-1"),
    ("US West (Oregon)", "us-west-2"),
    ("Europe (Ireland)", "eu-west-1"),
    ("Europe (London)", "eu-west-2"),
    ("Europe (Frankfurt)", "eu-central-1"),
    ("Europe (Paris)", "eu-west-3"),
    ("Europe (Stockholm)", "eu-north-1"),
    ("Asia Pacific (Tokyo)", "ap-northeast-1"),
    ("Asia Pacific (Seoul)", "ap-northeast-2"),
    ("Asia Pacific (Singapore)", "ap-southeast-1"),
    ("Asia Pacific (Sydney)", "ap-southeast-2"),
    ("Asia Pacific (Mumbai)", "ap-south-1"),
    ("Canada (Central)", "ca-central-1"),
    ("South America (Sao Paulo)", "sa-east-1"),
]


class _TestWorkerSignals(QObject):
    finished = pyqtSignal(TestResult)


class _TestWorker(QThread):
    """Background thread for testing AWS credentials."""

    def __init__(self, store: CredentialStore, profile: Profile) -> None:
        super().__init__()
        self.signals = _TestWorkerSignals()
        self._store = store
        self._profile = profile

    def run(self) -> None:
        result = self._store.test_connection(self._profile)
        self.signals.finished.emit(result)


class WelcomePage(QWizardPage):
    """Page 1: Welcome text."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setTitle("Welcome to S3UI")

        layout = QVBoxLayout(self)
        label = QLabel(
            "S3UI is a native file manager for Amazon S3.\n\n"
            "You can browse, upload, download, and manage files\n"
            "in your S3 buckets with a familiar dual-pane interface.\n\n"
            "Let's get started by connecting your AWS account."
        )
        label.setWordWrap(True)
        layout.addWidget(label)
        layout.addStretch()


class CredentialPage(QWizardPage):
    """Page 2: Enter and test AWS credentials."""

    def __init__(self, store: CredentialStore, parent=None) -> None:
        super().__init__(parent)
        self._store = store
        self._test_result: TestResult | None = None
        self._worker: _TestWorker | None = None

        self.setTitle("AWS Credentials")
        self.setSubTitle("Enter your AWS access keys to connect.")

        layout = QVBoxLayout(self)

        # Profile name
        layout.addWidget(QLabel("Profile Name:"))
        self._name_edit = QLineEdit()
        self._name_edit.setText("default")
        self._name_edit.setPlaceholderText("e.g., default, work, personal")
        layout.addWidget(self._name_edit)

        # Access Key ID
        layout.addWidget(QLabel("Access Key ID:"))
        self._access_key_edit = QLineEdit()
        self._access_key_edit.setPlaceholderText("AKIA...")
        layout.addWidget(self._access_key_edit)

        # Secret Access Key
        layout.addWidget(QLabel("Secret Access Key:"))
        secret_row = QHBoxLayout()
        self._secret_key_edit = QLineEdit()
        self._secret_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._secret_key_edit.setPlaceholderText("Your secret key")
        secret_row.addWidget(self._secret_key_edit)
        self._toggle_visibility_btn = QPushButton("Show")
        self._toggle_visibility_btn.setFixedWidth(50)
        self._toggle_visibility_btn.clicked.connect(self._toggle_secret_visibility)
        secret_row.addWidget(self._toggle_visibility_btn)
        layout.addLayout(secret_row)

        # Region
        layout.addWidget(QLabel("Region:"))
        self._region_combo = QComboBox()
        for display_name, region_code in AWS_REGIONS:
            self._region_combo.addItem(f"{display_name} ({region_code})", region_code)
        layout.addWidget(self._region_combo)

        # Test connection
        test_row = QHBoxLayout()
        self._test_btn = QPushButton("Test Connection")
        self._test_btn.clicked.connect(self._on_test_clicked)
        test_row.addWidget(self._test_btn)
        self._test_status = QLabel("")
        test_row.addWidget(self._test_status, 1)
        layout.addLayout(test_row)

        # Error detail
        self._error_label = QLabel("")
        self._error_label.setWordWrap(True)
        self._error_label.setStyleSheet("color: red;")
        self._error_label.setVisible(False)
        layout.addWidget(self._error_label)

        layout.addStretch()

    def _toggle_secret_visibility(self) -> None:
        if self._secret_key_edit.echoMode() == QLineEdit.EchoMode.Password:
            self._secret_key_edit.setEchoMode(QLineEdit.EchoMode.Normal)
            self._toggle_visibility_btn.setText("Hide")
        else:
            self._secret_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self._toggle_visibility_btn.setText("Show")

    def _on_test_clicked(self) -> None:
        name = self._name_edit.text().strip()
        access_key = self._access_key_edit.text().strip()
        secret_key = self._secret_key_edit.text().strip()
        region = self._region_combo.currentData()

        if not name or not access_key or not secret_key:
            self._test_status.setText("Please fill in all fields.")
            self._test_status.setStyleSheet("color: orange;")
            return

        profile = Profile(
            name=name,
            access_key_id=access_key,
            secret_access_key=secret_key,
            region=region,
        )

        self._test_btn.setEnabled(False)
        self._test_status.setText("Testing...")
        self._test_status.setStyleSheet("color: gray;")
        self._error_label.setVisible(False)

        self._worker = _TestWorker(self._store, profile)
        self._worker.signals.finished.connect(self._on_test_result)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _on_test_result(self, result: TestResult) -> None:
        self._test_btn.setEnabled(True)
        self._test_result = result

        if result.success:
            self._test_status.setText(f"Connected! Found {len(result.buckets)} bucket(s).")
            self._test_status.setStyleSheet("color: green;")
            self._error_label.setVisible(False)
        else:
            self._test_status.setText("Connection failed.")
            self._test_status.setStyleSheet("color: red;")
            self._error_label.setText(result.error_message)
            self._error_label.setVisible(True)

        self.completeChanged.emit()

    def isComplete(self) -> bool:
        return self._test_result is not None and self._test_result.success

    def get_profile(self) -> Profile:
        return Profile(
            name=self._name_edit.text().strip(),
            access_key_id=self._access_key_edit.text().strip(),
            secret_access_key=self._secret_key_edit.text().strip(),
            region=self._region_combo.currentData(),
        )

    def get_buckets(self) -> list[str]:
        if self._test_result:
            return self._test_result.buckets
        return []


class BucketPage(QWizardPage):
    """Page 3: Pick a bucket."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setTitle("Select a Bucket")
        self.setSubTitle("Choose which S3 bucket to open.")

        layout = QVBoxLayout(self)
        self._bucket_list = QListWidget()
        self._bucket_list.currentItemChanged.connect(lambda: self.completeChanged.emit())
        layout.addWidget(self._bucket_list)

    def initializePage(self) -> None:
        wizard = self.wizard()
        cred_page = wizard.page(1)
        buckets = cred_page.get_buckets()
        self._bucket_list.clear()
        self._bucket_list.addItems(sorted(buckets))
        if self._bucket_list.count() > 0:
            self._bucket_list.setCurrentRow(0)

    def isComplete(self) -> bool:
        return self._bucket_list.currentItem() is not None

    def selected_bucket(self) -> str:
        item = self._bucket_list.currentItem()
        return item.text() if item else ""


class SetupWizard(QWizard):
    """First-run wizard for setting up AWS credentials."""

    setup_complete = pyqtSignal(str, str)  # profile_name, bucket_name

    def __init__(self, store: CredentialStore | None = None, parent=None) -> None:
        super().__init__(parent)
        self._store = store or CredentialStore()
        self.setWindowTitle("S3UI Setup")
        self.setMinimumSize(500, 400)
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)

        self._welcome = WelcomePage()
        self._cred_page = CredentialPage(self._store)
        self._bucket_page = BucketPage()

        self.addPage(self._welcome)
        self.addPage(self._cred_page)
        self.addPage(self._bucket_page)

        self.finished.connect(self._on_finished)

    def _on_finished(self, result: int) -> None:
        if result == QWizard.DialogCode.Accepted.value:
            profile = self._cred_page.get_profile()
            bucket = self._bucket_page.selected_bucket()
            self._store.save_profile(profile)
            logger.info("Setup complete: profile='%s', bucket='%s'", profile.name, bucket)
            self.setup_complete.emit(profile.name, bucket)
