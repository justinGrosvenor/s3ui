"""Tests for S3Client wrapper using moto mock."""

import boto3
import pytest
from moto import mock_aws

from s3ui.core.credentials import Profile
from s3ui.core.s3_client import S3Client, S3ClientError


@pytest.fixture
def profile() -> Profile:
    return Profile(
        name="test",
        access_key_id="testing",
        secret_access_key="testing",
        region="us-east-1",
    )


@pytest.fixture
def s3_env(profile):
    """Set up a mocked S3 environment with a test bucket."""
    with mock_aws():
        # Create a bucket directly via boto3
        raw = boto3.client("s3", region_name="us-east-1")
        raw.create_bucket(Bucket="test-bucket")
        client = S3Client(profile)
        yield client, raw


class TestListBuckets:
    def test_returns_created_buckets(self, profile):
        with mock_aws():
            raw = boto3.client("s3", region_name="us-east-1")
            raw.create_bucket(Bucket="bucket-a")
            raw.create_bucket(Bucket="bucket-b")
            client = S3Client(profile)
            buckets = client.list_buckets()
            assert "bucket-a" in buckets
            assert "bucket-b" in buckets


class TestListObjects:
    def test_returns_objects_and_prefixes(self, s3_env):
        client, raw = s3_env
        raw.put_object(Bucket="test-bucket", Key="file1.txt", Body=b"hello")
        raw.put_object(Bucket="test-bucket", Key="folder/file2.txt", Body=b"world")

        objects, prefixes = client.list_objects("test-bucket")
        names = {o.name for o in objects}
        assert "file1.txt" in names
        assert "folder" in names  # prefix
        assert "folder/" in prefixes

    def test_pagination(self, s3_env):
        client, raw = s3_env
        # Create >1000 objects to trigger pagination
        for i in range(1050):
            raw.put_object(
                Bucket="test-bucket", Key=f"obj{i:04d}.txt", Body=b"x"
            )
        objects, _ = client.list_objects("test-bucket")
        assert len(objects) == 1050

    def test_prefix_filtering(self, s3_env):
        client, raw = s3_env
        raw.put_object(Bucket="test-bucket", Key="a/1.txt", Body=b"1")
        raw.put_object(Bucket="test-bucket", Key="a/2.txt", Body=b"2")
        raw.put_object(Bucket="test-bucket", Key="b/3.txt", Body=b"3")

        objects, _ = client.list_objects("test-bucket", prefix="a/")
        assert len(objects) == 2
        names = {o.name for o in objects}
        assert "1.txt" in names
        assert "2.txt" in names


class TestPutGetObject:
    def test_round_trip(self, s3_env):
        client, _raw = s3_env
        client.put_object("test-bucket", "test.txt", b"hello world")
        body = client.get_object("test-bucket", "test.txt")
        assert body.read() == b"hello world"


class TestDeleteObject:
    def test_delete_removes_object(self, s3_env):
        client, raw = s3_env
        raw.put_object(Bucket="test-bucket", Key="to-delete.txt", Body=b"bye")
        client.delete_object("test-bucket", "to-delete.txt")
        objects, _ = client.list_objects("test-bucket")
        keys = {o.key for o in objects}
        assert "to-delete.txt" not in keys

    def test_batch_delete(self, s3_env):
        client, raw = s3_env
        for i in range(5):
            raw.put_object(Bucket="test-bucket", Key=f"del{i}.txt", Body=b"x")
        failed = client.delete_objects(
            "test-bucket", [f"del{i}.txt" for i in range(5)]
        )
        assert failed == []
        objects, _ = client.list_objects("test-bucket")
        assert len(objects) == 0


class TestCopyObject:
    def test_copy_creates_at_destination(self, s3_env):
        client, raw = s3_env
        raw.put_object(Bucket="test-bucket", Key="original.txt", Body=b"data")
        client.copy_object("test-bucket", "original.txt", "test-bucket", "copy.txt")
        body = client.get_object("test-bucket", "copy.txt")
        assert body.read() == b"data"


class TestMultipartUpload:
    def test_full_multipart_flow(self, s3_env):
        client, _raw = s3_env
        upload_id = client.create_multipart_upload("test-bucket", "big.bin")
        assert upload_id

        part_data = [b"a" * 5 * 1024 * 1024, b"b" * 5 * 1024 * 1024, b"c" * 1024]
        parts = []
        for i, data in enumerate(part_data, 1):
            etag = client.upload_part(
                "test-bucket", "big.bin", upload_id, i, data
            )
            parts.append({"ETag": etag, "PartNumber": i})

        client.complete_multipart_upload("test-bucket", "big.bin", upload_id, parts)

        # Verify the object exists and has correct size
        item = client.head_object("test-bucket", "big.bin")
        expected_size = sum(len(d) for d in part_data)
        assert item.size == expected_size

    def test_abort_multipart(self, s3_env):
        client, _raw = s3_env
        upload_id = client.create_multipart_upload("test-bucket", "abort.bin")
        client.upload_part("test-bucket", "abort.bin", upload_id, 1, b"x" * 1024)
        client.abort_multipart_upload("test-bucket", "abort.bin", upload_id)

        # Object should not exist
        with pytest.raises(S3ClientError):
            client.head_object("test-bucket", "abort.bin")

    def test_list_parts(self, s3_env):
        client, _raw = s3_env
        upload_id = client.create_multipart_upload("test-bucket", "parts.bin")
        client.upload_part("test-bucket", "parts.bin", upload_id, 1, b"a" * 5 * 1024 * 1024)
        client.upload_part("test-bucket", "parts.bin", upload_id, 2, b"b" * 5 * 1024 * 1024)

        parts = client.list_parts("test-bucket", "parts.bin", upload_id)
        assert len(parts) == 2
        assert parts[0]["PartNumber"] == 1
        assert parts[1]["PartNumber"] == 2


class TestHeadObject:
    def test_returns_metadata(self, s3_env):
        client, raw = s3_env
        raw.put_object(Bucket="test-bucket", Key="info.txt", Body=b"metadata test")
        item = client.head_object("test-bucket", "info.txt")
        assert item.key == "info.txt"
        assert item.name == "info.txt"
        assert item.size == 13
        assert item.last_modified is not None


class TestErrorTranslation:
    def test_nonexistent_bucket(self, profile):
        with mock_aws():
            client = S3Client(profile)
            with pytest.raises(S3ClientError) as exc_info:
                client.list_objects("no-such-bucket-xyz")
            assert exc_info.value.detail != ""
