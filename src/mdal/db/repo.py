"""Typed access to every table. Each public write commits its own transaction.

Later stories add functions here; keep them grouped by table.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

# Tunables (architecture §3). Values are stored as JSON text.
SETTING_DEFAULTS: dict[str, Any] = {
    "anilist_rpm": 20,
    "anilist_write_batch": 10,
    "anilist_search_batch": 5,
    "anilist_page_size": 50,
    "mangadex_rps": 3,
    "match_auto": 0.92,
    "match_review": 0.60,
    "match_margin": 0.05,
    "jump_limit": 200,
    "first_write_done": False,
}


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _upsert(conn: sqlite3.Connection, table: str, row: Mapping[str, Any], key: Iterable[str]) -> None:
    cols = list(row)
    updates = [c for c in cols if c not in set(key)]
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
        f"ON CONFLICT({', '.join(key)}) DO "
        + (f"UPDATE SET {', '.join(f'{c}=excluded.{c}' for c in updates)}" if updates else "NOTHING")
    )
    conn.execute(sql, [row[c] for c in cols])


class Repo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ---- settings -------------------------------------------------------
    def get_setting(self, key: str) -> Any:
        if key not in SETTING_DEFAULTS:
            raise KeyError(key)
        row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else SETTING_DEFAULTS[key]

    def set_setting(self, key: str, value: Any) -> None:
        if key not in SETTING_DEFAULTS:
            raise KeyError(key)
        with self.conn:
            _upsert(self.conn, "settings", {"key": key, "value": json.dumps(value)}, ["key"])

    def all_settings(self) -> dict[str, Any]:
        return {key: self.get_setting(key) for key in SETTING_DEFAULTS}

    # ---- MangaDex snapshot ---------------------------------------------
    def replace_md_snapshot(self, manga: list[dict[str, Any]], reads: Mapping[str, Iterable[str]]) -> None:
        """Replace md_manga and md_read in one transaction. md_chapter (cache) is untouched."""
        with self.conn:
            self.conn.execute("DELETE FROM md_read")
            self.conn.execute("DELETE FROM md_manga")
            for row in manga:
                _upsert(self.conn, "md_manga", row, ["md_id"])
            self.conn.executemany(
                "INSERT OR IGNORE INTO md_read(md_id, chapter_id) VALUES (?, ?)",
                [(md_id, ch) for md_id, chapters in reads.items() for ch in chapters],
            )

    def md_manga(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM md_manga ORDER BY title").fetchall()

    def upsert_chapters(self, rows: list[dict[str, Any]]) -> None:
        with self.conn:
            for row in rows:
                _upsert(self.conn, "md_chapter", row, ["chapter_id"])

    def chapter_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM md_chapter").fetchone()[0]

    # ---- mapping --------------------------------------------------------
    def get_mapping(self, md_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM mapping WHERE md_id=?", (md_id,)).fetchone()

    def upsert_mapping(self, row: dict[str, Any]) -> None:
        row = {**row, "updated_at": row.get("updated_at") or now_iso()}
        if not isinstance(row.get("reasons", ""), str):
            row["reasons"] = json.dumps(row["reasons"])
        with self.conn:
            _upsert(self.conn, "mapping", row, ["md_id"])

    def delete_mapping(self, md_id: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM match_candidate WHERE md_id=?", (md_id,))
            self.conn.execute("DELETE FROM mapping WHERE md_id=?", (md_id,))

    # ---- sync runs ------------------------------------------------------
    def create_run(self, state: str = "fetching") -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO sync_run(started_at, state) VALUES (?, ?)", (now_iso(), state)
            )
        return int(cur.lastrowid)

    def update_run(self, run_id: int, **fields: Any) -> None:
        if not fields:
            return
        with self.conn:
            self.conn.execute(
                f"UPDATE sync_run SET {', '.join(f'{k}=?' for k in fields)} WHERE run_id=?",
                [*fields.values(), run_id],
            )

    def add_request(self, run_id: int, api: str, n: int = 1) -> None:
        column = {"anilist": "req_anilist", "mangadex": "req_mangadex"}[api]
        with self.conn:
            self.conn.execute(f"UPDATE sync_run SET {column}={column}+? WHERE run_id=?", (n, run_id))

    def get_run(self, run_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM sync_run WHERE run_id=?", (run_id,)).fetchone()

    def upsert_item(self, row: dict[str, Any]) -> None:
        with self.conn:
            _upsert(self.conn, "sync_item", row, ["run_id", "md_id"])

    def items(self, run_id: int) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM sync_item WHERE run_id=? ORDER BY md_id", (run_id,)).fetchall()
