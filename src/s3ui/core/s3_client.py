"""Instrumented S3 client wrapping boto3."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import boto3

from s3ui.core.errors import translate_error
from s3ui.models.s3_objects import S3Item

if TYPE_CHECKING:
    from s3ui.core.cost import CostTracker
    from s3ui.core.credentials import Profile

logger = logging.getLogger("s3ui.s3_client")


class S3ClientError(Exception):
    """Wraps an S3 error with user-facing message and raw detail."""

    def __init__(self, user_message: str, detail: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.detail = detail


class S3Client:
    """Wraps boto3 S3 client with cost tracking, error translation, and logging."""

    def __init__(
        self,
        profile: Profile,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        endpoint = profile.endpoint_url or None
        if profile.is_aws_profile:
            session = boto3.Session(profile_name=profile.name)
            self._client = session.client(
                "s3",
                region_name=profile.region or None,
                endpoint_url=endpoint,
            )
        else:
            self._client = boto3.client(
                "s3",
                aws_access_key_id=profile.access_key_id,
                aws_secret_access_key=profile.secret_access_key,
                region_name=profile.region,
                endpoint_url=endpoint,
            )
        self._cost = cost_tracker
        self._profile_name = profile.name
        logger.info(
            "S3Client created for profile '%s' region '%s' endpoint='%s' (aws_profile=%s)",
            profile.name,
            profile.region,
            profile.endpoint_url,
            profile.is_aws_profile,
        )

    def set_cost_tracker(self, tracker: CostTracker | None) -> None:
        """Attach or replace the cost tracker (e.g. after bucket selection)."""
        self._cost = tracker

    def _record(self, request_type: str, count: int = 1) -> None:
        if self._cost:
            self._cost.record_request(request_type, count)

    def _record_upload_bytes(self, size: int) -> None:
        if self._cost:
            self._cost.record_upload_bytes(size)

    def _record_download_bytes(self, size: int) -> None:
        if self._cost:
            self._cost.record_download_bytes(size)

    def _handle_error(self, exc: Exception, operation: str) -> None:
        user_msg, detail = translate_error(exc)
        logger.error("S3 operation '%s' failed: %s", operation, detail)
        raise S3ClientError(user_msg, detail) from exc

    # --- Bucket operations ---

    def list_buckets(self) -> list[str]:
        """Return a list of bucket names."""
        try:
            logger.debug("list_buckets")
            self._record("list")
            response = self._client.list_buckets()
            return [b["Name"] for b in response.get("Buckets", [])]
        except Exception as e:
            self._handle_error(e, "list_buckets")

    # --- Listing ---

    def list_objects(
        self, bucket: str, prefix: str = "", delimiter: str = "/"
    ) -> tuple[list[S3Item], list[str]]:
        """List objects and common prefixes under a prefix.

        Returns (objects, common_prefixes). Handles pagination internally.
        """
        try:
            logger.debug("list_objects bucket=%s prefix='%s'", bucket, prefix)
            objects: list[S3Item] = []
            prefixes: list[str] = []

            paginator = self._client.get_paginator("list_objects_v2")
            pages = paginator.paginate(Bucket=bucket, Prefix=prefix, Delimiter=delimiter)

            page_count = 0
            for page in pages:
                page_count += 1
                self._record("list")

                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    # Skip the prefix itself (S3 may return the prefix as an object)
                    if key == prefix:
                        continue
                    name = key[len(prefix) :] if prefix else key
                    objects.append(
                        S3Item(
                            name=name,
                            key=key,
                            is_prefix=False,
                            size=obj.get("Size"),
                            last_modified=obj.get("LastModified"),
                            storage_class=obj.get("StorageClass"),
                            etag=obj.get("ETag"),
                        )
                    )

                for cp in page.get("CommonPrefixes", []):
                    p = cp["Prefix"]
                    name = p[len(prefix) :].rstrip("/") if prefix else p.rstrip("/")
                    prefixes.append(p)
                    objects.append(S3Item(name=name, key=p, is_prefix=True))

            logger.debug(
                "list_objects returned %d items, %d prefixes across %d pages",
                len(objects),
                len(prefixes),
                page_count,
            )
            return objects, prefixes
        except Exception as e:
            self._handle_error(e, "list_objects")

    def head_object(self, bucket: str, key: str) -> S3Item:
        """Get full metadata for a single object."""
        try:
            logger.debug("head_object bucket=%s key='%s'", bucket, key)
            self._record("head")
            resp = self._client.head_object(Bucket=bucket, Key=key)
            name = key.rsplit("/", 1)[-1] if "/" in key else key
            return S3Item(
                name=name,
                key=key,
                is_prefix=False,
                size=resp.get("ContentLength"),
                last_modified=resp.get("LastModified"),
                storage_class=resp.get("StorageClass"),
                etag=resp.get("ETag"),
            )
        except Exception as e:
            self._handle_error(e, "head_object")

    # --- Single object operations ---

    def put_object(self, bucket: str, key: str, body: bytes) -> None:
        """Upload a small object in a single request."""
        try:
            logger.debug("put_object bucket=%s key='%s' size=%d", bucket, key, len(body))
            self._record("put")
            self._client.put_object(Bucket=bucket, Key=key, Body=body)
            self._record_upload_bytes(len(body))
        except Exception as e:
            self._handle_error(e, "put_object")

    def get_object(self, bucket: str, key: str, range_header: str | None = None):
        """Download an object (or a byte range). Returns the streaming body."""
        try:
            logger.debug("get_object bucket=%s key='%s' range=%s", bucket, key, range_header)
            self._record("get")
            kwargs = {"Bucket": bucket, "Key": key}
            if range_header:
                kwargs["Range"] = range_header
            return self._client.get_object(**kwargs)["Body"]
        except Exception as e:
            self._handle_error(e, "get_object")

    def delete_object(self, bucket: str, key: str) -> None:
        """Delete a single object."""
        try:
            logger.debug("delete_object bucket=%s key='%s'", bucket, key)
            self._record("delete")
            self._client.delete_object(Bucket=bucket, Key=key)
        except Exception as e:
            self._handle_error(e, "delete_object")

    def delete_objects(self, bucket: str, keys: list[str]) -> list[str]:
        """Batch delete up to 1000 objects. Returns list of keys that failed."""
        try:
            logger.debug("delete_objects bucket=%s count=%d", bucket, len(keys))
            self._record("delete", len(keys))
            response = self._client.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": k} for k in keys], "Quiet": True},
            )
            errors = response.get("Errors", [])
            if errors:
                failed = [e["Key"] for e in errors]
                logger.warning("delete_objects partial failure: %d failed", len(failed))
                return failed
            return []
        except Exception as e:
            self._handle_error(e, "delete_objects")

    def copy_object(self, src_bucket: str, src_key: str, dst_bucket: str, dst_key: str) -> None:
        """Server-side copy with metadata preservation."""
        try:
            logger.debug("copy_object %s/%s -> %s/%s", src_bucket, src_key, dst_bucket, dst_key)
            self._record("copy")
            self._client.copy_object(
                Bucket=dst_bucket,
                Key=dst_key,
                CopySource={"Bucket": src_bucket, "Key": src_key},
                MetadataDirective="COPY",
            )
        except Exception as e:
            self._handle_error(e, "copy_object")

    # --- Multipart upload ---

    def create_multipart_upload(self, bucket: str, key: str) -> str:
        """Initiate a multipart upload. Returns the upload_id."""
        try:
            logger.debug("create_multipart_upload bucket=%s key='%s'", bucket, key)
            self._record("put")
            response = self._client.create_multipart_upload(Bucket=bucket, Key=key)
            upload_id = response["UploadId"]
            logger.debug("Multipart upload initiated: upload_id=%s", upload_id)
            return upload_id
        except Exception as e:
            self._handle_error(e, "create_multipart_upload")

    def upload_part(
        self, bucket: str, key: str, upload_id: str, part_number: int, body: bytes
    ) -> str:
        """Upload a single part. Returns the ETag."""
        try:
            logger.debug(
                "upload_part bucket=%s key='%s' part=%d size=%d",
                bucket,
                key,
                part_number,
                len(body),
            )
            self._record("put")
            response = self._client.upload_part(
                Bucket=bucket,
                Key=key,
                UploadId=upload_id,
                PartNumber=part_number,
                Body=body,
            )
            self._record_upload_bytes(len(body))
            return response["ETag"]
        except Exception as e:
            self._handle_error(e, "upload_part")

    def complete_multipart_upload(
        self, bucket: str, key: str, upload_id: str, parts: list[dict]
    ) -> None:
        """Complete a multipart upload. parts is a list of {'ETag': ..., 'PartNumber': ...}."""
        try:
            logger.debug(
                "complete_multipart_upload bucket=%s key='%s' parts=%d",
                bucket,
                key,
                len(parts),
            )
            self._record("put")
            self._client.complete_multipart_upload(
                Bucket=bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
            )
        except Exception as e:
            self._handle_error(e, "complete_multipart_upload")

    def abort_multipart_upload(self, bucket: str, key: str, upload_id: str) -> None:
        """Abort a multipart upload and clean up parts."""
        try:
            logger.debug(
                "abort_multipart_upload bucket=%s key='%s' upload_id=%s",
                bucket,
                key,
                upload_id,
            )
            self._client.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
        except Exception as e:
            self._handle_error(e, "abort_multipart_upload")

    def list_parts(self, bucket: str, key: str, upload_id: str) -> list[dict]:
        """List uploaded parts for a multipart upload."""
        try:
            logger.debug("list_parts bucket=%s key='%s' upload_id=%s", bucket, key, upload_id)
            self._record("list")
            parts = []
            kwargs = {"Bucket": bucket, "Key": key, "UploadId": upload_id}
            while True:
                response = self._client.list_parts(**kwargs)
                for p in response.get("Parts", []):
                    parts.append(
                        {
                            "PartNumber": p["PartNumber"],
                            "ETag": p["ETag"],
                            "Size": p["Size"],
                        }
                    )
                if response.get("IsTruncated"):
                    kwargs["PartNumberMarker"] = response["NextPartNumberMarker"]
                else:
                    break
            return parts
        except Exception as e:
            self._handle_error(e, "list_parts")

    def list_multipart_uploads(self, bucket: str) -> list[dict]:
        """List in-progress multipart uploads for orphan cleanup."""
        try:
            logger.debug("list_multipart_uploads bucket=%s", bucket)
            self._record("list")
            uploads = []
            kwargs = {"Bucket": bucket}
            while True:
                response = self._client.list_multipart_uploads(**kwargs)
                for u in response.get("Uploads", []):
                    uploads.append(
                        {
                            "Key": u["Key"],
                            "UploadId": u["UploadId"],
                            "Initiated": u["Initiated"],
                        }
                    )
                if response.get("IsTruncated"):
                    kwargs["KeyMarker"] = response["NextKeyMarker"]
                    kwargs["UploadIdMarker"] = response["NextUploadIdMarker"]
                else:
                    break
            return uploads
        except Exception as e:
            self._handle_error(e, "list_multipart_uploads")
