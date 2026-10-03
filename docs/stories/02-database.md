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
- [ ] Opening a fresh path creates every table. Opening it again is a no-op (version 1).
- [ ] `get_setting('anilist_rpm')` returns 20 on a fresh DB, and `set_setting` persists across connections.
- [ ] Replacing the MangaDex snapshot (`md_manga`, `md_read`) leaves `md_chapter` rows untouched.
- [ ] `mapping.state` rejects values outside the CHECK list.
- [ ] A write committed in one connection is visible after the connection is closed and reopened (simulated crash).

## Tests
`test_db.py` covers each criterion using `tmp_path`.

## Dev notes
_(fill in after implementation)_
