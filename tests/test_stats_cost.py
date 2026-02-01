"""Tests for stats collector, stats dialog, and cost dialog."""

from s3ui.core.stats import BucketSnapshot
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
