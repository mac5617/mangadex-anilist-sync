# 11: Matching pipeline (tiers 1–4)

**Covers:** FR-14, FR-15, FR-16, FR-18, FR-19, FR-20. Architecture §7.

## Context
This connects the fetchers (07, 08) and the scoring (10). It is request-minimising by design: bulk validation, list-cache reuse, cached auto mappings.

## Tasks
- `matching/pipeline.py`: `async resolve_all(repo, al_fetch) -> MatchSummary`. Series selection and tier logic exactly as in §7.
- Tier 2/3 bulk: gather the parsed ids across **all** pending series first, make one `media_by_ids` and one `media_by_mal_ids` pass, then decide per series.
- Tier 4: pass 1 searches the primary titles of all remaining series (batched). Pass 2 searches the alt title only for series with no candidate ≥ `match_review`.
- Writes `mapping` (state, tier, confidence, reasons JSON, links_hash) and `match_candidate` (top 5).
- Manual resolution helpers used by story 15:
  - `confirm(md_id, al_media_id)` → state `confirmed`, tier 1;
  - `mark_not_on_anilist(md_id)`;
  - `parse_anilist_ref(text)`: accepts `123`, `https://anilist.co/manga/123/slug`, or `anilist.co/manga/123`, and rejects `/anime/` URLs.

## Acceptance criteria
- [x] A series with `state='confirmed'` and changed links is not re-matched, and no request is made for it.
- [x] A series with `state` in `auto`/`review`/`unmatched` and an unchanged `links_hash` is not re-matched (0 requests). A changed hash triggers re-matching.
- [x] `retry_matching(md_id)` deletes the mapping and candidates, so the next sync re-matches it.
- [x] `links.al` pointing to an id in the user's list → `auto` with 0 AniList requests.
- [x] `links.al` not in the list and absent from the `id_in` response → falls to tier 3/4.
- [x] `links.al` resolving to format NOVEL → `review`. `links.al` and `links.mal` resolving to different media → `review`.
- [x] `links.al: "abc"` → treated as absent.
- [x] Tier 4 auto, review and unmatched outcomes each persist the expected state and candidates.
- [x] 50 tier-4 series with batch 5 make ≤ 20 search requests.
- [x] `parse_anilist_ref` accepts the 3 forms and rejects anime URLs and garbage.

## Tests
`test_pipeline.py` (respx + seeded DB), `test_parse_ref.py`.

## Dev notes
- Done 2026-10-03; 205 tests pass in total (46 new).
- `resolve_all(repo, al_fetch)`: `al_fetch` is any `AniListLookup` (ids, MAL ids, search). `AniListFetch` is the real one, wrapping `fetch/anilist_list.py` at the configured page size and search batch.
- FR-16 "links.al and links.mal resolve to different media" is checked **without an extra request**: the media that `links.al` resolves to carries its own `idMal`, and a different `links.mal` means review. When that media has no `idMal`, it is accepted (no MAL lookup just to double-check).
- Only `auto` and `confirmed` mappings carry `al_media_id`. `review` and `unmatched` leave it NULL and keep their suggestions in `match_candidate`, so later stories can never sync a series nobody approved.
- Tier 2/3 matches store the linked media as a candidate with score 1.0; `mapping.confidence` is 1.0 for them (not title-scored).
- Second search: `md_manga.alt_titles` does not keep languages, so "English first, then ja-ro" is approximated as "the first alt title that is ≥ 80% Latin letters and differs from the primary after normalisation", falling back to the first differing alt.
- `md_chapter_count` for the ONE_SHOT check = max(read markers, numeric `last_chapter`).
- Candidates from both searches are merged by media id (best score kept) before classifying.
- Repo additions: `mappings()`, `save_match()` (mapping + candidates in one transaction), `candidates()`, `read_counts()`.
- `parse_anilist_ref` returns `None` (not an exception) for anything it rejects, including look-alike hosts such as `notanilist.co`.
