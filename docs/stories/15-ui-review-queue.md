# 15: Match review queue

**Covers:** FR-29, FR-20.

## Context
Uncertain matches wait here. Decisions made here are tier 1 and are never re-matched. Pasting an AniList id costs one validation request.

## Tasks
- `GET /review`: one card per series in `mapping.state='review'` (then, in a second section, `unmatched`). The left side shows the MangaDex title, alt titles (collapsed), year, original language, authors, cover (`https://uploads.mangadex.org/covers/{md_id}/{file}.256.jpg`, `loading="lazy"`, `referrerpolicy="no-referrer"`) and a link. The right side shows up to 5 candidates from `match_candidate` with cover (`coverImage.medium`), romaji/english title, year, format, country, score and reasons list, plus links.
- `POST /review/{md_id}/accept` (al_media_id) → `confirm`.
- `POST /review/{md_id}/manual` (text) → `parse_anilist_ref`. If the id is not in `al_media`, validate it with `media_by_ids` (1 request; must be MANGA), then `confirm`. Errors are shown inline.
- `POST /review/{md_id}/not-on-anilist` → `mark_not_on_anilist`.
- `POST /review/{md_id}/retry` → `retry_matching` (re-matched on the next sync).
- A "Not on AniList" sub-list with an "undo" action (deletes the mapping, so the series is re-matched next sync).
- After any action, the card is swapped out via HTMX. If the current `diffed` run contains this series, recompute that one `sync_item` with `rules.evaluate` (no requests).

## Acceptance criteria
- [x] Accepting a candidate sets `state='confirmed', tier=1`, and the next sync does not re-match it (verified with the pipeline).
- [x] A manual `https://anilist.co/manga/30013/x` that is not cached makes exactly 1 request. An anime id, or a non-existent id, shows an error and changes nothing.
- [x] "Not on AniList" removes the series from the queue on this and future syncs. Undo restores matching.
- [x] A decision made while a `diffed` run exists updates that run's item (e.g. skip "awaiting match review" → write).
- [x] Covers are lazy and no-referrer.

## Tests
`test_web_review.py`.

## Dev notes
- Done 2026-10-03; 306 tests pass in total (15 new).
- The pasted-id validation lives in `matching/pipeline.confirm_manual()` (web code may not call clients or fetchers, §2). Cached media cost nothing; uncached ids make one `media_by_ids` request. Anime ids/URLs and unknown ids give an inline error and change nothing.
- `accept` only takes media that are among the series' stored candidates.
- "Undo" for a not-on-AniList series is the retry action (deletes the mapping; re-matched next sync). The done-card after "Not on AniList" also offers Undo.
- `sync/orchestrator.refresh_item()` recomputes one series' row in the latest run, only when that run is `diffed` (no requests).
- Unmatched series (no candidates) show the paste box, "Not on AniList" and "Retry matching".
- Repo additions: `mapped_series(state)`, `candidate_media(md_id)`.
