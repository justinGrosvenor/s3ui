"""Tests for cost tracker."""

from datetime import date
from pathlib import Path

import pytest

from s3ui.core.cost import CostTracker
from s3ui.db.database import Database


@pytest.fixture
def db(tmp_path: Path) -> Database:
    d = Database(tmp_path / "cost_test.db")
    yield d
    d.close()


@pytest.fixture
def bucket_id(db: Database) -> int:
    cursor = db.execute(
        "INSERT INTO buckets (name, region, profile) VALUES (?, ?, ?)",
        ("cost-bucket", "us-east-1", "default"),
    )
    return cursor.lastrowid


@pytest.fixture
def tracker(db: Database, bucket_id: int) -> CostTracker:
    return CostTracker(db, bucket_id)


class TestRecordRequest:
    def test_increments_correct_column(self, tracker: CostTracker, db: Database, bucket_id: int):
        tracker.record_request("put", 3)
        row = db.fetchone(
            "SELECT put_requests FROM daily_usage WHERE bucket_id = ?", (bucket_id,)
        )
        assert row["put_requests"] == 3

    def test_accumulates_on_same_day(self, tracker: CostTracker, db: Database, bucket_id: int):
        tracker.record_request("get", 5)
        tracker.record_request("get", 10)
        row = db.fetchone(
            "SELECT get_requests FROM daily_usage WHERE bucket_id = ?", (bucket_id,)
        )
        assert row["get_requests"] == 15

    def test_record_on_new_day(self, tracker: CostTracker, db: Database, bucket_id: int):
        """Different days create different rows (tested by inserting directly)."""
        db.execute(
            "INSERT INTO daily_usage (bucket_id, usage_date, put_requests) VALUES (?, ?, ?)",
            (bucket_id, "2025-01-01", 10),
        )
        db.execute(
            "INSERT INTO daily_usage (bucket_id, usage_date, put_requests) VALUES (?, ?, ?)",
            (bucket_id, "2025-01-02", 20),
        )
        rows = db.fetchall(
            "SELECT * FROM daily_usage WHERE bucket_id = ? ORDER BY usage_date",
            (bucket_id,),
        )
        assert len(rows) >= 2

    def test_record_upload_bytes(self, tracker: CostTracker, db: Database, bucket_id: int):
        tracker.record_upload_bytes(1000)
        tracker.record_upload_bytes(2000)
        row = db.fetchone(
            "SELECT bytes_uploaded FROM daily_usage WHERE bucket_id = ?", (bucket_id,)
        )
        assert row["bytes_uploaded"] == 3000

    def test_record_download_bytes(self, tracker: CostTracker, db: Database, bucket_id: int):
        tracker.record_download_bytes(5000)
        row = db.fetchone(
            "SELECT bytes_downloaded FROM daily_usage WHERE bucket_id = ?", (bucket_id,)
        )
        assert row["bytes_downloaded"] == 5000


class TestDailyCost:
    def test_with_known_inputs(self, tracker: CostTracker, db: Database, bucket_id: int):
        target = date.today().isoformat()

        # Insert a snapshot: 100 GB standard storage
        db.execute(
            "INSERT INTO bucket_snapshots "
            "(bucket_id, snapshot_date, total_objects, total_bytes, standard_bytes) "
            "VALUES (?, ?, ?, ?, ?)",
            (bucket_id, target, 100, 100 * 1024**3, 100 * 1024**3),
        )

        # Insert usage: 1000 PUT requests, 10 GB downloaded
        db.execute(
            "INSERT INTO daily_usage "
            "(bucket_id, usage_date, put_requests, bytes_downloaded) VALUES (?, ?, ?, ?)",
            (bucket_id, target, 1000, 10 * 1024**3),
        )

        cost = tracker.get_daily_cost(target)
        # Storage: 100 GB * $0.023/GB/month / 30 ≈ $0.0767
        assert 0.07 < cost.storage < 0.08
        # Requests: 1000 * $0.000005 = $0.005
        assert abs(cost.requests - 0.005) < 0.001
        # Transfer: 10 GB * $0.09 = $0.90
        assert abs(cost.transfer - 0.90) < 0.01
        assert cost.total == cost.storage + cost.requests + cost.transfer

    def test_zero_usage(self, tracker: CostTracker):
        cost = tracker.get_daily_cost("2099-01-01")
        assert cost.storage == 0.0
        assert cost.requests == 0.0
        assert cost.transfer == 0.0
        assert cost.total == 0.0

    def test_transfer_tier_crossing(self, tracker: CostTracker, db: Database, bucket_id: int):
        """Transfer cost tiers: first 100 GB at one rate, next at another."""
        target = date.today().isoformat()
        # 150 GB downloaded
        db.execute(
            "INSERT INTO daily_usage (bucket_id, usage_date, bytes_downloaded) VALUES (?, ?, ?)",
            (bucket_id, target, 150 * 1024**3),
        )
        cost = tracker.get_daily_cost(target)
        # First 100 GB * $0.09 = $9.00
        # Next 50 GB * $0.085 = $4.25
        expected = 100 * 0.09 + 50 * 0.085
        assert abs(cost.transfer - expected) < 0.01


class TestMonthlyEstimate:
    def test_prorates_correctly(self, tracker: CostTracker, db: Database, bucket_id: int):
        today = date.today().isoformat()
        # Snapshot: 1 TB standard
        db.execute(
            "INSERT INTO bucket_snapshots "
            "(bucket_id, snapshot_date, total_bytes, standard_bytes) VALUES (?, ?, ?, ?)",
            (bucket_id, today, 1024**4, 1024**4),
        )
        estimate = tracker.get_monthly_estimate()
        # 1 TB * $0.023/GB = $23.55/month approximately
        assert estimate > 20


class TestRateTable:
    def test_read_rate(self, tracker: CostTracker):
        rate = tracker.get_rate("storage_standard_gb_month")
        assert rate == 0.023

    def test_write_rate(self, tracker: CostTracker):
        tracker.set_rate("storage_standard_gb_month", 0.025)
        assert tracker.get_rate("storage_standard_gb_month") == 0.025

    def test_unknown_rate(self, tracker: CostTracker):
        assert tracker.get_rate("nonexistent_rate") == 0.0
