import sqlite3
from pathlib import Path

import pytest


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


@pytest.fixture
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
