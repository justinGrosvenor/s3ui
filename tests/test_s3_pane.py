"""Tests for S3PaneWidget."""

from unittest.mock import MagicMock

from s3ui.models.s3_objects import S3Item
from s3ui.ui.s3_pane import S3PaneWidget


def _mock_client(items=None):
    """Create a mock S3Client that returns given items."""
    client = MagicMock()
    if items is None:
        items = []
    client.list_objects.return_value = (items, [])
    return client


def _make_items():
    """Create a standard set of test items."""
    return [
        S3Item(name="docs/", key="docs/", is_prefix=True),
        S3Item(name="readme.txt", key="readme.txt", is_prefix=False, size=1024),
        S3Item(name="data.csv", key="data.csv", is_prefix=False, size=2048),
    ]


class TestS3PaneWidget:
    def test_creates_without_error(self, qtbot):
        pane = S3PaneWidget()
        qtbot.addWidget(pane)
        assert pane is not None

    def test_not_connected_state(self, qtbot):
        pane = S3PaneWidget()
        qtbot.addWidget(pane)
        assert pane._connected is False
        # Table is hidden, placeholder visible (check internal visibility flags)
        assert not pane._table.isVisibleTo(pane)
        assert pane._placeholder.isVisibleTo(pane)

    def test_set_client_connected_state(self, qtbot):
        pane = S3PaneWidget()
        qtbot.addWidget(pane)
        pane.set_client(_mock_client())
        assert pane._connected is True
        assert pane._table.isVisibleTo(pane)
        assert not pane._placeholder.isVisibleTo(pane)

    def test_navigate_caches_result(self, qtbot):
        pane = S3PaneWidget()
        qtbot.addWidget(pane)
        client = _mock_client(_make_items())
        pane.set_client(client)
        pane._bucket = "test-bucket"

        # Manually simulate fetch completion
        pane._on_listing_complete("", _make_items(), pane._fetch_id)
        assert pane._model.item_count() == 3

        # Navigate again — should use cache
        client.list_objects.reset_mock()
        pane.navigate_to("", record_history=False)
        # Model should still have items from cache
        assert pane._model.item_count() == 3

    def test_back_forward_navigation(self, qtbot):
        pane = S3PaneWidget()
        qtbot.addWidget(pane)
        pane.set_client(_mock_client())
        pane._bucket = "test-bucket"

        pane._on_listing_complete("", [], pane._fetch_id)
        pane.navigate_to("docs/")
        pane._on_listing_complete("docs/", [], pane._fetch_id)
        pane.navigate_to("docs/sub/")
        pane._on_listing_complete("docs/sub/", [], pane._fetch_id)

        assert pane.current_prefix() == "docs/sub/"

        pane.go_back()
        assert pane.current_prefix() == "docs/"

        pane.go_forward()
        assert pane.current_prefix() == "docs/sub/"


class TestOptimisticMutations:
    def test_notify_upload_complete(self, qtbot):
        pane = S3PaneWidget()
        qtbot.addWidget(pane)
        pane.set_client(_mock_client())
        pane._bucket = "test-bucket"
        pane._on_listing_complete("", _make_items(), pane._fetch_id)
        initial_count = pane._model.item_count()

        pane.notify_upload_complete("new_file.txt", 512)
        assert pane._model.item_count() == initial_count + 1

    def test_notify_delete_complete(self, qtbot):
        pane = S3PaneWidget()
        qtbot.addWidget(pane)
        pane.set_client(_mock_client())
        pane._bucket = "test-bucket"
        pane._on_listing_complete("", _make_items(), pane._fetch_id)
        initial_count = pane._model.item_count()

        pane.notify_delete_complete(["readme.txt"])
        assert pane._model.item_count() == initial_count - 1

    def test_notify_new_folder(self, qtbot):
        pane = S3PaneWidget()
        qtbot.addWidget(pane)
        pane.set_client(_mock_client())
        pane._bucket = "test-bucket"
        pane._on_listing_complete("", _make_items(), pane._fetch_id)
        initial_count = pane._model.item_count()

        pane.notify_new_folder("images/", "images")
        assert pane._model.item_count() == initial_count + 1

    def test_notify_rename_complete(self, qtbot):
        pane = S3PaneWidget()
        qtbot.addWidget(pane)
        pane.set_client(_mock_client())
        pane._bucket = "test-bucket"
        pane._on_listing_complete("", _make_items(), pane._fetch_id)

        pane.notify_rename_complete("readme.txt", "README.md", "README.md")
        # Find the renamed item
        found = False
        for i in range(pane._model.item_count()):
            item = pane._model.get_item(i)
            if item.key == "README.md":
                found = True
                assert item.name == "README.md"
        assert found


class TestFilter:
    def test_filter_by_name(self, qtbot):
        pane = S3PaneWidget()
        qtbot.addWidget(pane)
        pane.set_client(_mock_client())
        pane._bucket = "test-bucket"
        pane._on_listing_complete("", _make_items(), pane._fetch_id)

        pane._filter_bar.setVisible(True)
        pane._filter_bar.setText("readme")
        # Proxy should filter
        assert pane._proxy.rowCount() == 1

    def test_clear_filter_shows_all(self, qtbot):
        pane = S3PaneWidget()
        qtbot.addWidget(pane)
        pane.set_client(_mock_client())
        pane._bucket = "test-bucket"
        pane._on_listing_complete("", _make_items(), pane._fetch_id)

        pane._filter_bar.setVisible(True)
        pane._filter_bar.setText("readme")
        assert pane._proxy.rowCount() == 1

        pane._filter_bar.clear()
        assert pane._proxy.rowCount() == 3
