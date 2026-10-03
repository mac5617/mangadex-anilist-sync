"""SQLite connection and versioned migrations (`migrations/NNN_name.sql`)."""

from __future__ import annotations

import re
import sqlite3
from importlib import resources
from pathlib import Path

_MIGRATION_NAME = re.compile(r"^(\d{3})_[\w-]+\.sql$")


def connect(path: Path | str) -> sqlite3.Connection:
    """Open the DB and bring the schema up to date.

    `check_same_thread=False`: FastAPI runs sync handlers in a threadpool. The
    sqlite3 module serialises access per connection, and this is a single-user app.
    """
    conn = sqlite3.connect(path, timeout=5, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    migrate(conn)
    return conn


def _migrations() -> list[tuple[int, str]]:
    found = []
    for entry in resources.files("mdal.db.migrations").iterdir():
        match = _MIGRATION_NAME.match(entry.name)
        if match:
            found.append((int(match.group(1)), entry.read_text(encoding="utf-8")))
    return sorted(found)


def schema_version(conn: sqlite3.Connection) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL)")
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return row[0] or 0


def migrate(conn: sqlite3.Connection) -> int:
    current = schema_version(conn)
    for version, sql in _migrations():
        if version > current:
            # Script and version bump in one transaction: a failed migration leaves no trace.
            conn.executescript(f"BEGIN;\n{sql}\nINSERT INTO schema_version(version) VALUES ({version});\nCOMMIT;")
            current = version
    return current
