"""Cost dashboard dialog."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

if TYPE_CHECKING:
    from s3ui.core.cost import CostTracker


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
        self._daily_table.setHorizontalHeaderLabels([
            "Date", "Requests", "Upload", "Download", "Est. Cost"
        ])
        layout.addWidget(self._daily_table)

        # Buttons
        btn_layout = QDialogButtonBox()
        from PyQt6.QtWidgets import QPushButton

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
        self._estimate_label.setText(f"Estimated cost this month: ${estimate:.2f}")

        days = self._cost.get_daily_costs(30)
        self._daily_table.setRowCount(len(days))
        for i, day in enumerate(days):
            self._daily_table.setItem(i, 0, QTableWidgetItem(day.date))
            total_requests = (
                day.list_requests + day.get_requests + day.put_requests
                + day.delete_requests + day.head_requests + day.copy_requests
            )
            self._daily_table.setItem(i, 1, QTableWidgetItem(str(total_requests)))
            self._daily_table.setItem(i, 2, QTableWidgetItem(f"{day.upload_bytes:,} B"))
            self._daily_table.setItem(i, 3, QTableWidgetItem(f"{day.download_bytes:,} B"))
            self._daily_table.setItem(i, 4, QTableWidgetItem(f"${day.total_cost:.4f}"))

    def _export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Cost Data", "s3ui-costs.csv", "CSV Files (*.csv)"
        )
        if not path:
            return

        if not self._cost:
            return

        days = self._cost.get_daily_costs(365)
        with open(path, "w") as f:
            f.write("date,list_requests,get_requests,put_requests,delete_requests,"
                    "head_requests,copy_requests,upload_bytes,download_bytes,total_cost\n")
            for day in days:
                f.write(
                    f"{day.date},{day.list_requests},{day.get_requests},"
                    f"{day.put_requests},{day.delete_requests},"
                    f"{day.head_requests},{day.copy_requests},"
                    f"{day.upload_bytes},{day.download_bytes},{day.total_cost:.6f}\n"
                )
