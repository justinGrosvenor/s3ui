"""S3 object data structures and Qt table model."""

from __future__ import annotations

import bisect
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt6.QtWidgets import QFileIconProvider

logger = logging.getLogger("s3ui.s3_objects")


@dataclass
class S3Item:
    """Represents a single S3 object or prefix (folder) in a listing."""

    name: str
    key: str
    is_prefix: bool
    size: int | None = None
    last_modified: datetime | None = None
    storage_class: str | None = None
    etag: str | None = None


def _sort_key(item: S3Item) -> tuple[int, str]:
    """Sort key: prefixes first (0), then objects (1), alphabetical by name."""
    return (0 if item.is_prefix else 1, item.name.lower())


def _format_size(size_bytes: int | None) -> str:
    """Format bytes into human-readable string."""
    if size_bytes is None:
        return ""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024**3):.1f} GB"


def _format_date(dt: datetime | None) -> str:
    """Format datetime into human-readable string."""
    if dt is None:
        return ""
    now = datetime.now(UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    delta = now - dt
    if delta.total_seconds() < 0:
        return dt.strftime("%b %d, %Y")
    if delta.total_seconds() < 60:
        return "Just now"
    if delta.total_seconds() < 3600:
        mins = int(delta.total_seconds() / 60)
        return f"{mins} minute{'s' if mins != 1 else ''} ago"
    if delta.total_seconds() < 86400:
        hours = int(delta.total_seconds() / 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    if dt.year == now.year:
        return dt.strftime("%b %d")
    return dt.strftime("%b %d, %Y")


# Column indices
COL_NAME = 0
COL_SIZE = 1
COL_MODIFIED = 2

_COLUMN_HEADERS = ["Name", "Size", "Date Modified"]
_COLUMN_COUNT = len(_COLUMN_HEADERS)


class S3ObjectModel(QAbstractTableModel):
    """Table model for S3 objects with granular mutation API."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._items: list[S3Item] = []
        self._icon_provider = QFileIconProvider()

    # --- Qt model interface ---

    _EMPTY_INDEX = QModelIndex()

    def rowCount(self, parent: QModelIndex = _EMPTY_INDEX) -> int:
        if parent.isValid():
            return 0
        return len(self._items)

    def columnCount(self, parent: QModelIndex = _EMPTY_INDEX) -> int:
        if parent.isValid():
            return 0
        return _COLUMN_COUNT

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._items):
            return None

        item = self._items[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if col == COL_NAME:
                return item.name
            elif col == COL_SIZE:
                if item.is_prefix:
                    return ""
                return _format_size(item.size)
            elif col == COL_MODIFIED:
                return _format_date(item.last_modified)
            return None

        if role == Qt.ItemDataRole.DecorationRole and col == COL_NAME:
            if item.is_prefix:
                return self._icon_provider.icon(QFileIconProvider.IconType.Folder)
            return self._icon_provider.icon(QFileIconProvider.IconType.File)

        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col == COL_SIZE:
                return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter

        if role == Qt.ItemDataRole.UserRole:
            return item

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

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        base = super().flags(index)
        if index.isValid():
            return base | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled
        return base

    # --- Data access ---

    def get_item(self, row: int) -> S3Item | None:
        """Get item at row index."""
        if 0 <= row < len(self._items):
            return self._items[row]
        return None

    def total_size(self) -> int:
        """Sum of all item sizes."""
        return sum(item.size or 0 for item in self._items if not item.is_prefix)

    def item_count(self) -> int:
        return len(self._items)

    def items(self) -> list[S3Item]:
        """Return a copy of the current items list."""
        return list(self._items)

    # --- Bulk operations ---

    def set_items(self, items: list[S3Item]) -> None:
        """Replace all items. Only for initial load."""
        self.beginResetModel()
        self._items = sorted(items, key=_sort_key)
        self.endResetModel()

    def clear(self) -> None:
        """Remove all items."""
        if not self._items:
            return
        self.beginResetModel()
        self._items.clear()
        self.endResetModel()

    # --- Granular mutation methods ---

    def insert_item(self, item: S3Item) -> int:
        """Insert item in sorted position. Returns the row index."""
        key = _sort_key(item)
        keys = [_sort_key(i) for i in self._items]
        row = bisect.bisect_left(keys, key)
        self.beginInsertRows(QModelIndex(), row, row)
        self._items.insert(row, item)
        self.endInsertRows()
        return row

    def remove_item(self, key: str) -> bool:
        """Remove item by key. Returns True if found."""
        for row, item in enumerate(self._items):
            if item.key == key:
                self.beginRemoveRows(QModelIndex(), row, row)
                self._items.pop(row)
                self.endRemoveRows()
                return True
        return False

    def remove_items(self, keys: set[str]) -> int:
        """Batch remove items by keys. Removes highest index first. Returns count removed."""
        rows_to_remove = []
        for row, item in enumerate(self._items):
            if item.key in keys:
                rows_to_remove.append(row)
        if not rows_to_remove:
            return 0
        # Remove from highest index first to avoid shifting
        for row in reversed(rows_to_remove):
            self.beginRemoveRows(QModelIndex(), row, row)
            self._items.pop(row)
            self.endRemoveRows()
        return len(rows_to_remove)

    def update_item(self, item_key: str, **fields) -> bool:
        """Update fields on an existing item. Emits dataChanged for that row only."""
        for row, item in enumerate(self._items):
            if item.key == item_key:
                for field, value in fields.items():
                    if hasattr(item, field):
                        setattr(item, field, value)
                top_left = self.index(row, 0)
                bottom_right = self.index(row, _COLUMN_COUNT - 1)
                self.dataChanged.emit(top_left, bottom_right)
                return True
        return False

    def append_items(self, items: list[S3Item]) -> None:
        """Append items at end (for incremental page loading)."""
        if not items:
            return
        start = len(self._items)
        end = start + len(items) - 1
        self.beginInsertRows(QModelIndex(), start, end)
        self._items.extend(items)
        self.endInsertRows()

    def diff_apply(self, new_items: list[S3Item]) -> bool:
        """Compute diff against current data and apply changes.

        Returns True if any changes were made.
        Used by background revalidation to minimize UI disruption.
        """
        new_sorted = sorted(new_items, key=_sort_key)
        old_by_key = {item.key: item for item in self._items}
        new_by_key = {item.key: item for item in new_sorted}

        old_keys = set(old_by_key.keys())
        new_keys = set(new_by_key.keys())

        removed_keys = old_keys - new_keys
        added_keys = new_keys - old_keys
        common_keys = old_keys & new_keys

        changed = False

        # Remove missing items
        if removed_keys:
            self.remove_items(removed_keys)
            changed = True

        # Update changed items
        for key in common_keys:
            old_item = old_by_key[key]
            new_item = new_by_key[key]
            updates = {}
            if old_item.size != new_item.size:
                updates["size"] = new_item.size
            if old_item.last_modified != new_item.last_modified:
                updates["last_modified"] = new_item.last_modified
            if old_item.storage_class != new_item.storage_class:
                updates["storage_class"] = new_item.storage_class
            if old_item.etag != new_item.etag:
                updates["etag"] = new_item.etag
            if old_item.name != new_item.name:
                updates["name"] = new_item.name
            if updates:
                self.update_item(key, **updates)
                changed = True

        # Insert new items
        for key in added_keys:
            self.insert_item(new_by_key[key])
            changed = True

        return changed
