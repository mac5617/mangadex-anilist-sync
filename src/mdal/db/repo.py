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
    # Cached from AniList `Viewer` when connecting (story 06); not user-editable.
    "anilist_user_id": None,
    "anilist_user_name": None,
}


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _upsert(
    conn: sqlite3.Connection, table: str, row: Mapping[str, Any], key: Iterable[str], preserve: Iterable[str] = ()
) -> None:
    """Insert or update. Columns in `preserve` are written on insert but never overwritten."""
    cols = list(row)
    updates = [c for c in cols if c not in set(key) | set(preserve)]
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

    def chapter_cache_state(self) -> dict[str, int]:
        """chapter_id -> missing (0 resolved, 1 missed once, 2 permanently missing)."""
        return {r["chapter_id"]: r["missing"] for r in self.conn.execute("SELECT chapter_id, missing FROM md_chapter")}

    def read_chapters(self, md_id: str) -> list[sqlite3.Row]:
        """Read chapters of one series with their cached number (NULL chapter or missing>0 = unresolved)."""
        return self.conn.execute(
            "SELECT r.chapter_id, c.chapter, c.missing FROM md_read r "
            "LEFT JOIN md_chapter c ON c.chapter_id = r.chapter_id WHERE r.md_id=?",
            (md_id,),
        ).fetchall()

    # ---- AniList ----------------------------------------------------------
    def upsert_media(self, rows: list[dict[str, Any]]) -> None:
        """Rows with `staff=None` (queries that did not ask for staff) keep the cached staff list."""
        with self.conn:
            for row in rows:
                if row.get("staff") is None:
                    _upsert(self.conn, "al_media", {**row, "staff": "[]"}, ["media_id"], preserve=["staff"])
                else:
                    _upsert(self.conn, "al_media", row, ["media_id"])

    def replace_al_entries(self, rows: list[dict[str, Any]]) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM al_entry")
            for row in rows:
                _upsert(self.conn, "al_entry", row, ["entry_id"])

    def media(self, media_ids: Iterable[int]) -> dict[int, sqlite3.Row]:
        ids = list(media_ids)
        if not ids:
            return {}
        rows = self.conn.execute(
            f"SELECT * FROM al_media WHERE media_id IN ({','.join('?' * len(ids))})", ids
        ).fetchall()
        return {r["media_id"]: r for r in rows}

    def media_by_mal(self, mal_ids: Iterable[int]) -> dict[int, list[sqlite3.Row]]:
        ids = list(mal_ids)
        if not ids:
            return {}
        found: dict[int, list[sqlite3.Row]] = {}
        for r in self.conn.execute(
            f"SELECT * FROM al_media WHERE id_mal IN ({','.join('?' * len(ids))}) ORDER BY media_id", ids
        ):
            found.setdefault(r["id_mal"], []).append(r)
        return found

    def add_al_entry(self, row: dict[str, Any]) -> None:
        with self.conn:
            _upsert(self.conn, "al_entry", row, ["entry_id"])

    def al_entries(self) -> dict[int, sqlite3.Row]:
        """media_id -> entry."""
        return {r["media_id"]: r for r in self.conn.execute("SELECT * FROM al_entry")}

    # ---- mapping --------------------------------------------------------
    def get_mapping(self, md_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM mapping WHERE md_id=?", (md_id,)).fetchone()

    def upsert_mapping(self, row: dict[str, Any]) -> None:
        row = {**row, "updated_at": row.get("updated_at") or now_iso()}
        if not isinstance(row.get("reasons", ""), str):
            row["reasons"] = json.dumps(row["reasons"])
        with self.conn:
            _upsert(self.conn, "mapping", row, ["md_id"])

    def mappings(self) -> dict[str, sqlite3.Row]:
        return {r["md_id"]: r for r in self.conn.execute("SELECT * FROM mapping")}

    def save_match(self, row: dict[str, Any], candidates: list[dict[str, Any]]) -> None:
        """Mapping plus its ranked candidates (replacing earlier ones) in one transaction."""
        row = {**row, "updated_at": row.get("updated_at") or now_iso()}
        if not isinstance(row.get("reasons", ""), str):
            row["reasons"] = json.dumps(row["reasons"], ensure_ascii=False)
        with self.conn:
            _upsert(self.conn, "mapping", row, ["md_id"])
            self.conn.execute("DELETE FROM match_candidate WHERE md_id=?", (row["md_id"],))
            for rank, c in enumerate(candidates, start=1):
                reasons = c["reasons"] if isinstance(c["reasons"], str) else json.dumps(c["reasons"], ensure_ascii=False)
                self.conn.execute(
                    "INSERT INTO match_candidate(md_id, al_media_id, score, reasons, rank) VALUES (?, ?, ?, ?, ?)",
                    (row["md_id"], c["al_media_id"], c["score"], reasons, rank),
                )

    def mapped_series(self, state: str) -> list[sqlite3.Row]:
        """Library series in one mapping state, with their MangaDex details (review screen)."""
        return self.conn.execute(
            "SELECT p.*, m.title, m.alt_titles, m.year, m.original_language, m.authors, m.cover_file "
            "FROM mapping p JOIN md_manga m ON m.md_id = p.md_id WHERE p.state=? ORDER BY m.title COLLATE NOCASE",
            (state,),
        ).fetchall()

    def candidate_media(self, md_id: str) -> list[sqlite3.Row]:
        """Ranked candidates joined with their AniList media."""
        return self.conn.execute(
            "SELECT c.score, c.reasons AS candidate_reasons, c.rank, a.* FROM match_candidate c "
            "JOIN al_media a ON a.media_id = c.al_media_id WHERE c.md_id=? ORDER BY c.rank",
            (md_id,),
        ).fetchall()

    def candidates(self, md_id: str) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM match_candidate WHERE md_id=? ORDER BY rank", (md_id,)).fetchall()

    def read_counts(self) -> dict[str, int]:
        """md_id -> number of read chapter markers."""
        return {r[0]: r[1] for r in self.conn.execute("SELECT md_id, COUNT(*) FROM md_read GROUP BY md_id")}

    def delete_mapping(self, md_id: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM match_candidate WHERE md_id=?", (md_id,))
            self.conn.execute("DELETE FROM mapping WHERE md_id=?", (md_id,))

    # ---- dismissed flags --------------------------------------------------
    def dismiss_flag(self, md_id: str, md_progress: int, flag_kind: str, reason: str | None) -> None:
        with self.conn:
            _upsert(self.conn, "dismissed_flag", {"md_id": md_id, "md_progress": md_progress, "flag_kind": flag_kind,
                                                  "reason": reason, "dismissed_at": now_iso()}, ["md_id"])

    def undismiss_flag(self, md_id: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM dismissed_flag WHERE md_id=?", (md_id,))

    def dismissed_flags(self) -> dict[str, sqlite3.Row]:
        return {r["md_id"]: r for r in self.conn.execute("SELECT * FROM dismissed_flag")}

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

    def replace_items(self, run_id: int, rows: list[dict[str, Any]]) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM sync_item WHERE run_id=?", (run_id,))
            for row in rows:
                _upsert(self.conn, "sync_item", row, ["run_id", "md_id"])

    def latest_run(self) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM sync_run ORDER BY run_id DESC LIMIT 1").fetchone()

    def runs(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM sync_run ORDER BY run_id DESC LIMIT ?", (limit,)).fetchall()

    def run_counts(self) -> dict[int, dict[str, int]]:
        """run_id -> counts by action and by write_state (history screen)."""
        counts: dict[int, dict[str, int]] = {}
        for column in ("action", "write_state"):
            for r in self.conn.execute(f"SELECT run_id, {column} AS k, COUNT(*) AS n FROM sync_item GROUP BY run_id, {column}"):
                counts.setdefault(r["run_id"], {})[r["k"]] = r["n"]
        return counts

    def diff_rows(self, run_id: int) -> list[sqlite3.Row]:
        """Items with display data: MangaDex title, AniList title/link/total, current AniList status."""
        return self.conn.execute(
            "SELECT i.*, m.title AS md_title, m.pub_status, m.last_chapter, m.cover_file, "
            "a.romaji, a.english, a.native, a.site_url, a.chapters AS al_chapters, a.status AS al_media_status, "
            "e.status AS al_status "
            "FROM sync_item i "
            "LEFT JOIN md_manga m ON m.md_id = i.md_id "
            "LEFT JOIN al_media a ON a.media_id = i.al_media_id "
            "LEFT JOIN al_entry e ON e.entry_id = i.al_entry_id "
            "WHERE i.run_id=? ORDER BY COALESCE(m.title, i.md_id) COLLATE NOCASE",
            (run_id,),
        ).fetchall()

    def review_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM mapping WHERE state='review'").fetchone()[0]

    def not_on_list(self) -> list[sqlite3.Row]:
        """Library series matched to AniList media that is not on the user's list (story 17)."""
        return self.conn.execute(
            "SELECT p.md_id, p.al_media_id, p.state FROM mapping p "
            "JOIN md_manga m ON m.md_id = p.md_id "
            "WHERE p.state IN ('auto','confirmed') AND p.al_media_id IS NOT NULL "
            "AND p.al_media_id NOT IN (SELECT media_id FROM al_entry) ORDER BY m.title COLLATE NOCASE"
        ).fetchall()

    def items_in_state(self, run_id: int, write_state: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM sync_item WHERE run_id=? AND write_state=? ORDER BY md_id", (run_id, write_state)
        ).fetchall()

    def update_items(self, run_id: int, updates: list[tuple[str, dict[str, Any]]]) -> None:
        """[(md_id, {column: value})] in one transaction (one commit per write batch)."""
        with self.conn:
            for md_id, fields in updates:
                if fields:
                    self.conn.execute(
                        f"UPDATE sync_item SET {', '.join(f'{k}=?' for k in fields)} WHERE run_id=? AND md_id=?",
                        [*fields.values(), run_id, md_id],
                    )

    def items(self, run_id: int) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM sync_item WHERE run_id=? ORDER BY md_id", (run_id,)).fetchall()
