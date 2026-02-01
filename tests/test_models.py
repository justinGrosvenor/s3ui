"""Tests for S3ObjectModel."""

from datetime import UTC, datetime, timedelta

from PyQt6.QtCore import Qt

from s3ui.models.s3_objects import (
    S3Item,
    S3ObjectModel,
    _format_date,
    _format_size,
    _sort_key,
)


def _make_item(name: str, key: str = "", is_prefix: bool = False, **kwargs) -> S3Item:
    return S3Item(name=name, key=key or name, is_prefix=is_prefix, **kwargs)


class TestSortKey:
    def test_prefixes_before_objects(self):
        folder = _make_item("docs/", key="docs/", is_prefix=True)
        file = _make_item("a.txt")
        assert _sort_key(folder) < _sort_key(file)

    def test_alphabetical_within_type(self):
        a = _make_item("alpha.txt")
        b = _make_item("beta.txt")
        assert _sort_key(a) < _sort_key(b)


class TestFormatSize:
    def test_none(self):
        assert _format_size(None) == ""

    def test_bytes(self):
        assert _format_size(0) == "0 B"
        assert _format_size(512) == "512 B"

    def test_kb(self):
        assert _format_size(1024) == "1.0 KB"

    def test_mb(self):
        assert _format_size(999 * 1024 * 1024) == "999.0 MB"

    def test_gb(self):
        assert _format_size(int(2.4 * 1024 ** 3)) == "2.4 GB"


class TestFormatDate:
    def test_none(self):
        assert _format_date(None) == ""

    def test_recent(self):
        now = datetime.now(UTC)
        result = _format_date(now - timedelta(seconds=30))
        assert result == "Just now"

    def test_minutes_ago(self):
        now = datetime.now(UTC)
        result = _format_date(now - timedelta(minutes=5))
        assert "5 minutes ago" in result

    def test_hours_ago(self):
        now = datetime.now(UTC)
        result = _format_date(now - timedelta(hours=3))
        assert "3 hours ago" in result

    def test_same_year(self):
        now = datetime.now(UTC)
        dt = now - timedelta(days=30)
        result = _format_date(dt)
        # Should be like "Jan 02" format (no year)
        assert str(dt.year) not in result

    def test_different_year(self):
        dt = datetime(2020, 6, 15, tzinfo=UTC)
        result = _format_date(dt)
        assert "2020" in result


class TestS3ObjectModel:
    def test_empty_model(self, qtbot):
        model = S3ObjectModel()
        assert model.rowCount() == 0
        assert model.columnCount() == 3

    def test_set_items(self, qtbot):
        model = S3ObjectModel()
        items = [
            _make_item("b.txt", size=100),
            _make_item("a.txt", size=200),
            _make_item("docs/", key="docs/", is_prefix=True),
        ]
        model.set_items(items)
        assert model.rowCount() == 3
        # Prefixes first, then alphabetical
        assert model.get_item(0).name == "docs/"
        assert model.get_item(1).name == "a.txt"
        assert model.get_item(2).name == "b.txt"

    def test_column_headers(self, qtbot):
        model = S3ObjectModel()
        assert model.headerData(0, Qt.Orientation.Horizontal) == "Name"
        assert model.headerData(1, Qt.Orientation.Horizontal) == "Size"
        assert model.headerData(2, Qt.Orientation.Horizontal) == "Date Modified"

    def test_data_display_role(self, qtbot):
        model = S3ObjectModel()
        items = [_make_item("test.txt", size=2048)]
        model.set_items(items)
        # Name column
        idx = model.index(0, 0)
        assert model.data(idx, Qt.ItemDataRole.DisplayRole) == "test.txt"
        # Size column
        idx = model.index(0, 1)
        assert model.data(idx, Qt.ItemDataRole.DisplayRole) == "2.0 KB"

    def test_total_size(self, qtbot):
        model = S3ObjectModel()
        model.set_items([
            _make_item("a.txt", size=100),
            _make_item("b.txt", size=200),
            _make_item("dir/", key="dir/", is_prefix=True),
        ])
        assert model.total_size() == 300

    def test_item_count(self, qtbot):
        model = S3ObjectModel()
        model.set_items([_make_item("a.txt"), _make_item("b.txt")])
        assert model.item_count() == 2

    def test_insert_item_sorted_position(self, qtbot):
        model = S3ObjectModel()
        model.set_items([_make_item("a.txt"), _make_item("c.txt")])
        row = model.insert_item(_make_item("b.txt"))
        assert row == 1
        assert model.get_item(1).name == "b.txt"

    def test_insert_prefix_before_objects(self, qtbot):
        model = S3ObjectModel()
        model.set_items([_make_item("a.txt")])
        row = model.insert_item(_make_item("dir/", key="dir/", is_prefix=True))
        assert row == 0
        assert model.get_item(0).is_prefix is True

    def test_remove_item(self, qtbot):
        model = S3ObjectModel()
        model.set_items([_make_item("a.txt"), _make_item("b.txt")])
        result = model.remove_item("a.txt")
        assert result is True
        assert model.rowCount() == 1
        assert model.get_item(0).name == "b.txt"

    def test_remove_item_nonexistent(self, qtbot):
        model = S3ObjectModel()
        model.set_items([_make_item("a.txt")])
        result = model.remove_item("nonexistent.txt")
        assert result is False
        assert model.rowCount() == 1

    def test_remove_items_batch(self, qtbot):
        model = S3ObjectModel()
        model.set_items([
            _make_item("a.txt"),
            _make_item("b.txt"),
            _make_item("c.txt"),
        ])
        count = model.remove_items({"a.txt", "c.txt"})
        assert count == 2
        assert model.rowCount() == 1
        assert model.get_item(0).name == "b.txt"

    def test_update_item(self, qtbot):
        model = S3ObjectModel()
        model.set_items([_make_item("a.txt", size=100)])
        result = model.update_item("a.txt", size=200)
        assert result is True
        assert model.get_item(0).size == 200

    def test_update_item_emits_data_changed(self, qtbot):
        model = S3ObjectModel()
        model.set_items([_make_item("a.txt", size=100)])
        signals = []
        model.dataChanged.connect(lambda tl, br: signals.append((tl.row(), br.row())))
        model.update_item("a.txt", size=200)
        assert len(signals) == 1
        assert signals[0] == (0, 0)

    def test_append_items(self, qtbot):
        model = S3ObjectModel()
        model.set_items([_make_item("a.txt")])
        model.append_items([_make_item("b.txt"), _make_item("c.txt")])
        assert model.rowCount() == 3

    def test_diff_apply_add_and_remove(self, qtbot):
        model = S3ObjectModel()
        model.set_items([_make_item("a.txt"), _make_item("b.txt")])
        new_items = [_make_item("b.txt"), _make_item("c.txt")]
        changed = model.diff_apply(new_items)
        assert changed is True
        assert model.rowCount() == 2
        names = {model.get_item(i).name for i in range(model.rowCount())}
        assert names == {"b.txt", "c.txt"}

    def test_diff_apply_no_change(self, qtbot):
        model = S3ObjectModel()
        items = [_make_item("a.txt", size=100)]
        model.set_items(items)
        changed = model.diff_apply([_make_item("a.txt", size=100)])
        assert changed is False

    def test_diff_apply_update(self, qtbot):
        model = S3ObjectModel()
        model.set_items([_make_item("a.txt", size=100)])
        changed = model.diff_apply([_make_item("a.txt", size=200)])
        assert changed is True
        assert model.get_item(0).size == 200

    def test_clear(self, qtbot):
        model = S3ObjectModel()
        model.set_items([_make_item("a.txt")])
        model.clear()
        assert model.rowCount() == 0

    def test_flags_drag_enabled(self, qtbot):
        model = S3ObjectModel()
        model.set_items([_make_item("a.txt")])
        flags = model.flags(model.index(0, 0))
        assert flags & Qt.ItemFlag.ItemIsDragEnabled

    def test_user_role_returns_item(self, qtbot):
        model = S3ObjectModel()
        model.set_items([_make_item("a.txt", size=42)])
        idx = model.index(0, 0)
        item = model.data(idx, Qt.ItemDataRole.UserRole)
        assert isinstance(item, S3Item)
        assert item.size == 42
