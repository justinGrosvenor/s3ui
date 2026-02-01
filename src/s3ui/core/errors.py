"""Maps boto3/botocore exceptions to plain-language error messages."""

import logging

logger = logging.getLogger("s3ui.errors")

# Maps AWS error codes to (user-facing message, suggestion)
ERROR_MESSAGES: dict[str, tuple[str, str]] = {
    "InvalidAccessKeyId": (
        "Invalid access key.",
        "Check that your Access Key ID is correct in Settings.",
    ),
    "SignatureDoesNotMatch": (
        "Invalid secret key.",
        "Check that your Secret Access Key is correct in Settings.",
    ),
    "AccessDenied": (
        "Access denied.",
        "Your AWS credentials don't have permission for this action. Check your IAM policy.",
    ),
    "NoSuchBucket": (
        "Bucket not found.",
        "The bucket may have been deleted or you may have a typo in the name.",
    ),
    "NoSuchKey": (
        "File not found.",
        "The file may have been deleted or moved by someone else.",
    ),
    "BucketAlreadyOwnedByYou": (
        "You already own this bucket.",
        "",
    ),
    "BucketNotEmpty": (
        "Bucket is not empty.",
        "Delete all files in the bucket before deleting it.",
    ),
    "EntityTooLarge": (
        "File is too large for a single upload.",
        "This shouldn't happen — the app should use multipart upload. Please report this bug.",
    ),
    "SlowDown": (
        "S3 is asking us to slow down.",
        "Too many requests. The app will retry automatically.",
    ),
    "ServiceUnavailable": (
        "S3 is temporarily unavailable.",
        "Try again in a few moments.",
    ),
    "InternalError": (
        "S3 encountered an internal error.",
        "Try again in a few moments.",
    ),
    "RequestTimeout": (
        "The request timed out.",
        "Check your network connection and try again.",
    ),
    "ExpiredToken": (
        "Your credentials have expired.",
        "Update your credentials in Settings.",
    ),
    "InvalidBucketName": (
        "Invalid bucket name.",
        "Bucket names must be 3-63 characters, lowercase letters, numbers, and hyphens.",
    ),
    "KeyTooLongError": (
        "File name is too long.",
        "S3 keys can be at most 1024 bytes.",
    ),
}


def translate_error(exc: Exception) -> tuple[str, str]:
    """Translate a boto3 exception to (user_message, raw_detail).

    Returns a tuple of (plain-language message for the user, raw error string for
    the "Show Details" expander).
    """
    raw_detail = str(exc)

    # botocore ClientError
    if hasattr(exc, "response"):
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ERROR_MESSAGES:
            user_msg, suggestion = ERROR_MESSAGES[code]
            if suggestion:
                user_msg = f"{user_msg} {suggestion}"
            return user_msg, raw_detail
        # Unknown AWS error code
        message = exc.response.get("Error", {}).get("Message", "")
        return f"AWS error: {message}" if message else "An AWS error occurred.", raw_detail

    # Connection errors
    err_type = type(exc).__name__
    if "ConnectionError" in err_type or "EndpointConnectionError" in err_type:
        return (
            "Could not connect to S3. Check your network connection and try again.",
            raw_detail,
        )

    if "ReadTimeoutError" in err_type or "ConnectTimeoutError" in err_type:
        return "The connection timed out. Check your network connection.", raw_detail

    # Fallback
    return "An unexpected error occurred.", raw_detail
