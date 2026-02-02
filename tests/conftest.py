import gc
import os
import sqlite3
from pathlib import Path

import pytest


def pytest_configure(config):
    """Work around PyQt6 SIGABRT crashes in the test suite.

    PyQt6's QThread destructor calls abort() when destroyed in a problematic
    state.  Three independent vectors trigger this:

    1. Python's cyclic GC destroys QThread instances at unpredictable times via
       reference chains (exception → traceback → frame → QThread).  Disabling
       the cyclic collector prevents this; objects are still freed via refcount.

    2. pytest-qt calls QApplication.processEvents() in its setup/call/teardown
       hooks.  Qt's event loop can destroy C++ objects during event dispatch,
       triggering abort() in QThread's destructor.  Replacing the hook-level
       ``_process_events`` with a no-op prevents this while leaving test-level
       event processing (qtbot.waitSignal, etc.) fully functional.

    3. QFileSystemModel.setRootPath() starts a background FSEvents watcher
       thread on macOS.  These threads accumulate across tests and their
       destructors call abort().  An autouse fixture below replaces
       QFileSystemModel with a non-watching subclass.

    Additionally ``sip.setdestroyonexit(False)`` prevents SIP from calling C++
    destructors at interpreter shutdown, and ``os._exit()`` in
    ``pytest_sessionfinish`` skips interpreter cleanup entirely.
    """
    gc.disable()

    try:
        from PyQt6 import sip

        sip.setdestroyonexit(False)
    except (ImportError, AttributeError):
        pass

    try:
        import pytestqt.plugin

        pytestqt.plugin._process_events = lambda: None
    except ImportError:
        pass


def pytest_sessionfinish(session, exitstatus):
    """Force-exit to avoid PyQt6 cleanup crash at interpreter shutdown."""
    os._exit(exitstatus)


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    """Provide a temporary database path for testing."""
    return tmp_path / "test.db"


@pytest.fixture
def tmp_db(tmp_db_path: Path) -> sqlite3.Connection:
    """Provide a temporary SQLite connection for testing."""
    conn = sqlite3.connect(str(tmp_db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def mock_keyring(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Mock the keyring module with a simple dict backend."""
    store: dict[str, str] = {}

    def get_password(service: str, key: str) -> str | None:
        return store.get(f"{service}:{key}")

    def set_password(service: str, key: str, value: str) -> None:
        store[f"{service}:{key}"] = value

    def delete_password(service: str, key: str) -> None:
        store.pop(f"{service}:{key}", None)

    monkeypatch.setattr("keyring.get_password", get_password)
    monkeypatch.setattr("keyring.set_password", set_password)
    monkeypatch.setattr("keyring.delete_password", delete_password)
    return store


@pytest.fixture(autouse=True)
def _no_real_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent tests from writing to the real ~/.s3ui directory."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("s3ui.constants.APP_DIR", tmp_path / ".s3ui")
    monkeypatch.setattr("s3ui.constants.DB_PATH", tmp_path / ".s3ui" / "s3ui.db")
    monkeypatch.setattr("s3ui.constants.LOG_DIR", tmp_path / ".s3ui" / "logs")
    monkeypatch.setattr("s3ui.constants.LOG_FILE", tmp_path / ".s3ui" / "logs" / "s3ui.log")


@pytest.fixture(autouse=True)
def _no_fs_watcher(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent QFileSystemModel from creating file-watcher threads.

    QFileSystemModel.setRootPath() starts a background FSEvents watcher thread
    on macOS.  When Qt objects are destroyed during test cleanup the watcher
    thread's destructor can call abort(), producing SIGABRT.  Replacing
    QFileSystemModel with a subclass whose setRootPath is a no-op prevents the
    thread from ever starting.  Tests that check navigation state
    (current_path, breadcrumb, history) are unaffected.
    """
    from PyQt6.QtCore import QModelIndex
    from PyQt6.QtGui import QFileSystemModel

    class _NoWatchModel(QFileSystemModel):
        def setRootPath(self, path):  # noqa: N802
            return QModelIndex()

    monkeypatch.setattr("s3ui.ui.local_pane.QFileSystemModel", _NoWatchModel)
