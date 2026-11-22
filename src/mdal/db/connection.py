"""SQLite connection and versioned migrations (`migrations/NNN_name.sql`)."""

from __future__ import annotations

import os
import re
import sqlite3
import threading
from collections.abc import Iterator
from importlib import resources
from pathlib import Path
from typing import Any

_MIGRATION_NAME = re.compile(r"^(\d{3})_[\w-]+\.sql$")
COPY_SUFFIX = ".before-migration"


class MigrationCopyError(Exception):
    """The safety copy couldn't be saved, so the database was left as it was. Shown to you as-is."""


class Result:
    """A finished query: its rows, read while the connection was held, plus what a cursor reports."""

    def __init__(self, cursor: sqlite3.Cursor) -> None:
        self.rows: list[Any] = cursor.fetchall() if cursor.description else []
        self.description = cursor.description
        self.rowcount = cursor.rowcount
        self.lastrowid = cursor.lastrowid
        self._next = 0

    def fetchone(self) -> Any:
        if self._next >= len(self.rows):
            return None
        self._next += 1
        return self.rows[self._next - 1]

    def fetchall(self) -> list[Any]:
        rest, self._next = self.rows[self._next:], len(self.rows)
        return rest

    def __iter__(self) -> Iterator[Any]:
        return iter(self.fetchall())


class LockedConnection:
    """One SQLite connection shared by the event loop (background syncs and scans) and FastAPI's worker threads,
    used by one thread at a time.

    Without this, a page reading while a background job wrote could get another statement's rows, or none
    (a `fetchone()` racing an `execute()`). Each statement runs and is read to the end under the lock, and a
    `with conn:` transaction holds it until it commits, so nothing else lands in the middle of one. The lock
    is re-entrant, so a transaction can run statements; no transaction awaits, so the event loop is never
    held up for longer than a query takes."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = threading.RLock()

    def execute(self, sql: str, params: Any = ()) -> Result:
        with self._lock:
            return Result(self._conn.execute(sql, params))

    def executemany(self, sql: str, rows: Any) -> Result:
        with self._lock:
            return Result(self._conn.executemany(sql, rows))

    def executescript(self, script: str) -> Result:
        with self._lock:
            return Result(self._conn.executescript(script))

    def commit(self) -> None:
        with self._lock:
            self._conn.commit()

    def rollback(self) -> None:
        with self._lock:
            self._conn.rollback()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> LockedConnection:
        self._lock.acquire()
        try:
            self._conn.__enter__()
        except BaseException:
            self._lock.release()
            raise
        return self

    def __exit__(self, *exc: Any) -> bool | None:
        try:
            return self._conn.__exit__(*exc)
        finally:
            self._lock.release()

    def __getattr__(self, name: str) -> Any:     # row_factory, in_transaction, total_changes...
        return getattr(self._conn, name)


def connect(path: Path | str) -> LockedConnection:
    """Open the DB and bring the schema up to date.

    `check_same_thread=False`: FastAPI runs sync handlers in a threadpool while background jobs run on the event
    loop; LockedConnection makes sure only one of them uses the connection at a time.
    """
    conn = sqlite3.connect(path, timeout=5, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        _copy_before_migrating(conn, path)
    except MigrationCopyError:
        conn.close()
        raise
    migrate(conn)
    return LockedConnection(conn)


def _copy_before_migrating(conn: sqlite3.Connection, path: Path | str) -> None:
    """Before a migration changes an existing database, keep one consistent copy beside it (the latest only).
    It holds the same sign-in data as the database itself."""
    current = schema_version(conn)
    if current == 0 or str(path) == ":memory:" or not any(version > current for version, _ in _migrations()):
        return
    target = Path(f"{path}{COPY_SUFFIX}")
    partial = Path(f"{target}.partial")
    try:
        partial.unlink(missing_ok=True)
        copy = sqlite3.connect(partial)
        try:
            conn.backup(copy)
        finally:
            copy.close()
        os.replace(partial, target)
    except (OSError, sqlite3.Error) as exc:
        partial.unlink(missing_ok=True)
        raise MigrationCopyError(
            f"Shiori couldn't save a copy of your database before updating it, so nothing was changed: {exc}"
        ) from exc


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
