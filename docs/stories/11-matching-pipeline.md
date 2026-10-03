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
- [ ] A series with `state='confirmed'` and changed links is not re-matched, and no request is made for it.
- [ ] A series with `state` in `auto`/`review`/`unmatched` and an unchanged `links_hash` is not re-matched (0 requests). A changed hash triggers re-matching.
- [ ] `retry_matching(md_id)` deletes the mapping and candidates, so the next sync re-matches it.
- [ ] `links.al` pointing to an id in the user's list → `auto` with 0 AniList requests.
- [ ] `links.al` not in the list and absent from the `id_in` response → falls to tier 3/4.
- [ ] `links.al` resolving to format NOVEL → `review`. `links.al` and `links.mal` resolving to different media → `review`.
- [ ] `links.al: "abc"` → treated as absent.
- [ ] Tier 4 auto, review and unmatched outcomes each persist the expected state and candidates.
- [ ] 50 tier-4 series with batch 5 make ≤ 20 search requests.
- [ ] `parse_anilist_ref` accepts the 3 forms and rejects anime URLs and garbage.

## Tests
`test_pipeline.py` (respx + seeded DB), `test_parse_ref.py`.

## Dev notes
_(fill in after implementation)_
