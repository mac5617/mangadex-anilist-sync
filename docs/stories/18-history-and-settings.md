# 18: Sync history and settings screens

**Covers:** FR-31, FR-32, NFR-16.

## Tasks
- `GET /history`: runs newest first, showing start time, final state, counts by action and write_state, request counts per API, duration and error. `GET /history/{run_id}` reuses the diff table, read-only, with `write_state`, `written_at` and `verify_note` columns.
- `GET/POST /settings`: edit every tunable in architecture §3 with range validation. Show the DB path (read-only) and the auth panels from story 06. A "reset batch size to 10" button. Secrets are never rendered; only "set / not set" is shown for each `.env` key.
- Settings changes apply to the live PacedQueues via `set_interval`.

## Acceptance criteria
- [ ] History lists runs, including one-item add runs, and the drill-down shows verify notes.
- [ ] `anilist_rpm=31` or `mangadex_rps=5` is rejected with a message. Valid values persist and change queue spacing immediately (test via the queue's interval).
- [ ] The settings page HTML contains none of the secret values (test with known fake secrets).
- [ ] The threshold ordering `match_review < match_auto` is enforced.

## Tests
`test_web_history.py`, `test_web_settings.py`.

## Dev notes
_(fill in after implementation)_
