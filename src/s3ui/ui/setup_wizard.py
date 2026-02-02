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
    QRadioButton,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from s3ui.core.credentials import (
    CredentialStore,
    Profile,
    TestResult,
    discover_aws_profiles,
    get_aws_profile_region,
)

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
    """Page 2: Choose an AWS CLI profile or enter credentials manually."""

    def __init__(
        self,
        store: CredentialStore,
        aws_profiles: list[str] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._test_result: TestResult | None = None
        self._worker: _TestWorker | None = None
        self._aws_profiles: list[str] = aws_profiles if aws_profiles is not None else []

        self.setTitle("AWS Credentials")
        self.setSubTitle("Use an existing AWS CLI profile or enter credentials manually.")

        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        # --- Option 1: AWS CLI profile ---
        self._aws_radio = QRadioButton("Use AWS CLI profile")
        layout.addWidget(self._aws_radio)

        self._aws_profile_combo = QComboBox()
        self._aws_profile_combo.setMinimumWidth(200)
        layout.addWidget(self._aws_profile_combo)

        self._aws_info_label = QLabel("")
        self._aws_info_label.setStyleSheet("color: gray; font-size: 11px; margin-left: 20px;")
        self._aws_info_label.setWordWrap(True)
        layout.addWidget(self._aws_info_label)

        # Region override (for AWS profiles)
        self._region_label = QLabel("Region (optional override):")
        layout.addWidget(self._region_label)

        self._region_combo = QComboBox()
        self._region_combo.addItem("Auto-detect from profile", "")
        for display_name, region_code in AWS_REGIONS:
            self._region_combo.addItem(f"{display_name} ({region_code})", region_code)
        layout.addWidget(self._region_combo)

        # --- Option 2: Manual credentials ---
        self._manual_radio = QRadioButton("Enter credentials manually")
        layout.addWidget(self._manual_radio)

        # Manual fields container — visibility controlled by radio
        self._manual_widget = _ManualCredentialWidget()
        self._manual_widget.fields_changed.connect(self.completeChanged)
        layout.addWidget(self._manual_widget)

        # Test connection
        layout.addSpacing(8)
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

        # Now connect radio signals and populate — AFTER all widgets are created
        self._aws_radio.toggled.connect(self._on_mode_changed)
        self._manual_radio.toggled.connect(self._on_mode_changed)

        # Populate AWS profiles (uses pre-discovered list or discovers fresh)
        self._populate_profiles()

    def _populate_profiles(self) -> None:
        """Populate the AWS profile combo with already-discovered profiles."""
        # If no profiles were passed in, try discovering now
        if not self._aws_profiles:
            self._aws_profiles = discover_aws_profiles()
            logger.debug("Wizard discovered %d AWS profiles", len(self._aws_profiles))

        self._aws_profile_combo.clear()

        if self._aws_profiles:
            for name in self._aws_profiles:
                region = get_aws_profile_region(name)
                display = f"{name} ({region})" if region else name
                self._aws_profile_combo.addItem(display, name)
            self._aws_info_label.setText(
                f"Found {len(self._aws_profiles)} profile(s) in ~/.aws/config"
            )
            self._aws_radio.setChecked(True)
        else:
            self._aws_profile_combo.addItem("(no profiles found)")
            self._aws_profile_combo.setEnabled(False)
            self._aws_radio.setEnabled(False)
            self._aws_info_label.setText(
                "No AWS CLI profiles found. Run 'aws configure' to create one,\n"
                "or enter credentials manually below."
            )
            self._manual_radio.setChecked(True)

        # Apply initial visibility
        self._on_mode_changed()

    def _on_mode_changed(self) -> None:
        is_aws = self._aws_radio.isChecked()
        has_profiles = bool(self._aws_profiles)
        # AWS section
        self._aws_profile_combo.setVisible(is_aws and has_profiles)
        self._aws_info_label.setVisible(is_aws)
        self._region_label.setVisible(is_aws and has_profiles)
        self._region_combo.setVisible(is_aws and has_profiles)
        # Manual section
        self._manual_widget.setVisible(not is_aws)
        # Reset test state
        self._test_result = None
        self._test_status.setText("")
        self._error_label.setVisible(False)
        self.completeChanged.emit()

    def _on_test_clicked(self) -> None:
        profile = self._build_profile()
        if profile is None:
            self._test_status.setText("Please fill in all required fields.")
            self._test_status.setStyleSheet("color: orange;")
            return

        self._test_btn.setEnabled(False)
        self._test_status.setText("Testing...")
        self._test_status.setStyleSheet("color: gray;")
        self._error_label.setVisible(False)

        self._worker = _TestWorker(self._store, profile)
        self._worker.signals.finished.connect(self._on_test_result)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _build_profile(self) -> Profile | None:
        """Build a Profile from the current UI state."""
        if self._aws_radio.isChecked():
            if not self._aws_profiles:
                return None
            name = self._aws_profile_combo.currentData()
            region = self._region_combo.currentData() or ""
            return Profile(name=name, region=region, is_aws_profile=True)
        else:
            name = self._manual_widget.profile_name()
            access_key = self._manual_widget.access_key()
            secret_key = self._manual_widget.secret_key()
            region = self._manual_widget.region()
            if not name or not access_key or not secret_key:
                return None
            return Profile(
                name=name,
                access_key_id=access_key,
                secret_access_key=secret_key,
                region=region,
            )

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
            detail = result.error_detail if result.error_detail else result.error_message
            self._error_label.setText(f"{result.error_message}\n\nDetail: {detail}")
            self._error_label.setVisible(True)

        self.completeChanged.emit()

    def isComplete(self) -> bool:
        """Page is complete when credentials are filled in (test is optional)."""
        if self._aws_radio.isChecked():
            return bool(self._aws_profiles)
        # Manual mode: need name, access key, and secret key
        return bool(
            self._manual_widget.profile_name()
            and self._manual_widget.access_key()
            and self._manual_widget.secret_key()
        )

    def get_profile(self) -> Profile:
        return self._build_profile() or Profile(name="default")

    def get_buckets(self) -> list[str]:
        if self._test_result:
            return self._test_result.buckets
        return []


class _ManualCredentialWidget(QWidget):
    """Sub-widget with manual credential entry fields."""

    fields_changed = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 8, 0, 8)
        layout.setSpacing(4)

        # Profile name
        layout.addWidget(QLabel("Profile Name:"))
        self._name_edit = QLineEdit()
        self._name_edit.setText("default")
        self._name_edit.setPlaceholderText("e.g., default, work, personal")
        self._name_edit.textChanged.connect(self.fields_changed)
        layout.addWidget(self._name_edit)

        # Access Key ID
        layout.addWidget(QLabel("Access Key ID:"))
        self._access_key_edit = QLineEdit()
        self._access_key_edit.setPlaceholderText("AKIA...")
        self._access_key_edit.textChanged.connect(self.fields_changed)
        layout.addWidget(self._access_key_edit)

        # Secret Access Key
        layout.addWidget(QLabel("Secret Access Key:"))
        secret_row = QHBoxLayout()
        self._secret_key_edit = QLineEdit()
        self._secret_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._secret_key_edit.setPlaceholderText("Your secret key")
        self._secret_key_edit.textChanged.connect(self.fields_changed)
        secret_row.addWidget(self._secret_key_edit)
        self._toggle_btn = QPushButton("Show")
        self._toggle_btn.setFixedWidth(50)
        self._toggle_btn.clicked.connect(self._toggle_visibility)
        secret_row.addWidget(self._toggle_btn)
        layout.addLayout(secret_row)

        # Region
        layout.addWidget(QLabel("Region:"))
        self._region_combo = QComboBox()
        for display_name, region_code in AWS_REGIONS:
            self._region_combo.addItem(f"{display_name} ({region_code})", region_code)
        layout.addWidget(self._region_combo)

    def _toggle_visibility(self) -> None:
        if self._secret_key_edit.echoMode() == QLineEdit.EchoMode.Password:
            self._secret_key_edit.setEchoMode(QLineEdit.EchoMode.Normal)
            self._toggle_btn.setText("Hide")
        else:
            self._secret_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self._toggle_btn.setText("Show")

    def profile_name(self) -> str:
        return self._name_edit.text().strip()

    def access_key(self) -> str:
        return self._access_key_edit.text().strip()

    def secret_key(self) -> str:
        return self._secret_key_edit.text().strip()

    def region(self) -> str:
        return self._region_combo.currentData()


class BucketPage(QWizardPage):
    """Page 3: Pick a bucket."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setTitle("Select a Bucket")
        self.setSubTitle("Choose which S3 bucket to open, or type a name.")

        layout = QVBoxLayout(self)

        self._bucket_list = QListWidget()
        self._bucket_list.currentItemChanged.connect(lambda: self.completeChanged.emit())
        layout.addWidget(self._bucket_list, 1)

        # Manual entry for when bucket listing wasn't available
        self._manual_label = QLabel("Or enter a bucket name:")
        layout.addWidget(self._manual_label)
        self._manual_edit = QLineEdit()
        self._manual_edit.setPlaceholderText("my-bucket-name")
        self._manual_edit.textChanged.connect(self.completeChanged)
        layout.addWidget(self._manual_edit)

        # Info label when no buckets are available
        self._info_label = QLabel(
            "No buckets were discovered. You can enter a bucket name manually,\n"
            "or skip this step and select a bucket later from the toolbar."
        )
        self._info_label.setWordWrap(True)
        self._info_label.setStyleSheet("color: gray;")
        self._info_label.setVisible(False)
        layout.addWidget(self._info_label)

    def initializePage(self) -> None:
        wizard = self.wizard()
        cred_page = wizard.page(1)
        buckets = cred_page.get_buckets()
        self._bucket_list.clear()
        self._manual_edit.clear()

        if buckets:
            self._bucket_list.addItems(sorted(buckets))
            self._bucket_list.setCurrentRow(0)
            self._bucket_list.setVisible(True)
            self._manual_label.setVisible(False)
            self._manual_edit.setVisible(False)
            self._info_label.setVisible(False)
        else:
            self._bucket_list.setVisible(False)
            self._manual_label.setVisible(True)
            self._manual_edit.setVisible(True)
            self._info_label.setVisible(True)

    def isComplete(self) -> bool:
        # Complete if a bucket is selected from list OR entered manually OR left empty (skip)
        return True

    def selected_bucket(self) -> str:
        item = self._bucket_list.currentItem()
        if item:
            return item.text()
        return self._manual_edit.text().strip()


class SetupWizard(QWizard):
    """First-run wizard for setting up AWS credentials."""

    def __init__(
        self,
        store: CredentialStore | None = None,
        parent=None,
        aws_profiles: list[str] | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store or CredentialStore()
        self.setWindowTitle("S3UI Setup")
        self.setMinimumSize(600, 550)
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)

        self._welcome = WelcomePage()
        self._cred_page = CredentialPage(self._store, aws_profiles=aws_profiles)
        self._bucket_page = BucketPage()

        self.addPage(self._welcome)
        self.addPage(self._cred_page)
        self.addPage(self._bucket_page)

    def get_profile(self) -> Profile:
        """Return the profile configured by the user."""
        return self._cred_page.get_profile()

    def get_bucket(self) -> str:
        """Return the bucket selected (or entered) by the user."""
        return self._bucket_page.selected_bucket()
