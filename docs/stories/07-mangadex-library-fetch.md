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
- [ ] With a mocked library of 250 series and 1,000 read ids, the fetch makes exactly 1 + 3 + 3 + 10 MangaDex data requests (status, manga, read, chapter), plus the token.
- [ ] Re-running with no new reads makes 0 `/chapter` requests.
- [ ] Every `/manga` and `/chapter` request includes all 4 `contentRating[]` values and `limit=100`. Every `/chapter` request includes `includeUnavailable=1`.
- [ ] `links.al: "12345"` is stored, and `links.al: "abc"` is stored verbatim (validation happens in matching).
- [ ] A chapter with `chapter: null` is cached with NULL. A requested id that is not returned is cached with `missing=1`.
- [ ] A `MangaDexBlocked` raised mid-fetch propagates, and DB writes from completed steps are kept.

## Tests
`test_fetch_mangadex.py` (respx fixtures in `tests/fixtures/mangadex/`).

## Dev notes
_(fill in after implementation)_
