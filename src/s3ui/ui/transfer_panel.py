"""Transfer panel widget for the bottom dock."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from s3ui.models.transfer_model import TransferModel

if TYPE_CHECKING:
    from s3ui.core.transfers import TransferEngine
    from s3ui.db.database import Database

logger = logging.getLogger("s3ui.transfer_panel")


class TransferPanelWidget(QWidget):
    """Panel showing active and completed transfers."""

    pause_requested = pyqtSignal(int)  # transfer_id
    resume_requested = pyqtSignal(int)  # transfer_id
    cancel_requested = pyqtSignal(int)  # transfer_id
    retry_requested = pyqtSignal(int)  # transfer_id

    def __init__(self, db: Database | None = None, parent=None) -> None:
        super().__init__(parent)
        self._db = db
        self._engine: TransferEngine | None = None
        self._paused_global = False

        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header bar
        header = QHBoxLayout()
        header.setContentsMargins(8, 4, 8, 4)
        self._header_label = QLabel("Transfers")
        header.addWidget(self._header_label)
        header.addStretch()

        self._pause_all_btn = QPushButton("Pause All")
        self._pause_all_btn.clicked.connect(self._on_pause_all)
        header.addWidget(self._pause_all_btn)

        header_widget = QWidget()
        header_widget.setLayout(header)
        layout.addWidget(header_widget)

        # Transfer table
        self._model = TransferModel(self._db)
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setShowGrid(False)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        # Direction column narrow
        self._table.setColumnWidth(0, 30)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self._table)

    @property
    def model(self) -> TransferModel:
        return self._model

    def set_engine(self, engine: TransferEngine) -> None:
        """Wire the transfer engine signals to the model."""
        self._engine = engine
        engine.transfer_progress.connect(self._model.on_progress)
        engine.transfer_speed.connect(self._model.on_speed)
        engine.transfer_status_changed.connect(self._model.on_status_changed)
        engine.transfer_error.connect(self._model.on_error)
        engine.transfer_finished.connect(self._model.on_finished)

    def add_transfer(self, transfer_id: int) -> None:
        """Add a transfer to the panel."""
        self._model.add_transfer(transfer_id)
        self._update_header()

    def _update_header(self) -> None:
        active = self._model.active_count()
        queued = self._model.queued_count()
        parts = []
        if active:
            parts.append(f"{active} active")
        if queued:
            parts.append(f"{queued} queued")
        if parts:
            self._header_label.setText(f"Transfers ({', '.join(parts)})")
        else:
            self._header_label.setText("Transfers")

    def _on_pause_all(self) -> None:
        if not self._engine:
            return
        if self._paused_global:
            self._engine.resume_all()
            self._pause_all_btn.setText("Pause All")
            self._paused_global = False
        else:
            self._engine.pause_all()
            self._pause_all_btn.setText("Resume All")
            self._paused_global = True

    def _on_context_menu(self, pos) -> None:
        from PyQt6.QtWidgets import QMenu

        index = self._table.indexAt(pos)
        if not index.isValid():
            return

        row_data = self._model.get_transfer_row(self._model._rows[index.row()].transfer_id)
        if not row_data:
            return

        menu = QMenu(self)
        tid = row_data.transfer_id

        if row_data.status == "in_progress":
            pause_action = menu.addAction("Pause")
            pause_action.triggered.connect(lambda: self.pause_requested.emit(tid))
        elif row_data.status == "paused":
            resume_action = menu.addAction("Resume")
            resume_action.triggered.connect(lambda: self.resume_requested.emit(tid))
        elif row_data.status == "failed":
            retry_action = menu.addAction("Retry")
            retry_action.triggered.connect(lambda: self.retry_requested.emit(tid))

        if row_data.status in ("queued", "in_progress", "paused"):
            cancel_action = menu.addAction("Cancel")
            cancel_action.triggered.connect(lambda: self.cancel_requested.emit(tid))

        menu.exec(self._table.viewport().mapToGlobal(pos))
