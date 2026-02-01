"""Cost tracking and estimation for S3 operations."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from s3ui.db.database import Database

logger = logging.getLogger("s3ui.cost")

REQUEST_TYPE_COLUMN = {
    "put": "put_requests",
    "get": "get_requests",
    "list": "list_requests",
    "delete": "delete_requests",
    "copy": "copy_requests",
    "head": "head_requests",
}


@dataclass
class DailyCost:
    date: str
    storage: float
    requests: float
    transfer: float

    @property
    def total(self) -> float:
        return self.storage + self.requests + self.transfer


class CostTracker:
    """Tracks S3 API usage and calculates cost estimates."""

    def __init__(self, db: Database, bucket_id: int) -> None:
        self._db = db
        self._bucket_id = bucket_id

    def record_request(self, request_type: str, count: int = 1) -> None:
        """Record API request(s) for cost tracking."""
        column = REQUEST_TYPE_COLUMN.get(request_type)
        if not column:
            logger.warning("Unknown request type: %s", request_type)
            return

        today = date.today().isoformat()
        self._db.execute(
            f"INSERT INTO daily_usage (bucket_id, usage_date, {column}) "
            f"VALUES (?, ?, ?) "
            f"ON CONFLICT(bucket_id, usage_date) DO UPDATE SET {column} = {column} + ?",
            (self._bucket_id, today, count, count),
        )

    def record_upload_bytes(self, size: int) -> None:
        """Record bytes uploaded."""
        today = date.today().isoformat()
        self._db.execute(
            "INSERT INTO daily_usage (bucket_id, usage_date, bytes_uploaded) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(bucket_id, usage_date) DO UPDATE SET bytes_uploaded = bytes_uploaded + ?",
            (self._bucket_id, today, size, size),
        )

    def record_download_bytes(self, size: int) -> None:
        """Record bytes downloaded."""
        today = date.today().isoformat()
        self._db.execute(
            "INSERT INTO daily_usage (bucket_id, usage_date, bytes_downloaded) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(bucket_id, usage_date) DO UPDATE "
            "SET bytes_downloaded = bytes_downloaded + ?",
            (self._bucket_id, today, size, size),
        )

    def get_rate(self, name: str) -> float:
        """Get a cost rate by name."""
        row = self._db.fetchone("SELECT rate FROM cost_rates WHERE name = ?", (name,))
        return row["rate"] if row else 0.0

    def set_rate(self, name: str, rate: float) -> None:
        """Update a cost rate."""
        self._db.execute(
            "UPDATE cost_rates SET rate = ?, updated_at = datetime('now') WHERE name = ?",
            (rate, name),
        )

    def get_daily_cost(self, target_date: str) -> DailyCost:
        """Calculate estimated cost for a single day."""
        storage_cost = self._calc_storage_cost(target_date)
        request_cost = self._calc_request_cost(target_date)
        transfer_cost = self._calc_transfer_cost(target_date)
        return DailyCost(
            date=target_date,
            storage=storage_cost,
            requests=request_cost,
            transfer=transfer_cost,
        )

    def get_daily_costs(self, start_date: str, end_date: str) -> list[DailyCost]:
        """Get daily costs for a date range (for charting)."""
        costs = []
        current = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        while current <= end:
            costs.append(self.get_daily_cost(current.isoformat()))
            current += timedelta(days=1)
        return costs

    def get_monthly_estimate(self) -> float:
        """Estimate the current month's total cost."""
        today = date.today()
        first_of_month = today.replace(day=1)

        # Storage: use the most recent snapshot, prorate for the full month
        snapshot = self._db.fetchone(
            "SELECT total_bytes, standard_bytes, ia_bytes, glacier_bytes, "
            "deep_archive_bytes, intelligent_tiering_bytes "
            "FROM bucket_snapshots WHERE bucket_id = ? "
            "ORDER BY snapshot_date DESC LIMIT 1",
            (self._bucket_id,),
        )
        if snapshot:
            import calendar

            days_in_month = calendar.monthrange(today.year, today.month)[1]
            storage_cost = self._storage_cost_from_snapshot(snapshot) * days_in_month
        else:
            storage_cost = 0.0

        # Requests and transfer: sum month-to-date
        request_cost = 0.0
        transfer_cost = 0.0
        rows = self._db.fetchall(
            "SELECT * FROM daily_usage WHERE bucket_id = ? AND usage_date >= ?",
            (self._bucket_id, first_of_month.isoformat()),
        )
        for row in rows:
            request_cost += self._request_cost_from_row(row)
            transfer_cost += self._transfer_cost_from_row(row)

        return storage_cost + request_cost + transfer_cost

    # --- Internal calculation methods ---

    def _calc_storage_cost(self, target_date: str) -> float:
        """Storage cost for one day based on the most recent snapshot."""
        snapshot = self._db.fetchone(
            "SELECT * FROM bucket_snapshots WHERE bucket_id = ? AND snapshot_date <= ? "
            "ORDER BY snapshot_date DESC LIMIT 1",
            (self._bucket_id, target_date),
        )
        if not snapshot:
            return 0.0
        return self._storage_cost_from_snapshot(snapshot)

    def _storage_cost_from_snapshot(self, snapshot) -> float:
        """Calculate daily storage cost from a snapshot row."""
        gb = 1024**3
        tiers = [
            ("standard_bytes", "storage_standard_gb_month"),
            ("ia_bytes", "storage_ia_gb_month"),
            ("glacier_bytes", "storage_glacier_gb_month"),
            ("deep_archive_bytes", "storage_deep_archive_gb_month"),
            ("intelligent_tiering_bytes", "storage_intelligent_tiering_gb_month"),
        ]
        cost = 0.0
        for col, rate_name in tiers:
            cost += (snapshot[col] or 0) / gb * self.get_rate(rate_name) / 30
        return cost

    def _calc_request_cost(self, target_date: str) -> float:
        row = self._db.fetchone(
            "SELECT * FROM daily_usage WHERE bucket_id = ? AND usage_date = ?",
            (self._bucket_id, target_date),
        )
        if not row:
            return 0.0
        return self._request_cost_from_row(row)

    def _request_cost_from_row(self, row) -> float:
        cost = 0.0
        cost += (row["put_requests"] or 0) * self.get_rate("put_request")
        cost += (row["get_requests"] or 0) * self.get_rate("get_request")
        cost += (row["list_requests"] or 0) * self.get_rate("list_request")
        cost += (row["delete_requests"] or 0) * self.get_rate("delete_request")
        cost += (row["copy_requests"] or 0) * self.get_rate("copy_request")
        cost += (row["head_requests"] or 0) * self.get_rate("head_request")
        return cost

    def _calc_transfer_cost(self, target_date: str) -> float:
        row = self._db.fetchone(
            "SELECT * FROM daily_usage WHERE bucket_id = ? AND usage_date = ?",
            (self._bucket_id, target_date),
        )
        if not row:
            return 0.0
        return self._transfer_cost_from_row(row)

    def _transfer_cost_from_row(self, row) -> float:
        # Transfer IN is free
        # Transfer OUT is tiered
        gb = 1024**3
        bytes_out = row["bytes_downloaded"] or 0
        gb_out = bytes_out / gb

        cost = 0.0
        if gb_out <= 100:
            cost = gb_out * self.get_rate("transfer_out_gb_first_100")
        else:
            cost = 100 * self.get_rate("transfer_out_gb_first_100")
            cost += (gb_out - 100) * self.get_rate("transfer_out_gb_next_10k")

        return cost
