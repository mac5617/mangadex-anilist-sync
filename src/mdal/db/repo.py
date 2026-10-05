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
    # MangaDex safety (not user-editable): cooldown after a block/429 limit/dropped connection,
    # and the saved login so restarts do not log in again. The session holds tokens: never render it.
    "mangadex_cooldown_until": None,
    "mangadex_cooldown_reason": None,
    "mangadex_session": None,
    # Cached from AniList `Viewer` when connecting (story 06); not user-editable.
    "anilist_user_id": None,
    "anilist_user_name": None,
    # MyAnimeList: request pacing (user-editable), the saved login (tokens: never render it) and the user name.
    "mal_rpm": 30,
    "mal_session": None,
    "mal_user_name": None,
    # Recommendations: the local model (Ollama), the last refresh, and the model's last picks.
    "ollama_model": "gpt-oss:20b",
    "rec_status": None,      # {state, detail, error, started_at, finished_at}
    "rec_llm": None,         # {model, summary, picks: [{id, reason}], created_at, error}
    # New releases: the embedding model for description matching, the scan's language, its last run and picks.
    "embed_model": "qwen3-embedding:0.6b",
    "new_language": "en",    # only series with chapters in this language
    "new_status": None,      # {state, detail, error, started_at, finished_at, requests}
    "new_llm": None,         # {model, picks: [{md_id, reason}], created_at, error}
    "new_scan_hours": 24,    # scan MangaDex in the background this often; 0 = only when asked
    "new_seen": [],
    "new_digest": None,      # {at, md_ids}: picks from the latest scan not yet seen on New releases          # MangaDex ids of picks already shown on New releases (for the Home digest)
    "mangaupdates_rps": 1,
    # List: series skipped while ranking, the last background save of scores, and when Reading counts as stalled.
    "rank_skipped": [],
    "rank_save": None,       # {state, detail, error, started_at, finished_at, saved}
    "stalled_days": 90,
    "mal_mirror": True,      # list edits (scores, Paused/Dropped, notes) also go to MyAnimeList when connected
    "title_language": "english",   # which AniList title pages show: english (romaji when missing) or romaji
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
        """Columns a query did not ask for (`staff`, `tags`, `description` = None) keep their cached values."""
        with self.conn:
            for row in rows:
                keep = [c for c in ("staff", "tags", "description") if c in row and row[c] is None]
                if "staff" in keep:
                    row = {**row, "staff": "[]"}  # NOT NULL column: insert placeholder, never overwrite
                _upsert(self.conn, "al_media", row, ["media_id"], preserve=keep)

    def set_staff_roles(self, roles: dict[int, list[dict[str, Any]]]) -> None:
        with self.conn:
            self.conn.executemany("UPDATE al_media SET staff_roles=? WHERE media_id=?",
                                  [(json.dumps(v, ensure_ascii=False), k) for k, v in roles.items()])

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
    def create_run(self, state: str = "fetching", target: str = "anilist") -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO sync_run(started_at, state, target) VALUES (?, ?, ?)", (now_iso(), state, target)
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
        column = {"anilist": "req_anilist", "mangadex": "req_mangadex", "mal": "req_mal"}[api]
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

    def latest_run(self, target: str | None = None) -> sqlite3.Row | None:
        if target is None:
            return self.conn.execute("SELECT * FROM sync_run ORDER BY run_id DESC LIMIT 1").fetchone()
        return self.conn.execute("SELECT * FROM sync_run WHERE target=? ORDER BY run_id DESC LIMIT 1", (target,)).fetchone()

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
        """Items with display data: MangaDex title, target title/link/total, current status on the target.

        For MyAnimeList runs the list entry, its total and its publication status come from mal_entry,
        falling back to the AniList match's total while the series is not on the MAL list.
        """
        return self.conn.execute(
            "SELECT i.*, r.target, m.title AS md_title, m.pub_status, m.last_chapter, m.cover_file, "
            "a.romaji, a.english, a.native, a.site_url, "
            "CASE WHEN r.target='mal' THEN COALESCE(me.chapters, CASE WHEN me.mal_id IS NULL THEN a.chapters END) "
            "     ELSE a.chapters END AS al_chapters, "
            "CASE WHEN r.target='mal' THEN COALESCE(me.media_status, CASE WHEN me.mal_id IS NULL THEN a.status END) "
            "     ELSE a.status END AS al_media_status, "
            "CASE WHEN r.target='mal' THEN me.status ELSE e.status END AS al_status, "
            "me.title AS mal_title "
            "FROM sync_item i "
            "JOIN sync_run r ON r.run_id = i.run_id "
            "LEFT JOIN md_manga m ON m.md_id = i.md_id "
            "LEFT JOIN al_media a ON a.media_id = i.al_media_id "
            "LEFT JOIN al_entry e ON e.entry_id = i.al_entry_id AND r.target='anilist' "
            "LEFT JOIN mal_entry me ON me.mal_id = i.mal_id AND r.target='mal' "
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

    # ---- recommendations -------------------------------------------------
    def replace_rec_candidates(self, rows: list[dict[str, Any]]) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM rec_candidate")
            for row in rows:
                _upsert(self.conn, "rec_candidate", row, ["media_id"])

    def rec_candidates(self) -> list[sqlite3.Row]:
        """Candidates joined with their AniList media."""
        return self.conn.execute(
            "SELECT c.sources, c.fetched_at AS rec_fetched_at, m.* FROM rec_candidate c "
            "JOIN al_media m ON m.media_id = c.media_id"
        ).fetchall()

    def hide_rec(self, media_id: int) -> None:
        with self.conn:
            _upsert(self.conn, "rec_hidden", {"media_id": media_id, "hidden_at": now_iso()}, ["media_id"])

    def unhide_rec(self, media_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM rec_hidden WHERE media_id=?", (media_id,))

    def hidden_recs(self) -> set[int]:
        return {r[0] for r in self.conn.execute("SELECT media_id FROM rec_hidden")}

    # ---- new releases -------------------------------------------------------
    def replace_new_releases(self, rows: list[dict[str, Any]], follows: Iterable[str]) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM md_new")
            for row in rows:
                _upsert(self.conn, "md_new", row, ["md_id"])
            self.conn.execute("DELETE FROM md_follow")
            self.conn.executemany("INSERT OR IGNORE INTO md_follow(md_id) VALUES (?)", [(i,) for i in follows])

    def new_releases(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM md_new").fetchall()

    def set_similarity(self, values: dict[str, tuple[float, list[str]]]) -> None:
        with self.conn:
            self.conn.executemany("UPDATE md_new SET similarity=?, similar_to=? WHERE md_id=?",
                                  [(s, json.dumps(t, ensure_ascii=False), k) for k, (s, t) in values.items()])

    def md_follows(self) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT md_id FROM md_follow")}

    def hide_new(self, md_id: str) -> None:
        with self.conn:
            _upsert(self.conn, "md_new_hidden", {"md_id": md_id, "hidden_at": now_iso()}, ["md_id"])

    def unhide_all_new(self) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM md_new_hidden")

    def hidden_new(self) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT md_id FROM md_new_hidden")}

    def descriptions(self, media_ids: Iterable[int]) -> dict[int, str]:
        ids = list(media_ids)
        out: dict[int, str] = {}
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            out.update({r[0]: r[1] for r in self.conn.execute(
                f"SELECT media_id, description FROM al_media WHERE description IS NOT NULL "
                f"AND media_id IN ({','.join('?' * len(chunk))})", chunk)})
        return out

    # ---- embeddings ---------------------------------------------------------
    def embeddings(self, model: str, refs: Iterable[str]) -> dict[str, tuple[str, bytes]]:
        """ref -> (text_hash, vector bytes)."""
        wanted = list(refs)
        out: dict[str, tuple[str, bytes]] = {}
        for start in range(0, len(wanted), 500):
            chunk = wanted[start : start + 500]
            out.update({r[0]: (r[1], r[2]) for r in self.conn.execute(
                f"SELECT ref, text_hash, vector FROM embedding WHERE model=? AND ref IN ({','.join('?' * len(chunk))})",
                [model, *chunk])})
        return out

    def save_embeddings(self, model: str, rows: list[tuple[str, str, bytes]]) -> None:
        """rows: (ref, text_hash, vector bytes)."""
        with self.conn:
            self.conn.executemany(
                "INSERT INTO embedding(ref, model, text_hash, vector) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(ref, model) DO UPDATE SET text_hash=excluded.text_hash, vector=excluded.vector",
                [(ref, model, h, v) for ref, h, v in rows])

    # ---- edits to your AniList list (sync/list_edit.py) --------------------------
    def update_al_entry(self, media_id: int, **fields: Any) -> None:
        allowed = {"score", "status", "notes"}
        if not fields or set(fields) - allowed:
            raise ValueError(f"only {allowed} can be edited")
        with self.conn:
            self.conn.execute(f"UPDATE al_entry SET {', '.join(f'{k}=?' for k in fields)} WHERE media_id=?",
                              [*fields.values(), media_id])

    def log_edit(self, media_id: int, entry_id: int, field: str, old: Any, new: Any, state: str,
                 error: str | None = None, site: str = "anilist") -> None:
        with self.conn:
            self.conn.execute("INSERT INTO list_edit(media_id, entry_id, site, field, old, new, state, error, at) "
                              "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                              (media_id, entry_id, site, field, None if old is None else str(old), str(new), state, error,
                               now_iso()))

    def scored_by_shiori(self) -> set[int]:
        """Series whose AniList score Shiori has set (ranking or quick scoring)."""
        return {r[0] for r in self.conn.execute(
            "SELECT DISTINCT media_id FROM list_edit WHERE site='anilist' AND field='score' AND state='done'")}

    def list_edits(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM list_edit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    # ---- your ranking -------------------------------------------------------------
    def ranking(self) -> list[sqlite3.Row]:
        """Every ranked series, best first within each tier (tiers in order liked, fine, disliked)."""
        return self.conn.execute(
            "SELECT * FROM ranking ORDER BY CASE tier WHEN 'liked' THEN 0 WHEN 'fine' THEN 1 ELSE 2 END, position"
        ).fetchall()

    def set_rank(self, media_id: int, tier: str, position: float) -> None:
        with self.conn:
            _upsert(self.conn, "ranking", {"media_id": media_id, "tier": tier, "position": position,
                                           "ranked_at": now_iso()}, ["media_id"])

    def unrank(self, media_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM ranking WHERE media_id=?", (media_id,))

    def renumber_tier(self, tier: str) -> None:
        """Positions 0, 1, 2... again, so fractions never pile up."""
        with self.conn:
            ids = [r[0] for r in self.conn.execute("SELECT media_id FROM ranking WHERE tier=? ORDER BY position", (tier,))]
            self.conn.executemany("UPDATE ranking SET position=? WHERE media_id=?", [(i, m) for i, m in enumerate(ids)])

    # ---- friends' lists -------------------------------------------------------------
    def save_friend(self, name: str, user_id: int, entries: list[dict[str, Any]]) -> None:
        with self.conn:
            _upsert(self.conn, "friend", {"name": name, "user_id": user_id, "fetched_at": now_iso(),
                                          "entries": json.dumps(entries)}, ["name"])

    def friend(self, name: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM friend WHERE name=?", (name,)).fetchone()

    def friends(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT name, fetched_at FROM friend ORDER BY fetched_at DESC").fetchall()

    # ---- your verdicts ---------------------------------------------------------
    def set_feedback(self, key: str, verdict: str, title: str, source: str, *, genres: list[str] = (),
                     tags: list[str] = (), staff: list[dict[str, Any]] = (), description: str | None = None) -> None:
        with self.conn:
            _upsert(self.conn, "rec_feedback", {
                "key": key, "verdict": verdict, "title": title, "source": source, "created_at": now_iso(),
                "genres": json.dumps(list(genres), ensure_ascii=False), "tags": json.dumps(list(tags), ensure_ascii=False),
                "staff": json.dumps(list(staff), ensure_ascii=False), "description": description}, ["key"])

    def clear_feedback(self, key: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM rec_feedback WHERE key=?", (key,))

    def feedback(self) -> dict[str, sqlite3.Row]:
        """key -> {key, verdict, title, source, created_at}, newest first."""
        return {r["key"]: r for r in self.conn.execute("SELECT * FROM rec_feedback ORDER BY created_at DESC")}

    # ---- lookups for series pages -------------------------------------------------
    def cached(self, key: str, kind: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM lookup_cache WHERE key=? AND kind=?", (key, kind)).fetchone()

    def cached_all(self, kind: str) -> dict[str, Any]:
        """key -> parsed data, for every cached lookup of one kind that found something."""
        return {r[0]: json.loads(r[1]) for r in self.conn.execute(
            "SELECT key, data FROM lookup_cache WHERE kind=? AND data IS NOT NULL", (kind,))}

    def cache(self, key: str, kind: str, data: Any) -> None:
        with self.conn:
            _upsert(self.conn, "lookup_cache", {"key": key, "kind": kind, "fetched_at": now_iso(),
                                                "data": None if data is None else json.dumps(data, ensure_ascii=False)},
                    ["key", "kind"])

    # ---- chat ---------------------------------------------------------------
    def chat_messages(self, limit: int = 200) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM (SELECT * FROM chat_message ORDER BY id DESC LIMIT ?) ORDER BY id", (limit,)).fetchall()

    def add_chat_message(self, role: str, content: str, picks: list[dict[str, Any]] | None = None,
                         notes: list[str] | None = None) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO chat_message(role, content, picks, notes, created_at) VALUES (?, ?, ?, ?, ?)",
                (role, content, json.dumps(picks, ensure_ascii=False) if picks is not None else None,
                 json.dumps(notes, ensure_ascii=False) if notes else None, now_iso()))
        return int(cur.lastrowid or 0)

    def clear_chat(self) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM chat_message")

    # ---- MyAnimeList ------------------------------------------------------
    def replace_mal_entries(self, rows: list[dict[str, Any]]) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM mal_entry")
            for row in rows:
                _upsert(self.conn, "mal_entry", row, ["mal_id"])

    def update_mal_entry(self, mal_id: int, **fields: Any) -> None:
        """Keep the local copy of a MAL entry in step after a list edit (score, status)."""
        if not fields or set(fields) - {"score", "status"}:
            raise ValueError("only score and status can be edited")
        with self.conn:
            self.conn.execute(f"UPDATE mal_entry SET {', '.join(f'{k}=?' for k in fields)} WHERE mal_id=?",
                              [*fields.values(), mal_id])

    def mal_entries(self) -> dict[int, sqlite3.Row]:
        """mal_id -> entry."""
        return {r["mal_id"]: r for r in self.conn.execute("SELECT * FROM mal_entry")}

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
