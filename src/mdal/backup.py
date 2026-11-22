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
from dataclasses import dataclass, field
from typing import Any

from mdal import __version__
from mdal import tunables
from mdal.db.repo import SETTING_DEFAULTS, Repo, now_iso

FORMAT = "shiori-backup"
TABLES = {   # table -> primary key (restore replaces rows with the same key)
    "mapping": "md_id", "dismissed_flag": "md_id", "rec_hidden": "media_id", "md_new_hidden": "md_id",
    "rec_feedback": "key", "ranking": "media_id", "chat_message": "id", "friend": "name", "list_edit": "id",
    "series_note": "key",
}
SECRET_SETTINGS = {"mangadex_session", "mal_session", "mangadex_cooldown_until", "mangadex_cooldown_reason"}
IDENTITY_SETTINGS = {"anilist_user_id", "anilist_user_name", "mal_user_name"}  # comes from who is signed in
STATE_SETTINGS = {"rec_status", "new_status", "rank_save"}       # what a job was doing: meaningless elsewhere

LEFT_OUT = SECRET_SETTINGS | IDENTITY_SETTINGS | STATE_SETTINGS


class BackupError(Exception):
    pass


@dataclass
class Restored:
    counts: dict[str, int] = field(default_factory=dict)   # rows put back, per table
    kept: list[str] = field(default_factory=list)          # settings whose backup value wasn't valid: yours stayed


def make_backup(repo: Repo) -> dict[str, Any]:
    tables = {t: [dict(r) for r in repo.conn.execute(f"SELECT * FROM {t}")] for t in TABLES}
    settings = {k: v for k, v in repo.all_settings().items() if k not in LEFT_OUT}
    return {"format": FORMAT, "version": __version__, "exported_at": now_iso(), "tables": tables, "settings": settings}


def _site_copies(table: str, row: dict[str, Any]) -> list[dict[str, Any]]:
    """A Dismissal from a backup older than per-site Dismissals applied to both sites, so it's restored for both."""
    if table == "dismissed_flag" and "site" not in row:
        return [{**row, "site": site} for site in ("anilist", "mal")]
    return [row]


def restore(repo: Repo, data: Any) -> Restored:
    """Put a backup back. Rows replace rows with the same key; nothing else is deleted. A setting whose value isn't
    valid keeps your current one."""
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise BackupError("This isn't a Shiori backup file.")
    counts: dict[str, int] = {}
    offered = data.get("settings")
    offered = {k: v for k, v in offered.items() if k in SETTING_DEFAULTS and k not in LEFT_OUT} \
        if isinstance(offered, dict) else {}
    settings, kept = tunables.restorable(repo, offered)
    with repo.conn:
        for table, rows in (data.get("tables") or {}).items():
            if table not in TABLES or not isinstance(rows, list):
                continue
            columns = {r[1] for r in repo.conn.execute(f"PRAGMA table_info({table})")}
            n = 0
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if TABLES[table] not in row or TABLES[table] not in columns:
                    continue
                try:
                    for copy in _site_copies(table, row):
                        cols = [c for c in copy if c in columns]
                        repo.conn.execute(f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                                          [copy[c] for c in cols])
                    n += 1
                except sqlite3.IntegrityError:
                    continue            # a value this version no longer accepts
            counts[table] = n
        for key, value in settings.items():
            repo.conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (key, json.dumps(value)))
    return Restored(counts, kept)


def to_csv(header: list[str], rows: list[list[Any]]) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(header)
    w.writerows(rows)
    return "﻿" + out.getvalue()      # a BOM so Excel reads the titles as UTF-8
