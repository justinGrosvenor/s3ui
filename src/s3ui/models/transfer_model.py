"""Transfer model for the transfer panel with signal coalescing."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer, pyqtSignal

if TYPE_CHECKING:
    from s3ui.db.database import Database

logger = logging.getLogger("s3ui.transfer_model")

# Column indices
COL_DIRECTION = 0
COL_FILE = 1
COL_PROGRESS = 2
COL_SPEED = 3
COL_ETA = 4
COL_STATUS = 5

_COLUMN_HEADERS = ["", "File", "Progress", "Speed", "ETA", "Status"]
_COLUMN_COUNT = len(_COLUMN_HEADERS)


@dataclass
class TransferRow:
    """In-memory representation of a transfer for display."""

    transfer_id: int
    direction: str  # "upload" or "download"
    filename: str
    local_path: str
    s3_key: str
    total_bytes: int
    transferred_bytes: int = 0
    speed_bps: float = 0.0
    eta_seconds: float = 0.0
    _smoothed_eta: float = 0.0
    status: str = "queued"
    error_message: str = ""


def _format_speed(bps: float) -> str:
    if bps <= 0:
        return "—"
    if bps < 1024:
        return f"{bps:.0f} B/s"
    if bps < 1024 * 1024:
        return f"{bps / 1024:.1f} KB/s"
    return f"{bps / (1024 * 1024):.1f} MB/s"


def _format_eta(seconds: float) -> str:
    if seconds <= 0:
        return "—"
    if seconds < 60:
        return f"~{int(seconds)} sec"
    if seconds < 3600:
        return f"~{int(seconds / 60)} min"
    return f"~{seconds / 3600:.1f} hr"


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024**3:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024**3):.1f} GB"


def _format_progress(transferred: int, total: int) -> str:
    if total <= 0:
        return _format_size(transferred) if transferred > 0 else ""
    return f"{_format_size(transferred)} / {_format_size(total)}"


def _format_pct(transferred: int, total: int) -> str:
    if total <= 0:
        return "0%"
    pct = max(0, min((transferred / total) * 100, 100))
    return f"{pct:.0f}%"


def _format_status(row: TransferRow) -> str:
    if row.status == "completed":
        return "Complete"
    if row.status == "failed":
        return "Failed"
    if row.status == "cancelled":
        return "Cancelled"
    if row.status == "paused":
        return "Paused"
    if row.status == "in_progress":
        return _format_pct(row.transferred_bytes, row.total_bytes)
    return "Queued"


class TransferModel(QAbstractTableModel):
    """Table model for transfers with 100ms coalesced updates."""

    _EMPTY_INDEX = QModelIndex()
    counts_changed = pyqtSignal()

    def __init__(self, db: Database | None = None, parent=None) -> None:
        super().__init__(parent)
        self._db = db
        self._rows: list[TransferRow] = []
        self._id_to_row: dict[int, int] = {}
        self._pending_updates: dict[int, dict] = {}
        self._dirty_rows: set[int] = set()

        # Coalescing timer
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._flush_updates)

    # --- Qt model interface ---

    def rowCount(self, parent: QModelIndex = _EMPTY_INDEX) -> int:
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = _EMPTY_INDEX) -> int:
        if parent.isValid():
            return 0
        return _COLUMN_COUNT

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._rows):
            return None

        row = self._rows[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if col == COL_DIRECTION:
                return "↑" if row.direction == "upload" else "↓"
            if col == COL_FILE:
                return row.filename
            if col == COL_PROGRESS:
                return _format_progress(row.transferred_bytes, row.total_bytes)
            if col == COL_SPEED:
                if row.status == "in_progress":
                    return _format_speed(row.speed_bps)
                return "—"
            if col == COL_ETA:
                if row.status == "in_progress":
                    return _format_eta(row.eta_seconds)
                return "—"
            if col == COL_STATUS:
                return _format_status(row)
            return None

        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col in (COL_PROGRESS, COL_SPEED, COL_ETA):
                return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            if col == COL_DIRECTION:
                return Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
            return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter

        if role == Qt.ItemDataRole.UserRole:
            return row

        if role == Qt.ItemDataRole.ToolTipRole and col == COL_STATUS:
            if row.status == "failed":
                return row.error_message
            return None

        return None

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ):
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
            and 0 <= section < _COLUMN_COUNT
        ):
            return _COLUMN_HEADERS[section]
        return None

    # --- Public API ---

    def add_transfer(self, transfer_id: int) -> None:
        """Add a single transfer from the database."""
        self.add_transfers([transfer_id])

    def add_transfers(self, transfer_ids: list[int]) -> None:
        """Add many transfers in one model insertion.

        Building each row with its own query and its own beginInsertRows cycle
        is what froze the UI on huge folder selections (hundreds of thousands
        of single-row inserts). Fetch the batch in one pass and insert the whole
        span at once so a 400k-file drop stays responsive.
        """
        if self._db is None or not transfer_ids:
            return

        from pathlib import Path

        # Preserve caller order, drop duplicates and anything already shown.
        seen: set[int] = set()
        wanted: list[int] = []
        for tid in transfer_ids:
            if tid in self._id_to_row or tid in seen:
                continue
            seen.add(tid)
            wanted.append(tid)
        if not wanted:
            return

        # Fetch in chunks to stay under SQLite's bound-parameter limit, then
        # emit their rows in the caller's original order.
        fetched: dict[int, object] = {}
        for start in range(0, len(wanted), 900):
            chunk = wanted[start : start + 900]
            placeholders = ",".join("?" * len(chunk))
            for db_row in self._db.fetchall(
                f"SELECT * FROM transfers WHERE id IN ({placeholders})", tuple(chunk)
            ):
                fetched[db_row["id"]] = db_row

        new_rows: list[TransferRow] = []
        for tid in wanted:
            db_row = fetched.get(tid)
            if db_row is None:
                continue
            new_rows.append(
                TransferRow(
                    transfer_id=tid,
                    direction=db_row["direction"],
                    filename=Path(db_row["local_path"]).name,
                    local_path=db_row["local_path"],
                    s3_key=db_row["object_key"],
                    total_bytes=db_row["total_bytes"] or 0,
                    transferred_bytes=db_row["transferred"] or 0,
                    status=db_row["status"],
                )
            )
        if not new_rows:
            return

        first = len(self._rows)
        last = first + len(new_rows) - 1
        self.beginInsertRows(QModelIndex(), first, last)
        for offset, row in enumerate(new_rows):
            self._rows.append(row)
            self._id_to_row[row.transfer_id] = first + offset
        self.endInsertRows()
        self.counts_changed.emit()

        if not self._timer.isActive():
            self._timer.start()

    def remove_by_status(self, statuses) -> list[int]:
        """Drop every row whose status is in `statuses`; return their ids.

        Used to reclaim memory: a cancelled flood or a long run's worth of
        completed rows are removed in one model reset rather than lingering
        one dataclass per file. Returns the removed transfer ids so the caller
        can delete them from the database in the same sweep.
        """
        statuses = set(statuses)
        removed = [r.transfer_id for r in self._rows if r.status in statuses]
        if not removed:
            return []
        self.beginResetModel()
        self._rows = [r for r in self._rows if r.status not in statuses]
        self._id_to_row = {r.transfer_id: i for i, r in enumerate(self._rows)}
        for tid in removed:
            self._pending_updates.pop(tid, None)
        # Row indices changed; re-derive dirty rows for surviving pending updates.
        self._dirty_rows = {
            self._id_to_row[t] for t in self._pending_updates if t in self._id_to_row
        }
        self.endResetModel()
        self.counts_changed.emit()
        return removed

    def get_transfer_row(self, transfer_id: int) -> TransferRow | None:
        idx = self._id_to_row.get(transfer_id)
        if idx is not None and idx < len(self._rows):
            return self._rows[idx]
        return None

    # --- Signal handlers (buffer into pending updates) ---

    def on_progress(self, transfer_id: int, bytes_done: int, total: int) -> None:
        self._buffer_update(transfer_id, transferred_bytes=bytes_done, total_bytes=total)

    def on_speed(self, transfer_id: int, bps: float) -> None:
        idx = self._id_to_row.get(transfer_id)
        if idx is not None:
            row = self._rows[idx]
            remaining = row.total_bytes - row.transferred_bytes
            eta = remaining / bps if bps > 0 else 0
            # ETA smoothing
            smoothed = 0.7 * eta + 0.3 * row._smoothed_eta if row._smoothed_eta > 0 else eta
            self._buffer_update(
                transfer_id, speed_bps=bps, eta_seconds=smoothed, _smoothed_eta=smoothed
            )

    def on_status_changed(self, transfer_id: int, new_status: str) -> None:
        self._buffer_update(transfer_id, status=new_status)

    def on_error(self, transfer_id: int, user_msg: str, detail: str) -> None:
        self._buffer_update(transfer_id, status="failed", error_message=user_msg)

    def on_finished(self, transfer_id: int) -> None:
        self._buffer_update(
            transfer_id, status="completed", speed_bps=0, eta_seconds=0, _smoothed_eta=0
        )

    # --- Internal ---

    def _buffer_update(self, transfer_id: int, **fields) -> None:
        if transfer_id not in self._pending_updates:
            self._pending_updates[transfer_id] = {}
        self._pending_updates[transfer_id].update(fields)

        idx = self._id_to_row.get(transfer_id)
        if idx is not None:
            self._dirty_rows.add(idx)
            if not self._timer.isActive():
                self._timer.start()

    def _flush_updates(self) -> None:
        if not self._pending_updates:
            self._timer.stop()
            return

        counts_changed = False
        for transfer_id, fields in self._pending_updates.items():
            idx = self._id_to_row.get(transfer_id)
            if idx is None or idx >= len(self._rows):
                continue
            row = self._rows[idx]
            counts_changed |= "status" in fields and fields["status"] != row.status
            for field_name, value in fields.items():
                if hasattr(row, field_name):
                    setattr(row, field_name, value)

        if self._dirty_rows:
            min_row = min(self._dirty_rows)
            max_row = max(self._dirty_rows)
            top_left = self.index(min_row, 0)
            bottom_right = self.index(max_row, _COLUMN_COUNT - 1)
            self.dataChanged.emit(top_left, bottom_right)

        self._pending_updates.clear()
        self._dirty_rows.clear()
        self._timer.stop()  # The next incoming update rearms the coalescing timer.
        if counts_changed:
            self.counts_changed.emit()

    def active_count(self) -> int:
        return sum(1 for r in self._rows if r.status == "in_progress")

    def queued_count(self) -> int:
        return sum(1 for r in self._rows if r.status == "queued")
