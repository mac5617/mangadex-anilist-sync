"""Backup and export: what only Shiori knows, as JSON to keep and restore; your list and ratings as CSV.

The backup holds your decisions and work: matches, dismissed flags, ratings, your ranking, hidden series,
Ask conversations, friends compared, list edits, notes kept in Shiori and settings. It never holds logins or
tokens, and leaves out what AniList and MangaDex give back on the next sync (lists, chapters, series details,
caches).
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from typing import Any

from mdal import __version__
from mdal.db.repo import SETTING_DEFAULTS, Repo, now_iso

FORMAT = "shiori-backup"
TABLES = {   # table -> primary key (restore replaces rows with the same key)
    "mapping": "md_id", "dismissed_flag": "md_id", "rec_hidden": "media_id", "md_new_hidden": "md_id",
    "rec_feedback": "key", "ranking": "media_id", "chat_message": "id", "friend": "name", "list_edit": "id",
    "series_note": "key",
}
SECRET_SETTINGS = {"mangadex_session", "mal_session", "mangadex_cooldown_until", "mangadex_cooldown_reason"}
STATE_SETTINGS = {"rec_status", "new_status", "rank_save"}       # what a job was doing: meaningless elsewhere


class BackupError(Exception):
    pass


def make_backup(repo: Repo) -> dict[str, Any]:
    tables = {t: [dict(r) for r in repo.conn.execute(f"SELECT * FROM {t}")] for t in TABLES}
    settings = {k: v for k, v in repo.all_settings().items() if k not in SECRET_SETTINGS | STATE_SETTINGS}
    return {"format": FORMAT, "version": __version__, "exported_at": now_iso(), "tables": tables, "settings": settings}


def restore(repo: Repo, data: Any) -> dict[str, int]:
    """Put a backup back. Rows replace rows with the same key; nothing else is deleted. Returns rows per table."""
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise BackupError("This isn't a Shiori backup file.")
    counts: dict[str, int] = {}
    with repo.conn:
        for table, rows in (data.get("tables") or {}).items():
            if table not in TABLES or not isinstance(rows, list):
                continue
            columns = {r[1] for r in repo.conn.execute(f"PRAGMA table_info({table})")}
            n = 0
            for row in rows:
                if not isinstance(row, dict):
                    continue
                cols = [c for c in row if c in columns]
                if TABLES[table] not in cols:
                    continue
                try:
                    repo.conn.execute(f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                                      [row[c] for c in cols])
                    n += 1
                except sqlite3.IntegrityError:
                    continue            # a value this version no longer accepts
            counts[table] = n
        for key, value in (data.get("settings") or {}).items():
            if key in SETTING_DEFAULTS and key not in SECRET_SETTINGS | STATE_SETTINGS:
                repo.conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (key, json.dumps(value)))
    return counts


def to_csv(header: list[str], rows: list[list[Any]]) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(header)
    w.writerows(rows)
    return "﻿" + out.getvalue()      # a BOM so Excel reads the titles as UTF-8
