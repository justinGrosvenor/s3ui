"""Bucket statistics dialog."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from s3ui.core.stats import BucketSnapshot, StatsCollector
from s3ui.models.s3_objects import _format_size

if TYPE_CHECKING:
    from s3ui.core.s3_client import S3Client
    from s3ui.db.database import Database


class StatsDialog(QDialog):
    """Shows bucket statistics and storage breakdown."""

    def __init__(
        self,
        s3_client: S3Client | None = None,
        bucket: str = "",
        db: Database | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._s3 = s3_client
        self._bucket = bucket
        self._db = db
        self._collector: StatsCollector | None = None

        self.setWindowTitle(f"Bucket Stats — {bucket}" if bucket else "Bucket Stats")
        self.setMinimumSize(500, 400)

        layout = QVBoxLayout(self)

        # Summary
        self._summary = QLabel("No data yet. Click Scan to begin.")
        self._summary.setStyleSheet("font-size: 14px;")
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)

        # Progress
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 0)  # indeterminate
        self._progress_bar.setVisible(False)
        layout.addWidget(self._progress_bar)

        self._progress_label = QLabel("")
        self._progress_label.setVisible(False)
        layout.addWidget(self._progress_label)

        # Storage breakdown table
        self._breakdown_table = QTableWidget()
        self._breakdown_table.setColumnCount(3)
        self._breakdown_table.setHorizontalHeaderLabels(["Storage Class", "Size", "Objects"])
        self._breakdown_table.setVisible(False)
        layout.addWidget(self._breakdown_table)

        # Top 10 largest
        self._largest_label = QLabel("Largest Files:")
        self._largest_label.setStyleSheet("font-weight: bold; margin-top: 10px;")
        self._largest_label.setVisible(False)
        layout.addWidget(self._largest_label)

        self._largest_table = QTableWidget()
        self._largest_table.setColumnCount(2)
        self._largest_table.setHorizontalHeaderLabels(["Key", "Size"])
        self._largest_table.setVisible(False)
        layout.addWidget(self._largest_table)

        # Buttons
        btn_layout = QDialogButtonBox()
        self._scan_btn = QPushButton("Scan")
        self._scan_btn.clicked.connect(self._start_scan)
        btn_layout.addButton(self._scan_btn, QDialogButtonBox.ButtonRole.ActionRole)

        self._cancel_btn = QPushButton("Cancel Scan")
        self._cancel_btn.clicked.connect(self._cancel_scan)
        self._cancel_btn.setVisible(False)
        btn_layout.addButton(self._cancel_btn, QDialogButtonBox.ButtonRole.ActionRole)

        close_btn = btn_layout.addButton(QDialogButtonBox.StandardButton.Close)
        close_btn.clicked.connect(self.accept)
        layout.addWidget(btn_layout)

    def _start_scan(self) -> None:
        if not self._s3 or not self._bucket:
            return

        self._scan_btn.setVisible(False)
        self._cancel_btn.setVisible(True)
        self._progress_bar.setVisible(True)
        self._progress_label.setVisible(True)
        self._summary.setText("Scanning...")

        self._collector = StatsCollector(self._s3, self._bucket, self._db, self)
        self._collector.signals.progress.connect(self._on_progress)
        self._collector.signals.complete.connect(self._on_complete)
        self._collector.signals.error.connect(self._on_error)
        self._collector.finished.connect(self._collector.deleteLater)
        self._collector.start()

    def _cancel_scan(self) -> None:
        if self._collector:
            self._collector.cancel()
        self._scan_btn.setVisible(True)
        self._cancel_btn.setVisible(False)
        self._progress_bar.setVisible(False)
        self._progress_label.setVisible(False)
        self._summary.setText("Scan cancelled.")

    def _on_progress(self, count: int) -> None:
        self._progress_label.setText(f"Scanned {count:,} objects...")

    def _on_complete(self, snapshot: BucketSnapshot) -> None:
        self._scan_btn.setVisible(True)
        self._scan_btn.setText("Rescan")
        self._cancel_btn.setVisible(False)
        self._progress_bar.setVisible(False)
        self._progress_label.setVisible(False)

        self._summary.setText(
            f"Total: {snapshot.total_count:,} objects, {_format_size(snapshot.total_bytes)}"
        )

        # Breakdown table
        self._breakdown_table.setVisible(True)
        self._breakdown_table.setRowCount(len(snapshot.bytes_by_class))
        for i, (cls, size) in enumerate(
            sorted(snapshot.bytes_by_class.items(), key=lambda x: -x[1])
        ):
            self._breakdown_table.setItem(i, 0, QTableWidgetItem(cls))
            self._breakdown_table.setItem(i, 1, QTableWidgetItem(_format_size(size)))
            count = snapshot.count_by_class.get(cls, 0)
            self._breakdown_table.setItem(i, 2, QTableWidgetItem(f"{count:,}"))

        # Top 10 largest
        if snapshot.top_largest:
            self._largest_label.setVisible(True)
            self._largest_table.setVisible(True)
            self._largest_table.setRowCount(len(snapshot.top_largest))
            for i, entry in enumerate(snapshot.top_largest):
                self._largest_table.setItem(i, 0, QTableWidgetItem(entry["key"]))
                self._largest_table.setItem(i, 1, QTableWidgetItem(_format_size(entry["size"])))

    def _on_error(self, msg: str) -> None:
        self._scan_btn.setVisible(True)
        self._cancel_btn.setVisible(False)
        self._progress_bar.setVisible(False)
        self._progress_label.setVisible(False)
        self._summary.setText(f"Error: {msg}")
