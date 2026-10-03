# 08: AniList list fetch, bulk lookups, batched search (read-only)

**Covers:** FR-13, FR-15 (lookup half), FR-17 (candidate data). Architecture §6.

## Context
Everything here is a read. The list fetch is the backbone of the never-lower rule, so de-duplication across custom lists must be exact.

## Tasks
- `fetch/anilist_list.py`, with GraphQL documents as module constants (one fragment `MEDIA_FIELDS` reused everywhere):
  - `viewer()`;
  - `fetch_list(user_id)`: one `MediaListCollection(userId, type: MANGA)` with `lists{name isCustomList entries{id status progress media{…MEDIA_FIELDS}}}`. Flatten all lists, de-duplicate by entry id, and replace the `al_entry` snapshot. Upsert `al_media`.
  - `media_by_ids(ids)` and `media_by_mal_ids(ids)`: skip ids already in `al_media` (for mal: rows with a matching `id_mal`), then `Page(page:n, perPage:50){pageInfo{hasNextPage} media(id_in|idMal_in:…, type: MANGA){…}}`. Note: `type: MANGA` filtering means a non-MANGA id simply does not come back; treat absent ids as invalid.
  - `search_batch(titles: list[str]) -> list[list[Media]]`: one document, aliases `s0…sN`, each `Page(perPage:5){media(search:$qN, type:MANGA){…MEDIA_FIELDS staff(perPage:3){nodes{name{full native}}}}}`. Chunked by `anilist_search_batch`.
- `MEDIA_FIELDS = id idMal type format status chapters countryOfOrigin startDate{year} title{romaji english native} synonyms coverImage{medium} siteUrl`

## Acceptance criteria
- [ ] A collection where entry 7 appears in "Reading" and in a custom list yields one `al_entry` row. An entry present only in a custom list (hidden from status lists) is included.
- [ ] `fetch_list` makes exactly 1 request.
- [ ] `media_by_ids([ids already in al_media])` makes 0 requests. 120 unknown ids make 3 requests.
- [ ] `search_batch` with 12 titles and batch 5 makes 3 requests. Results are returned in input order, mapped from the aliases.
- [ ] Query strings are passed as GraphQL variables, never interpolated (a title containing `"` and `}` is sent intact in variables).
- [ ] The architecture guard (no `SaveMediaListEntry`) still passes.

## Tests
`test_fetch_anilist.py`.

## Dev notes
_(fill in after implementation)_
