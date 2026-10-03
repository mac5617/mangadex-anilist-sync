# 02: SQLite schema and repository

**Covers:** schema for FR-6, FR-11, FR-19, FR-20, FR-31. Architecture §4.

## Context
All state lives here: caches, mappings, runs. Resumability (FR-6) depends on writes committing immediately.

## Tasks
- `db/connection.py`: open with `journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout=5000`. Apply migrations from `db/migrations/NNN_*.sql` in order, tracked in `schema_version`.
- `001_init.sql` exactly as in §4.
- `db/repo.py`: small typed functions, grouped by table (settings get/set with defaults from §3; md_* upserts and snapshot replacement; mapping CRUD; run and item CRUD). Each public write function commits its own transaction unless it is given an explicit transaction context.
- Settings defaults are applied lazily: `get_setting(key)` returns the §3 default when no row exists.

## Acceptance criteria
- [x] Opening a fresh path creates every table. Opening it again is a no-op (version 1).
- [x] `get_setting('anilist_rpm')` returns 20 on a fresh DB, and `set_setting` persists across connections.
- [x] Replacing the MangaDex snapshot (`md_manga`, `md_read`) leaves `md_chapter` rows untouched.
- [x] `mapping.state` rejects values outside the CHECK list.
- [x] A write committed in one connection is visible after the connection is closed and reopened (simulated crash).

## Tests
`test_db.py` covers each criterion using `tmp_path`, plus WAL mode, unknown setting keys, the `set_status`-only-COMPLETED constraint, and `delete_mapping` removing candidates.

## Dev notes
- Done 2026-10-03; 32 tests pass in total.
- `schema_version` is created by `connection.schema_version()` (code), not by `001_init.sql`, so the migrator can read it before any migration exists. Each migration runs in one `BEGIN…COMMIT` script together with its version bump.
- Added beyond §4:
  - an index `al_media(id_mal)` (for tier-3 lookups);
  - a FK `sync_item.run_id → sync_run`;
  - a CHECK on `flag_kind`.
- `Repo(conn)` is a thin class. `SETTING_DEFAULTS` is the single source of tunable defaults, and unknown keys raise `KeyError`. Values are stored as JSON.
- `Repo.upsert_mapping` accepts `reasons` as a list (JSON-encoded automatically) or a string.
- `Repo.add_request(run_id, 'anilist'|'mangadex')` exists for the clients' `request_counter` (story 13).
- **For later stories:** add repo functions next to their table group. Use `with self.conn:` for every write so it commits immediately (resumability, FR-6).
