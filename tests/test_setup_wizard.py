"""Tests for setup wizard and settings dialog."""

from s3ui.core.credentials import CredentialStore
from s3ui.ui.settings_dialog import CredentialsTab, GeneralTab, SettingsDialog, TransfersTab
from s3ui.ui.setup_wizard import AWS_REGIONS, BucketPage, CredentialPage, SetupWizard, WelcomePage


class TestWelcomePage:
    def test_creates(self, qtbot):
        page = WelcomePage()
        qtbot.addWidget(page)
        assert page.title() == "Welcome to S3UI"


class TestCredentialPage:
    def test_creates(self, qtbot):
        store = CredentialStore()
        page = CredentialPage(store)
        qtbot.addWidget(page)
        assert page.title() == "AWS Credentials"

    def test_not_complete_initially(self, qtbot):
        store = CredentialStore()
        page = CredentialPage(store)
        qtbot.addWidget(page)
        assert page.isComplete() is False

    def test_get_profile(self, qtbot):
        store = CredentialStore()
        page = CredentialPage(store)
        qtbot.addWidget(page)
        page._name_edit.setText("test")
        page._access_key_edit.setText("AKIATEST")
        page._secret_key_edit.setText("secret123")
        profile = page.get_profile()
        assert profile.name == "test"
        assert profile.access_key_id == "AKIATEST"


class TestBucketPage:
    def test_creates(self, qtbot):
        page = BucketPage()
        qtbot.addWidget(page)
        assert page.title() == "Select a Bucket"

    def test_not_complete_when_empty(self, qtbot):
        page = BucketPage()
        qtbot.addWidget(page)
        assert page.isComplete() is False


class TestSetupWizard:
    def test_creates(self, qtbot):
        store = CredentialStore()
        wizard = SetupWizard(store)
        qtbot.addWidget(wizard)
        assert wizard.windowTitle() == "S3UI Setup"

    def test_has_three_pages(self, qtbot):
        store = CredentialStore()
        wizard = SetupWizard(store)
        qtbot.addWidget(wizard)
        # QWizard page IDs
        assert wizard.page(0) is not None
        assert wizard.page(1) is not None
        assert wizard.page(2) is not None


class TestAwsRegions:
    def test_regions_populated(self):
        assert len(AWS_REGIONS) > 10
        for display, code in AWS_REGIONS:
            assert "-" in code  # e.g., us-east-1
            assert len(display) > 0


class TestSettingsDialog:
    def test_creates(self, qtbot):
        dialog = SettingsDialog()
        qtbot.addWidget(dialog)
        assert dialog.windowTitle() == "Settings"

    def test_has_tabs(self, qtbot):
        dialog = SettingsDialog()
        qtbot.addWidget(dialog)
        assert dialog._tabs.count() == 3


class TestCredentialsTab:
    def test_creates(self, qtbot):
        store = CredentialStore()
        tab = CredentialsTab(store)
        qtbot.addWidget(tab)
        assert tab._profile_list is not None

    def test_add_button_exists(self, qtbot):
        store = CredentialStore()
        tab = CredentialsTab(store)
        qtbot.addWidget(tab)
        assert tab._add_btn.text() == "Add Profile..."


class TestTransfersTab:
    def test_creates(self, qtbot):
        tab = TransfersTab()
        qtbot.addWidget(tab)
        assert tab._max_concurrent.value() == 4

    def test_default_range(self, qtbot):
        tab = TransfersTab()
        qtbot.addWidget(tab)
        assert tab._max_concurrent.minimum() == 1
        assert tab._max_concurrent.maximum() == 16


class TestGeneralTab:
    def test_creates(self, qtbot):
        tab = GeneralTab()
        qtbot.addWidget(tab)
        assert tab._dir_edit is not None

    def test_show_hidden_default(self, qtbot):
        tab = GeneralTab()
        qtbot.addWidget(tab)
        assert tab._show_hidden.isChecked() is False
