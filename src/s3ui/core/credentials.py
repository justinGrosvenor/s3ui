"""Credential storage via OS keyring, AWS config discovery, and profile management."""

import json
import logging
from dataclasses import dataclass, field

import keyring

from s3ui.constants import KEYRING_SERVICE
from s3ui.core.errors import translate_error

logger = logging.getLogger("s3ui.credentials")

PROFILES_INDEX_KEY = "profiles"


@dataclass
class Profile:
    name: str
    access_key_id: str = ""
    secret_access_key: str = ""
    region: str = ""
    is_aws_profile: bool = False  # True = use boto3 Session(profile_name=name)


@dataclass
class TestResult:
    success: bool
    buckets: list[str] = field(default_factory=list)
    error_message: str = ""
    error_detail: str = ""


def discover_aws_profiles() -> list[str]:
    """Discover profile names from ~/.aws/config and ~/.aws/credentials.

    Returns a list of available AWS CLI profile names (e.g., ["default", "work"]).
    """
    try:
        import botocore.session

        session = botocore.session.Session()
        profiles = list(session.available_profiles)
        logger.debug("Discovered %d AWS profiles: %s", len(profiles), profiles)
        return sorted(profiles)
    except Exception:
        logger.debug("Could not discover AWS profiles", exc_info=True)
        return []


def get_aws_profile_region(profile_name: str) -> str:
    """Read the region configured for an AWS CLI profile, or empty string."""
    try:
        import botocore.session

        session = botocore.session.Session(profile=profile_name)
        return session.get_config_variable("region") or ""
    except Exception:
        return ""


class CredentialStore:
    """Manages AWS credential profiles — both AWS CLI profiles and custom keyring profiles."""

    def list_profiles(self) -> list[str]:
        """Return names of all saved custom profiles (from keyring)."""
        raw = keyring.get_password(KEYRING_SERVICE, PROFILES_INDEX_KEY)
        if not raw:
            return []
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []

    def get_profile(self, name: str) -> Profile | None:
        """Load a custom profile by name from the keyring."""
        raw = keyring.get_password(KEYRING_SERVICE, f"profile:{name}")
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return Profile(
                name=name,
                access_key_id=data.get("access_key_id", ""),
                secret_access_key=data.get("secret_access_key", ""),
                region=data.get("region", ""),
                is_aws_profile=data.get("is_aws_profile", False),
            )
        except (json.JSONDecodeError, KeyError, TypeError):
            logger.error("Corrupt profile data for '%s'", name)
            return None

    def save_profile(self, profile: Profile) -> None:
        """Save a profile to the keyring and update the index."""
        data = json.dumps(
            {
                "access_key_id": profile.access_key_id,
                "secret_access_key": profile.secret_access_key,
                "region": profile.region,
                "is_aws_profile": profile.is_aws_profile,
            }
        )
        keyring.set_password(KEYRING_SERVICE, f"profile:{profile.name}", data)

        # Update profile index
        profiles = self.list_profiles()
        if profile.name not in profiles:
            profiles.append(profile.name)
            keyring.set_password(KEYRING_SERVICE, PROFILES_INDEX_KEY, json.dumps(profiles))
        logger.info("Saved profile '%s' (aws_profile=%s)", profile.name, profile.is_aws_profile)

    def delete_profile(self, name: str) -> None:
        """Remove a profile from the keyring and index."""
        keyring.delete_password(KEYRING_SERVICE, f"profile:{name}")

        profiles = self.list_profiles()
        if name in profiles:
            profiles.remove(name)
            keyring.set_password(KEYRING_SERVICE, PROFILES_INDEX_KEY, json.dumps(profiles))
        logger.info("Deleted profile '%s'", name)

    def test_connection(self, profile: Profile) -> TestResult:
        """Test AWS credentials by calling list_buckets.

        Supports both AWS CLI profiles (is_aws_profile=True) and explicit keys.
        """
        try:
            import boto3

            if profile.is_aws_profile:
                session = boto3.Session(profile_name=profile.name)
                client = session.client(
                    "s3",
                    region_name=profile.region or None,
                )
            else:
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
            logger.warning("Connection test failed for profile '%s': %s", profile.name, detail)
            return TestResult(
                success=False,
                buckets=[],
                error_message=user_msg,
                error_detail=detail,
            )
