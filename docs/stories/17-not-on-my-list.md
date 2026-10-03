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
- [ ] The list shows exactly the matched series that are absent from `al_entry`.
- [ ] Add sends one mutation with `mediaId`, `status` and `progress` only, then the row disappears.
- [ ] Adding a media already in `al_entry` is refused with no request.
- [ ] The add appears in history as a one-item run.

## Tests
`test_web_not_listed.py`, `test_add_entry.py`.

## Dev notes
_(fill in after implementation)_
