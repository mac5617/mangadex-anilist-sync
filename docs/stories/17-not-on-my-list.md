# 17: Not-on-my-list screen and "add to AniList"

**Covers:** FR-30; the "do not create automatically" decision.

## Context
Series that are matched (confirmed or auto) but have no AniList list entry. Adding one is an explicit per-item user action, and it is the only code path that creates entries.

## Tasks
- `GET /not-listed`: rows with the MangaDex title, the AniList title, cover, link, proposed progress (from `rules`, using the latest read data), a status dropdown (Reading, Planning, Paused, Completed, Dropped; default Completed when `rules.completion_default` holds, else Reading) and an "Add" button. Rows for media whose total `chapters` is lower than the proposed progress show a warning and pre-fill progress = chapters.
- `sync/add_entry.py`: `add(md_id, status, progress)`:
  - Guard: if `al_entry` has the media (a stale view), refuse with "already on your list".
  - Otherwise one `SaveMediaListEntry(mediaId, status, progress)`.
  - Insert the returned entry into `al_entry`, and record a one-item `sync_run` (state `done`) for history.
- The first-write guard (FR-9) also applies: if `first_write_done` is false, this counts as the first write, and on success sets it.

## Acceptance criteria
- [x] The list shows exactly the matched series that are absent from `al_entry`.
- [x] Add sends one mutation with `mediaId`, `status` and `progress` only, then the row disappears.
- [x] Adding a media already in `al_entry` is refused with no request.
- [x] The add appears in history as a one-item run.

## Tests
`test_web_not_listed.py`, `test_add_entry.py`.

## Dev notes
- Done 2026-10-03; 357 tests pass in total (17 new).
- The screen is at `/not-listed` (the nav link from story 14 pointed at `/not-on-list` and was renamed).
- `sync/add_entry.py`: `not_listed_rows(repo)` (proposal via `rules.evaluate` with no entry, capped at AniList's total when reads exceed it, with a warning) and `add(repo, client, md_id, status, progress)`.
- The status is inserted as an enum literal only after a whitelist check (`CURRENT`, `PLANNING`, `PAUSED`, `COMPLETED`, `DROPPED`); `mediaId` and `progress` travel as `$m`/`$p` variables. Refusals that make no request: not matched, already on the list, unsupported status, negative progress, progress above AniList's total.
- Each add is a one-item run (state `done`, `req_anilist` = 1) with one `sync_item` (`write_state='done'`), so it shows in history. A refused or failed add leaves a `failed` run with the message. A successful add sets `first_write_done`.
- Adds are refused (409) while a sync holds the orchestrator lock, so a running sync's list snapshot cannot clobber the new entry.
- After an add, `refresh_item` updates the series' row in an open `diffed` run.
- `can_resume` now requires pending or done items in every state, so an add that crashed mid-request never shows a Resume button.
