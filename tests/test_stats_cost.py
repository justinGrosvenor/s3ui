"""Tests for stats collector, stats dialog, and cost dialog."""

from s3ui.core.cost import CostTracker
from s3ui.core.stats import BucketSnapshot
from s3ui.db.database import Database
from s3ui.ui.cost_dialog import CostDialog
from s3ui.ui.stats_dialog import StatsDialog


class TestBucketSnapshot:
    def test_defaults(self):
        snap = BucketSnapshot(bucket="test")
        assert snap.total_count == 0
        assert snap.total_bytes == 0
        assert snap.bytes_by_class == {}
        assert snap.top_largest == []


class TestStatsDialog:
    def test_creates(self, qtbot):
        dialog = StatsDialog()
        qtbot.addWidget(dialog)
        assert dialog.windowTitle() == "Bucket Stats"

    def test_with_bucket_name(self, qtbot):
        dialog = StatsDialog(bucket="my-bucket")
        qtbot.addWidget(dialog)
        assert "my-bucket" in dialog.windowTitle()

    def test_scan_button_exists(self, qtbot):
        dialog = StatsDialog()
        qtbot.addWidget(dialog)
        assert dialog._scan_btn.text() == "Scan"


class TestCostDialog:
    def test_creates(self, qtbot):
        dialog = CostDialog()
        qtbot.addWidget(dialog)
        assert dialog.windowTitle() == "Cost Dashboard"

    def test_estimate_label(self, qtbot):
        dialog = CostDialog()
        qtbot.addWidget(dialog)
        assert "—" in dialog._estimate_label.text()

    def test_daily_table_columns(self, qtbot):
        dialog = CostDialog()
        qtbot.addWidget(dialog)
        assert dialog._daily_table.columnCount() == 5

    def test_loads_with_tracker(self, qtbot, tmp_path):
        """CostDialog loads data when given a real CostTracker."""
        db = Database(tmp_path / "cost_dlg.db")
        bid = db.execute(
            "INSERT INTO buckets (name, region, profile) VALUES (?, ?, ?)",
            ("b", "us-east-1", "p"),
        ).lastrowid
        tracker = CostTracker(db, bid)
        tracker.record_request("put", 10)

        dialog = CostDialog(cost_tracker=tracker)
        qtbot.addWidget(dialog)

        assert "$" in dialog._estimate_label.text()
        assert dialog._daily_table.rowCount() > 0
        db.close()

    def test_export_csv(self, qtbot, tmp_path):
        """Export CSV writes valid data."""
        db = Database(tmp_path / "cost_csv.db")
        bid = db.execute(
            "INSERT INTO buckets (name, region, profile) VALUES (?, ?, ?)",
            ("b", "us-east-1", "p"),
        ).lastrowid
        tracker = CostTracker(db, bid)
        tracker.record_request("get", 5)

        dialog = CostDialog(cost_tracker=tracker)
        qtbot.addWidget(dialog)

        csv_path = tmp_path / "out.csv"
        # Call export directly with a known path (bypass file dialog)
        dialog._cost = tracker
        from unittest.mock import patch

        with patch(
            "s3ui.ui.cost_dialog.QFileDialog.getSaveFileName",
            return_value=(str(csv_path), "CSV Files (*.csv)"),
        ):
            dialog._export_csv()

        content = csv_path.read_text()
        assert "date," in content
        assert "total_cost" in content
        db.close()
