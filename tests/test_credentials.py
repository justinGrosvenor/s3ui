"""Tests for credential store and error translation."""

from unittest.mock import MagicMock, patch

import pytest

from s3ui.core.credentials import CredentialStore, Profile, discover_aws_profiles
from s3ui.core.errors import ERROR_MESSAGES, translate_error


@pytest.fixture
def store(mock_keyring) -> CredentialStore:
    return CredentialStore()


@pytest.fixture
def sample_profile() -> Profile:
    return Profile(
        name="test",
        access_key_id="AKIAIOSFODNN7EXAMPLE",
        secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        region="us-east-1",
    )


class TestCredentialStore:
    def test_save_and_read_profile(self, store: CredentialStore, sample_profile: Profile):
        store.save_profile(sample_profile)
        loaded = store.get_profile("test")
        assert loaded is not None
        assert loaded.name == "test"
        assert loaded.access_key_id == sample_profile.access_key_id
        assert loaded.secret_access_key == sample_profile.secret_access_key
        assert loaded.region == sample_profile.region

    def test_list_profiles(self, store: CredentialStore, sample_profile: Profile):
        assert store.list_profiles() == []
        store.save_profile(sample_profile)
        assert store.list_profiles() == ["test"]

        second = Profile("second", "AKIA2", "secret2", "eu-west-1")
        store.save_profile(second)
        assert store.list_profiles() == ["test", "second"]

    def test_delete_profile(self, store: CredentialStore, sample_profile: Profile):
        store.save_profile(sample_profile)
        assert store.list_profiles() == ["test"]

        store.delete_profile("test")
        assert store.list_profiles() == []
        assert store.get_profile("test") is None

    def test_get_nonexistent_profile(self, store: CredentialStore):
        assert store.get_profile("no_such_profile") is None

    def test_save_profile_idempotent(self, store: CredentialStore, sample_profile: Profile):
        """Saving the same profile twice doesn't duplicate in the index."""
        store.save_profile(sample_profile)
        store.save_profile(sample_profile)
        assert store.list_profiles() == ["test"]

    def test_test_connection_success(self, store: CredentialStore, sample_profile: Profile):
        mock_client = MagicMock()
        mock_client.list_buckets.return_value = {
            "Buckets": [{"Name": "bucket-a"}, {"Name": "bucket-b"}]
        }
        with patch("boto3.client", return_value=mock_client):
            result = store.test_connection(sample_profile)

        assert result.success is True
        assert result.buckets == ["bucket-a", "bucket-b"]
        assert result.error_message == ""

    def test_test_connection_invalid_credentials(
        self, store: CredentialStore, sample_profile: Profile
    ):
        mock_client = MagicMock()
        error_response = {"Error": {"Code": "InvalidAccessKeyId", "Message": "bad key"}}
        from botocore.exceptions import ClientError

        mock_client.list_buckets.side_effect = ClientError(error_response, "ListBuckets")
        with patch("boto3.client", return_value=mock_client):
            result = store.test_connection(sample_profile)

        assert result.success is False
        assert "Invalid access key" in result.error_message
        assert result.error_detail != ""

    def test_test_connection_network_error(
        self, store: CredentialStore, sample_profile: Profile
    ):
        from botocore.exceptions import EndpointConnectionError

        with patch("boto3.client") as mock_boto:
            mock_boto.return_value.list_buckets.side_effect = EndpointConnectionError(
                endpoint_url="https://s3.amazonaws.com"
            )
            result = store.test_connection(sample_profile)

        assert result.success is False
        assert "connect" in result.error_message.lower()

    def test_save_aws_profile(self, store: CredentialStore):
        """AWS CLI profiles are saved with is_aws_profile flag."""
        profile = Profile(name="work", region="eu-west-1", is_aws_profile=True)
        store.save_profile(profile)
        loaded = store.get_profile("work")
        assert loaded is not None
        assert loaded.is_aws_profile is True
        assert loaded.access_key_id == ""
        assert loaded.region == "eu-west-1"

    def test_test_connection_aws_profile(self, store: CredentialStore):
        """Testing an AWS CLI profile uses boto3.Session."""
        profile = Profile(name="default", region="us-east-1", is_aws_profile=True)
        mock_session = MagicMock()
        mock_client = MagicMock()
        mock_client.list_buckets.return_value = {"Buckets": [{"Name": "b1"}]}
        mock_session.client.return_value = mock_client

        with patch("boto3.Session", return_value=mock_session) as mock_sess_cls:
            result = store.test_connection(profile)

        mock_sess_cls.assert_called_once_with(profile_name="default")
        assert result.success is True
        assert result.buckets == ["b1"]


class TestDiscoverAwsProfiles:
    def test_returns_list(self):
        profiles = discover_aws_profiles()
        assert isinstance(profiles, list)

    def test_returns_sorted_list(self):
        profiles = discover_aws_profiles()
        assert profiles == sorted(profiles)


class TestErrorTranslation:
    def test_known_error_codes(self):
        """All entries in ERROR_MESSAGES produce a non-empty user message."""
        for code, (msg, _suggestion) in ERROR_MESSAGES.items():
            assert len(msg) > 0, f"Empty message for {code}"

    def test_translate_client_error(self):
        from botocore.exceptions import ClientError

        exc = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "denied"}},
            "GetObject",
        )
        user_msg, detail = translate_error(exc)
        assert "Access denied" in user_msg
        assert "denied" in detail

    def test_translate_unknown_error_code(self):
        from botocore.exceptions import ClientError

        exc = ClientError(
            {"Error": {"Code": "SomeNewError", "Message": "something new"}},
            "PutObject",
        )
        user_msg, detail = translate_error(exc)
        assert "something new" in user_msg

    def test_translate_generic_exception(self):
        exc = RuntimeError("something broke")
        user_msg, detail = translate_error(exc)
        assert "unexpected" in user_msg.lower()
        assert "something broke" in detail

    def test_translate_all_known_codes(self):
        """Every code in ERROR_MESSAGES translates correctly."""
        from botocore.exceptions import ClientError

        for code in ERROR_MESSAGES:
            exc = ClientError(
                {"Error": {"Code": code, "Message": f"test {code}"}},
                "TestOp",
            )
            user_msg, detail = translate_error(exc)
            expected_msg = ERROR_MESSAGES[code][0]
            assert expected_msg in user_msg, f"Translation failed for {code}"
