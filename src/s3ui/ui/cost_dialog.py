"""Cost dashboard dialog."""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

if TYPE_CHECKING:
    from s3ui.core.cost import CostTracker


def _fmt_bytes(n: int) -> str:
    """Human-readable byte size."""
    if n < 1024:
        return f"{n} B"
    if n < 1024**2:
        return f"{n / 1024:.1f} KB"
    if n < 1024**3:
        return f"{n / 1024**2:.1f} MB"
    return f"{n / 1024**3:.2f} GB"


class CostDialog(QDialog):
    """Shows monthly cost estimate and daily breakdown."""

    def __init__(self, cost_tracker: CostTracker | None = None, parent=None) -> None:
        super().__init__(parent)
        self._cost = cost_tracker
        self.setWindowTitle("Cost Dashboard")
        self.setMinimumSize(500, 400)

        layout = QVBoxLayout(self)

        # Monthly estimate
        self._estimate_label = QLabel("Estimated cost this month: —")
        self._estimate_label.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(self._estimate_label)

        # Daily breakdown table
        layout.addWidget(QLabel("Daily Breakdown (last 30 days):"))
        self._daily_table = QTableWidget()
        self._daily_table.setColumnCount(5)
        self._daily_table.setHorizontalHeaderLabels(
            ["Date", "Requests", "Upload", "Download", "Est. Cost"]
        )
        layout.addWidget(self._daily_table)

        # Buttons
        btn_layout = QDialogButtonBox()

        export_btn = QPushButton("Export CSV")
        export_btn.clicked.connect(self._export_csv)
        btn_layout.addButton(export_btn, QDialogButtonBox.ButtonRole.ActionRole)

        close_btn = btn_layout.addButton(QDialogButtonBox.StandardButton.Close)
        close_btn.clicked.connect(self.accept)
        layout.addWidget(btn_layout)

        self._load_data()

    def _load_data(self) -> None:
        if not self._cost:
            return

        estimate = self._cost.get_monthly_estimate()
        self._estimate_label.setText(f"Estimated cost this month: ${estimate:.4f}")

        today = date.today()
        start = (today - timedelta(days=29)).isoformat()
        end = today.isoformat()
        days = self._cost.get_daily_costs(start, end)

        # Get raw usage rows for request counts and byte totals
        usage_map = self._build_usage_map(start, end)

        self._daily_table.setRowCount(len(days))
        for i, day in enumerate(days):
            self._daily_table.setItem(i, 0, QTableWidgetItem(day.date))

            usage = usage_map.get(day.date)
            if usage:
                total_reqs = (
                    (usage["put_requests"] or 0)
                    + (usage["get_requests"] or 0)
                    + (usage["list_requests"] or 0)
                    + (usage["delete_requests"] or 0)
                    + (usage["head_requests"] or 0)
                    + (usage["copy_requests"] or 0)
                )
                upload = usage["bytes_uploaded"] or 0
                download = usage["bytes_downloaded"] or 0
            else:
                total_reqs = 0
                upload = 0
                download = 0

            self._daily_table.setItem(i, 1, QTableWidgetItem(f"{total_reqs:,}"))
            self._daily_table.setItem(i, 2, QTableWidgetItem(_fmt_bytes(upload)))
            self._daily_table.setItem(i, 3, QTableWidgetItem(_fmt_bytes(download)))
            self._daily_table.setItem(i, 4, QTableWidgetItem(f"${day.total:.4f}"))

    def _build_usage_map(self, start: str, end: str) -> dict:
        """Query raw daily_usage rows and index by date."""
        if not self._cost or not self._cost._db:
            return {}
        rows = self._cost._db.fetchall(
            "SELECT * FROM daily_usage WHERE bucket_id = ? "
            "AND usage_date >= ? AND usage_date <= ? ORDER BY usage_date",
            (self._cost._bucket_id, start, end),
        )
        return {row["usage_date"]: row for row in rows}

    def _export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Cost Data", "s3ui-costs.csv", "CSV Files (*.csv)"
        )
        if not path or not self._cost:
            return

        today = date.today()
        start = (today - timedelta(days=364)).isoformat()
        end = today.isoformat()
        days = self._cost.get_daily_costs(start, end)
        usage_map = self._build_usage_map(start, end)

        with open(path, "w") as f:
            f.write(
                "date,put_requests,get_requests,list_requests,delete_requests,"
                "head_requests,copy_requests,bytes_uploaded,bytes_downloaded,"
                "storage_cost,request_cost,transfer_cost,total_cost\n"
            )
            for day in days:
                usage = usage_map.get(day.date)
                if usage:
                    f.write(
                        f"{day.date},{usage['put_requests'] or 0},"
                        f"{usage['get_requests'] or 0},"
                        f"{usage['list_requests'] or 0},"
                        f"{usage['delete_requests'] or 0},"
                        f"{usage['head_requests'] or 0},"
                        f"{usage['copy_requests'] or 0},"
                        f"{usage['bytes_uploaded'] or 0},"
                        f"{usage['bytes_downloaded'] or 0},"
                        f"{day.storage:.6f},{day.requests:.6f},"
                        f"{day.transfer:.6f},{day.total:.6f}\n"
                    )
                else:
                    f.write(
                        f"{day.date},0,0,0,0,0,0,0,0,"
                        f"{day.storage:.6f},0.000000,0.000000,"
                        f"{day.storage:.6f}\n"
                    )
