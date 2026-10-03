# 07: MangaDex library fetch and chapter-number cache

**Covers:** FR-10, FR-11, FR-12, FR-26 (data). Architecture §6, §11.

## Context
This is the biggest consumer of MangaDex requests. The permanent `md_chapter` cache is what keeps later syncs cheap.

## Tasks
- `fetch/mangadex_library.py`: `async fetch_library(client, repo) -> LibrarySummary`:
  1. Call `/manga/status`.
  2. Call `/manga` for the ids via `get_by_ids`, with all 4 content ratings and `includes[]=author&includes[]=cover_art`. Map the attributes into `md_manga`; `links_hash` = sha1 of the sorted JSON. Author names come from `relationships[type=author].attributes.name`, the cover file from `relationships[type=cover_art].attributes.fileName`.
  3. Call `/manga/read?grouped=true` for the ids in groups of 100, to fill `md_read`. Manga that are absent from the response have no reads.
  4. Compute the unknown chapter ids, call `/chapter` via `get_by_ids` with all 4 ratings and `includeUnavailable=1`, and upsert into `md_chapter`. Requested-but-missing ids get `missing=1`. Ids with `missing=1` and `fetched_at` older than the current run are retried once; after that they stay missing.
- Return counts: series, read ids, new chapter ids resolved, missing ids, requests made.

## Acceptance criteria
- [x] With a mocked library of 250 series and 1,000 read ids, the fetch makes exactly 1 + 3 + 3 + 10 MangaDex data requests (status, manga, read, chapter), plus the token.
- [x] Re-running with no new reads makes 0 `/chapter` requests.
- [x] Every `/manga` and `/chapter` request includes all 4 `contentRating[]` values and `limit=100`. Every `/chapter` request includes `includeUnavailable=1`.
- [x] `links.al: "12345"` is stored, and `links.al: "abc"` is stored verbatim (validation happens in matching).
- [x] A chapter with `chapter: null` is cached with NULL. A requested id that is not returned is cached with `missing=1`.
- [x] A `MangaDexBlocked` raised mid-fetch propagates, and DB writes from completed steps are kept.

## Tests
`test_fetch_mangadex.py` (respx fixtures in `tests/fixtures/mangadex/`).

## Dev notes
- Done 2026-10-03; 100 tests pass in total.
- `fetch_library(client, repo, progress=cb)` returns `LibrarySummary` (series, read_ids, chapters_resolved, chapters_missing). The `progress` callback receives human-readable phase text, for `sync_run.phase_detail` (story 13).
- `md_chapter.missing` is a small counter: **0** resolved, **1** missed once (retried next sync), **2** permanently missing (never requested again). An id that reappears is set back to 0.
- `/manga/read` is called directly per chunk of 100, not via `get_by_ids`. The spec allows an ungrouped array response; that is accepted only for a 1-id chunk, otherwise it raises. `grouped=true`, no `limit`.
- `includes[]` also requests `artist` (the story said author only). Artist names go into `authors` too, because AniList staff lists both story and art credits. It costs no extra request.
- Title choice: the primary title is `en` → `ja-ro` → `ko-ro` → `zh-ro` → first value. Every other title and alt title is de-duplicated into `alt_titles`.
- `links` can come back as `[]` from MangaDex when empty; it is normalised to `{}`.
- `md_manga`/`md_read` are replaced in one transaction **before** `/chapter` resolution, so a 403 during chapter resolution keeps the fresh snapshot (tested).
- New repo helpers: `chapter_cache_state()` and `read_chapters(md_id)` (joins md_read with md_chapter; story 12/13 use it as rules input).
- `tests/factories.py::FakeMangaDex` is a stateful respx fake of all 5 endpoints. It honours `contentRating[]`, `limit` and `includeUnavailable` defaults, so a forgotten override makes tests fail. Reuse it for the story 13 budget test.
