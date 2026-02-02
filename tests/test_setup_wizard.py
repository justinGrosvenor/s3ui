"""Tests for setup wizard and settings dialog."""

from s3ui.core.credentials import CredentialStore, discover_aws_profiles
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

    def test_has_aws_and_manual_modes(self, qtbot):
        store = CredentialStore()
        page = CredentialPage(store)
        qtbot.addWidget(page)
        assert page._aws_radio is not None
        assert page._manual_radio is not None

    def test_manual_mode_shows_fields(self, qtbot):
        store = CredentialStore()
        page = CredentialPage(store)
        qtbot.addWidget(page)
        # Switch to manual mode
        page._manual_radio.setChecked(True)
        assert page._manual_widget.isVisibleTo(page)

    def test_get_profile_manual_mode(self, qtbot):
        store = CredentialStore()
        page = CredentialPage(store)
        qtbot.addWidget(page)
        # Switch to manual mode
        page._manual_radio.setChecked(True)
        page._manual_widget._name_edit.setText("test")
        page._manual_widget._access_key_edit.setText("AKIATEST")
        page._manual_widget._secret_key_edit.setText("secret123")
        profile = page.get_profile()
        assert profile.name == "test"
        assert profile.access_key_id == "AKIATEST"
        assert profile.is_aws_profile is False

    def test_get_profile_aws_mode_no_profiles(self, qtbot):
        store = CredentialStore()
        page = CredentialPage(store)
        qtbot.addWidget(page)
        # With no AWS profiles, get_profile in aws mode returns default
        page._aws_radio.setChecked(True)
        profile = page.get_profile()
        # When no profiles, returns fallback
        assert profile is not None


class TestBucketPage:
    def test_creates(self, qtbot):
        page = BucketPage()
        qtbot.addWidget(page)
        assert page.title() == "Select a Bucket"

    def test_complete_even_when_empty(self, qtbot):
        """Bucket page is always complete — user can skip bucket selection."""
        page = BucketPage()
        qtbot.addWidget(page)
        assert page.isComplete() is True

    def test_manual_entry_visible_when_no_buckets(self, qtbot):
        """Manual bucket entry shows when no buckets are available."""
        page = BucketPage()
        qtbot.addWidget(page)
        assert page._manual_edit is not None

    def test_selected_bucket_from_manual_entry(self, qtbot):
        page = BucketPage()
        qtbot.addWidget(page)
        page._manual_edit.setText("my-bucket")
        assert page.selected_bucket() == "my-bucket"


class TestCredentialPageCompletion:
    def test_manual_mode_complete_with_fields(self, qtbot):
        store = CredentialStore()
        page = CredentialPage(store)
        qtbot.addWidget(page)
        page._manual_radio.setChecked(True)
        assert page.isComplete() is False  # Fields empty

        page._manual_widget._name_edit.setText("test")
        page._manual_widget._access_key_edit.setText("AKIATEST")
        page._manual_widget._secret_key_edit.setText("secret123")
        assert page.isComplete() is True

    def test_manual_mode_not_complete_missing_key(self, qtbot):
        store = CredentialStore()
        page = CredentialPage(store)
        qtbot.addWidget(page)
        page._manual_radio.setChecked(True)
        page._manual_widget._name_edit.setText("test")
        page._manual_widget._access_key_edit.setText("AKIATEST")
        # secret key missing
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
        assert wizard.page(0) is not None
        assert wizard.page(1) is not None
        assert wizard.page(2) is not None


class TestAwsRegions:
    def test_regions_populated(self):
        assert len(AWS_REGIONS) > 10
        for display, code in AWS_REGIONS:
            assert "-" in code
            assert len(display) > 0


class TestDiscoverAwsProfiles:
    def test_returns_list(self):
        """discover_aws_profiles always returns a list (even if empty)."""
        profiles = discover_aws_profiles()
        assert isinstance(profiles, list)

    def test_profiles_are_strings(self):
        profiles = discover_aws_profiles()
        for name in profiles:
            assert isinstance(name, str)


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
