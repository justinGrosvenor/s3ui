"""Tests for database layer, migrations, and preferences."""

import threading
from pathlib import Path

import pytest

from s3ui.db.database import Database, get_bool_pref, get_int_pref, get_pref, set_pref


@pytest.fixture
def db(tmp_path: Path) -> Database:
    """Create a fresh test database."""
    d = Database(tmp_path / "test.db")
    yield d
    d.close()


class TestDatabase:
    def test_fresh_db_creates_all_tables(self, db: Database):
        tables = db.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        table_names = {row["name"] for row in tables}
        expected = {
            "buckets",
            "bucket_snapshots",
            "daily_usage",
            "cost_rates",
            "transfers",
            "transfer_parts",
            "preferences",
            "schema_version",
        }
        assert expected.issubset(table_names)

    def test_migration_runs_idempotently(self, tmp_path: Path):
        db1 = Database(tmp_path / "idem.db")
        db2 = Database(tmp_path / "idem.db")
        # Both should succeed without error
        tables = db2.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        assert len(tables) >= 8
        db1.close()
        db2.close()

    def test_schema_version_is_updated(self, db: Database):
        row = db.fetchone("SELECT MAX(version) as v FROM schema_version")
        assert row["v"] == 1

    def test_crud_preferences(self, db: Database):
        set_pref(db, "test_key", "test_value")
        assert get_pref(db, "test_key") == "test_value"

        set_pref(db, "test_key", "updated")
        assert get_pref(db, "test_key") == "updated"

    def test_crud_buckets(self, db: Database):
        db.execute(
            "INSERT INTO buckets (name, region, profile) VALUES (?, ?, ?)",
            ("my-bucket", "us-east-1", "default"),
        )
        row = db.fetchone("SELECT * FROM buckets WHERE name = ?", ("my-bucket",))
        assert row["name"] == "my-bucket"
        assert row["region"] == "us-east-1"
        assert row["profile"] == "default"

        db.execute("DELETE FROM buckets WHERE name = ?", ("my-bucket",))
        row = db.fetchone("SELECT * FROM buckets WHERE name = ?", ("my-bucket",))
        assert row is None

    def test_same_bucket_different_profiles(self, db: Database):
        """Same bucket name with different profiles should create two rows."""
        db.execute(
            "INSERT INTO buckets (name, region, profile) VALUES (?, ?, ?)",
            ("shared-bucket", "us-east-1", "profile-a"),
        )
        db.execute(
            "INSERT INTO buckets (name, region, profile) VALUES (?, ?, ?)",
            ("shared-bucket", "us-east-1", "profile-b"),
        )
        rows = db.fetchall(
            "SELECT * FROM buckets WHERE name = ?", ("shared-bucket",)
        )
        assert len(rows) == 2

    def test_same_bucket_same_profile_rejected(self, db: Database):
        """Same bucket name with same profile should be rejected."""
        import sqlite3 as _sqlite3

        db.execute(
            "INSERT INTO buckets (name, region, profile) VALUES (?, ?, ?)",
            ("dup-bucket", "us-east-1", "default"),
        )
        with pytest.raises(_sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO buckets (name, region, profile) VALUES (?, ?, ?)",
                ("dup-bucket", "us-east-1", "default"),
            )

    def test_concurrent_reads(self, tmp_path: Path):
        """Concurrent reads from two threads don't block."""
        db = Database(tmp_path / "concurrent.db")
        set_pref(db, "thread_test", "value")

        results = []

        def read_pref():
            val = get_pref(db, "thread_test")
            results.append(val)

        t1 = threading.Thread(target=read_pref)
        t2 = threading.Thread(target=read_pref)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert results == ["value", "value"]
        db.close()

    def test_cost_rates_seeded(self, db: Database):
        """Default cost rates are seeded by migration."""
        rows = db.fetchall("SELECT * FROM cost_rates")
        assert len(rows) >= 10
        # Check a known rate
        row = db.fetchone(
            "SELECT rate FROM cost_rates WHERE name = ?",
            ("storage_standard_gb_month",),
        )
        assert row["rate"] == 0.023

    def test_foreign_keys_enforced(self, db: Database):
        """Foreign key constraints are active."""
        import sqlite3 as _sqlite3

        with pytest.raises(_sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO bucket_snapshots (bucket_id, snapshot_date) VALUES (?, ?)",
                (9999, "2025-01-01"),
            )


class TestPreferences:
    def test_get_missing_key_returns_default(self, db: Database):
        assert get_pref(db, "nonexistent") is None
        assert get_pref(db, "nonexistent", "fallback") == "fallback"

    def test_bool_pref(self, db: Database):
        set_pref(db, "flag", "true")
        assert get_bool_pref(db, "flag") is True

        set_pref(db, "flag", "false")
        assert get_bool_pref(db, "flag") is False

        assert get_bool_pref(db, "missing", default=True) is True

    def test_int_pref(self, db: Database):
        set_pref(db, "count", "42")
        assert get_int_pref(db, "count") == 42

        set_pref(db, "count", "not_a_number")
        assert get_int_pref(db, "count", default=0) == 0

        assert get_int_pref(db, "missing", default=10) == 10
