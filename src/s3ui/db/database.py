import logging
import sqlite3
import threading
from pathlib import Path

from s3ui.constants import DB_PATH

logger = logging.getLogger("s3ui.db")

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


class Database:
    """Thread-safe SQLite database with WAL mode and migration support."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = str(db_path or DB_PATH)
        self._local = threading.local()
        self._write_lock = threading.Lock()

        # Ensure directory exists
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

        # Initialize on the creating thread and run migrations
        conn = self._get_conn()
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        self._run_migrations()
        logger.info("Database initialized at %s", self._db_path)

    def _get_conn(self) -> sqlite3.Connection:
        """Get a thread-local connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self._db_path)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA foreign_keys=ON")
        return self._local.conn

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Execute a SQL statement with write serialization."""
        conn = self._get_conn()
        if sql.lstrip().upper().startswith("SELECT"):
            return conn.execute(sql, params)
        with self._write_lock:
            cursor = conn.execute(sql, params)
            conn.commit()
            return cursor

    def executemany(self, sql: str, params_list: list[tuple]) -> sqlite3.Cursor:
        """Execute a SQL statement with multiple parameter sets."""
        conn = self._get_conn()
        with self._write_lock:
            cursor = conn.executemany(sql, params_list)
            conn.commit()
            return cursor

    def executescript(self, sql: str) -> None:
        """Execute a SQL script (multiple statements)."""
        conn = self._get_conn()
        with self._write_lock:
            conn.executescript(sql)

    def fetchone(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        """Execute a SELECT and return one row."""
        return self._get_conn().execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        """Execute a SELECT and return all rows."""
        return self._get_conn().execute(sql, params).fetchall()

    def _run_migrations(self) -> None:
        """Apply pending migrations in order."""
        conn = self._get_conn()

        # Ensure schema_version table exists (bootstrap)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
        )
        conn.commit()

        current = self._get_schema_version()

        migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        for migration_file in migration_files:
            version = int(migration_file.stem.split("_")[0])
            if version > current:
                logger.info("Applying migration %03d: %s", version, migration_file.name)
                sql = migration_file.read_text()
                with self._write_lock:
                    conn.executescript(sql)
                    conn.execute(
                        "INSERT OR REPLACE INTO schema_version (version) VALUES (?)",
                        (version,),
                    )
                    conn.commit()

    def _get_schema_version(self) -> int:
        """Get the current schema version."""
        row = self._get_conn().execute(
            "SELECT MAX(version) as v FROM schema_version"
        ).fetchone()
        return row["v"] if row and row["v"] is not None else 0

    def close(self) -> None:
        """Close the thread-local connection."""
        if hasattr(self._local, "conn") and self._local.conn is not None:
            self._local.conn.close()
            self._local.conn = None


# Preferences helpers

def get_pref(db: Database, key: str, default: str | None = None) -> str | None:
    """Get a preference value by key."""
    row = db.fetchone("SELECT value FROM preferences WHERE key = ?", (key,))
    return row["value"] if row else default


def set_pref(db: Database, key: str, value: str) -> None:
    """Set a preference value."""
    db.execute(
        "INSERT OR REPLACE INTO preferences (key, value) VALUES (?, ?)",
        (key, value),
    )


def get_bool_pref(db: Database, key: str, default: bool = False) -> bool:
    """Get a boolean preference."""
    val = get_pref(db, key)
    if val is None:
        return default
    return val.lower() in ("true", "1", "yes")


def get_int_pref(db: Database, key: str, default: int = 0) -> int:
    """Get an integer preference."""
    val = get_pref(db, key)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default
