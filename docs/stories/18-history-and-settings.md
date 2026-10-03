# 18: Sync history and settings screens

**Covers:** FR-31, FR-32, NFR-16.

## Tasks
- `GET /history`: runs newest first, showing start time, final state, counts by action and write_state, request counts per API, duration and error. `GET /history/{run_id}` reuses the diff table, read-only, with `write_state`, `written_at` and `verify_note` columns.
- `GET/POST /settings`: edit every tunable in architecture §3 with range validation. Show the DB path (read-only) and the auth panels from story 06. A "reset batch size to 10" button. Secrets are never rendered; only "set / not set" is shown for each `.env` key.
- Settings changes apply to the live PacedQueues via `set_interval`.

## Acceptance criteria
- [x] History lists runs, including one-item add runs, and the drill-down shows verify notes.
- [x] `anilist_rpm=31` or `mangadex_rps=5` is rejected with a message. Valid values persist and change queue spacing immediately (test via the queue's interval).
- [x] The settings page HTML contains none of the secret values (test with known fake secrets).
- [x] The threshold ordering `match_review < match_auto` is enforced.

## Tests
`test_web_history.py`, `test_web_settings.py`.

## Dev notes
- Done 2026-10-03; 373 tests pass in total (16 new).
- Ranges: `anilist_rpm` 1–30 and `mangadex_rps` 0.2–4 (§3); the others are sanity bounds chosen here: write batch 1–25, search batch 1–10, page size 1–50 (50 confirmed live), `match_auto`/`match_review` 0–1 with review < auto, `match_margin` 0–0.5, `jump_limit` 1–10000. Whole-number fields reject decimals. On any error nothing is saved and the submitted values are shown back.
- Saving re-spaces the live PacedQueues (`set_interval`) immediately.
- `first_write_done` is shown read-only, not editable.
- The `.env` panel shows set / not set per key, never values. The DB path is shown read-only with a pointer to `MDAL_DB_PATH`.
- History: `/history` (newest 200 runs, counts by action and by write_state, requests per API, duration, error or summary) and `/history/{id}` (read-only rows with write state, time, `al_status_before`, and the verify/drop/failure note). One-item add runs show like any other run.
- New repo helper: `run_counts()`.
