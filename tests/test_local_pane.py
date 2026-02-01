"""Tests for local pane and breadcrumb bar."""

from s3ui.ui.breadcrumb_bar import BreadcrumbBar
from s3ui.ui.local_pane import LocalPaneWidget, _format_size


class TestBreadcrumbBar:
    def test_creates_without_error(self, qtbot):
        bar = BreadcrumbBar()
        qtbot.addWidget(bar)
        assert bar is not None

    def test_set_path(self, qtbot):
        bar = BreadcrumbBar()
        qtbot.addWidget(bar)
        bar.set_path("/Users/test/Documents")
        assert bar.current_path() == "/Users/test/Documents"

    def test_click_emits_signal(self, qtbot):
        bar = BreadcrumbBar()
        qtbot.addWidget(bar)
        bar.set_path("/a/b/c")
        # Signal should exist
        assert hasattr(bar, "path_clicked")


class TestLocalPaneWidget:
    def test_creates_without_error(self, qtbot, tmp_path):
        pane = LocalPaneWidget()
        qtbot.addWidget(pane)
        assert pane is not None

    def test_navigate_to_directory(self, qtbot, tmp_path):
        pane = LocalPaneWidget()
        qtbot.addWidget(pane)
        # Create a test directory
        test_dir = tmp_path / "test_nav"
        test_dir.mkdir()
        pane.navigate_to(str(test_dir))
        assert pane.current_path() == str(test_dir)

    def test_breadcrumb_reflects_path(self, qtbot, tmp_path):
        pane = LocalPaneWidget()
        qtbot.addWidget(pane)
        test_dir = tmp_path / "bread" / "crumb"
        test_dir.mkdir(parents=True)
        pane.navigate_to(str(test_dir))
        assert pane._breadcrumb.current_path() == str(test_dir)

    def test_back_forward_navigation(self, qtbot, tmp_path):
        pane = LocalPaneWidget()
        qtbot.addWidget(pane)

        dir_a = tmp_path / "dir_a"
        dir_b = tmp_path / "dir_b"
        dir_a.mkdir()
        dir_b.mkdir()

        pane.navigate_to(str(dir_a))
        pane.navigate_to(str(dir_b))
        assert pane.current_path() == str(dir_b)

        pane.go_back()
        assert pane.current_path() == str(dir_a)

        pane.go_forward()
        assert pane.current_path() == str(dir_b)

    def test_go_up(self, qtbot, tmp_path):
        pane = LocalPaneWidget()
        qtbot.addWidget(pane)
        child = tmp_path / "child"
        child.mkdir()
        pane.navigate_to(str(child))
        pane.go_up()
        assert pane.current_path() == str(tmp_path)

    def test_show_hidden_files(self, qtbot, tmp_path):
        pane = LocalPaneWidget()
        qtbot.addWidget(pane)
        pane.set_show_hidden(True)
        pane.set_show_hidden(False)
        # No crash is the test


class TestFormatSize:
    def test_bytes(self):
        assert _format_size(0) == "0 B"
        assert _format_size(512) == "512 B"

    def test_kb(self):
        assert _format_size(1024) == "1.0 KB"
        assert _format_size(1536) == "1.5 KB"

    def test_mb(self):
        assert _format_size(1024 * 1024) == "1.0 MB"

    def test_gb(self):
        assert _format_size(2560 * 1024 * 1024) == "2.5 GB"
