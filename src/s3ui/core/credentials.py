"""Credential storage via OS keyring and profile management."""

import json
import logging
from dataclasses import dataclass

import keyring

from s3ui.constants import KEYRING_SERVICE
from s3ui.core.errors import translate_error

logger = logging.getLogger("s3ui.credentials")

PROFILES_INDEX_KEY = "profiles"


@dataclass
class Profile:
    name: str
    access_key_id: str
    secret_access_key: str
    region: str


@dataclass
class TestResult:
    success: bool
    buckets: list[str]
    error_message: str = ""
    error_detail: str = ""


class CredentialStore:
    """Manages AWS credential profiles in the OS keyring."""

    def list_profiles(self) -> list[str]:
        """Return names of all saved profiles."""
        raw = keyring.get_password(KEYRING_SERVICE, PROFILES_INDEX_KEY)
        if not raw:
            return []
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []

    def get_profile(self, name: str) -> Profile | None:
        """Load a profile by name from the keyring."""
        raw = keyring.get_password(KEYRING_SERVICE, f"profile:{name}")
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return Profile(
                name=name,
                access_key_id=data["access_key_id"],
                secret_access_key=data["secret_access_key"],
                region=data["region"],
            )
        except (json.JSONDecodeError, KeyError, TypeError):
            logger.error("Corrupt profile data for '%s'", name)
            return None

    def save_profile(self, profile: Profile) -> None:
        """Save a profile to the keyring and update the index."""
        data = json.dumps({
            "access_key_id": profile.access_key_id,
            "secret_access_key": profile.secret_access_key,
            "region": profile.region,
        })
        keyring.set_password(KEYRING_SERVICE, f"profile:{profile.name}", data)

        # Update profile index
        profiles = self.list_profiles()
        if profile.name not in profiles:
            profiles.append(profile.name)
            keyring.set_password(
                KEYRING_SERVICE, PROFILES_INDEX_KEY, json.dumps(profiles)
            )
        logger.info("Saved profile '%s'", profile.name)

    def delete_profile(self, name: str) -> None:
        """Remove a profile from the keyring and index."""
        keyring.delete_password(KEYRING_SERVICE, f"profile:{name}")

        profiles = self.list_profiles()
        if name in profiles:
            profiles.remove(name)
            keyring.set_password(
                KEYRING_SERVICE, PROFILES_INDEX_KEY, json.dumps(profiles)
            )
        logger.info("Deleted profile '%s'", name)

    def test_connection(self, profile: Profile) -> TestResult:
        """Test AWS credentials by calling list_buckets.

        Returns a TestResult with success status and bucket list (on success)
        or error messages (on failure).
        """
        try:
            import boto3

            client = boto3.client(
                "s3",
                aws_access_key_id=profile.access_key_id,
                aws_secret_access_key=profile.secret_access_key,
                region_name=profile.region,
            )
            response = client.list_buckets()
            bucket_names = [b["Name"] for b in response.get("Buckets", [])]
            logger.info(
                "Connection test succeeded for profile '%s': %d buckets",
                profile.name,
                len(bucket_names),
            )
            return TestResult(success=True, buckets=bucket_names)
        except Exception as e:
            user_msg, detail = translate_error(e)
            logger.warning(
                "Connection test failed for profile '%s': %s", profile.name, detail
            )
            return TestResult(
                success=False,
                buckets=[],
                error_message=user_msg,
                error_detail=detail,
            )
