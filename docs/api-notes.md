# API notes

Verified 2026-10-03 against live docs. AniList: `docs.anilist.co` returns 403 to automated fetches, so its pages were read from the docs' source repo (`github.com/AniList/docs`, branch `master`, `docs/guide/**` and `docs/reference/**`). MangaDex: `api.mangadex.org/docs/` and the OpenAPI spec at `api.mangadex.org/docs/static/api.yaml`.

Legend: **Confirmed** = matches the build prompt. **Corrected** = docs differ from the prompt; the docs win. **New** = not in the prompt. **Unverified** = not documented; must be checked in a live read-only test (story 09).

---

## AniList

Endpoint: `POST https://graphql.anilist.co`, JSON body `{query, variables}`, headers `Authorization: Bearer <token>`, `Content-Type: application/json`, `Accept: application/json`.

### Rate limits
- **Confirmed.** Normally 90 req/min; currently "degraded" at **30 req/min**. Increases are not being granted.
- **Confirmed.** Over the limit → HTTP 429 plus a one-minute timeout; headers `Retry-After` (seconds) and `X-RateLimit-Reset` (Unix time). There is also an unquantified burst limiter.
- **New (third-party report, `github.com/Mitchures/animitchures/pull/15`).** A 429 can arrive as a **Cloudflare HTML page**, not a JSON body. The client must classify by HTTP status, never by parsing JSON first. `Retry-After` may be missing, so fall back to 60 s.
- **New.** GraphQL errors can arrive with HTTP 200. Always inspect `errors`.
- **New (`considerations.md`).** AniList may block IPs that send excessive requests, and may disable the API entirely during outages. In that case the response is an error message, not data, and the client must halt rather than retry.

### Auth
- **Corrected/new.** Create the client at AniList → Settings → Developer → "Create New Application" (name + redirect URL). **Applications cannot be deleted.**
- Authorization-code grant: browser goes to `https://anilist.co/api/v2/oauth/authorize?client_id=…&redirect_uri=…&response_type=code`. Then `POST https://anilist.co/api/v2/oauth/token` with `grant_type=authorization_code`, `client_id`, `client_secret`, `redirect_uri`, `code`. The response has `access_token` (a JWT).
- The redirect URI must match the registered one exactly. Alternative: register `https://anilist.co/api/v2/oauth/pin`, and AniList shows the token for manual copy ("pin" flow).
- **New.** Access tokens last **1 year**. **No refresh tokens.** On expiry the user re-authorises.
- **Unverified.** Whether AniList accepts `http://127.0.0.1:<port>/…` as a redirect URL. If it rejects it, use the pin flow.

### Queries we rely on
- **Confirmed.** `Viewer { id name }` returns the authenticated user. `MediaListCollection` does not infer the user, so `userId` is required.
- **Confirmed.** `MediaListCollection(userId: Int, type: MANGA)` returns the whole list grouped into `lists[].entries[]`. Optional `chunk`/`perChunk` (max 500) with `hasNextChunk`. Limited to the 11,000 most recently updated entries (irrelevant at our scale).
- **New.** Entries can be hidden from status lists and appear **only in custom lists**, and one entry can appear in several lists. Iterate every list and de-duplicate by entry `id`.
- **Confirmed.** `Page(page, perPage) { media(id_in: [Int], type: MANGA) }` and `media(idMal_in: [Int], type: MANGA)` exist. `search: String` and `format_in` exist too.
- **New.** `pageInfo.total` and `lastPage` are inaccurate; use only `hasNextPage`. `page × perPage` ≤ 5000. perPage max is not stated in the fetched text; the community-known cap is 50 (**Unverified**).
- **New.** A `Page` may contain only one data field. Several **aliased `Page` roots** in one document are standard GraphQL, but whether AniList allows them is **Unverified** (read-only test).
- Media fields used: `id idMal type format status chapters volumes countryOfOrigin startDate{year} title{romaji english native} synonyms coverImage{medium} siteUrl isAdult staff`. All exist. `chapters` = "amount of chapters the manga has when complete", null while ongoing.

### Mutations
- **Confirmed.** `SaveMediaListEntry(id: Int, mediaId: Int, progress: Int, status: …, …) : MediaList`. With `id` (list-entry id) it updates that entry. Without it, AniList decides create-vs-update from `mediaId`.
- **Decision.** For progress updates we always pass the entry `id` and `progress` only. That way a stale diff can never *create* an entry. If the entry was deleted meanwhile, the call errors instead.
- **New.** `UpdateMediaListEntries(ids: [Int], progress: Int)` exists, but it sets the *same* value on every id, so it is useless for per-series progress. Batching uses aliased `SaveMediaListEntry` fields instead.
- **Unverified.** The query-complexity cap is not documented anywhere in the docs. The batch size of 10 is unverified. A complexity rejection refuses the whole document before execution, so the safe test is: send the first multi-entry batch; on a complexity error, halve the batch size and retry (story 16).
- **Unverified.** Whether setting `status: COMPLETED` (FR-25) makes AniList fill `completedAt` or change `repeat` by itself. We never send dates; the verify step reports what changed.
- **Unverified, important.** Whether the server changes `status`, `startedAt` or `completedAt` as a side effect of a progress-only save (e.g. PLANNING→CURRENT, or →COMPLETED at the final chapter). The verify step compares status before and after and reports any change. The first live write (one entry, user-approved) is where we learn this.
- **New, user-visible.** On AniList, progress updates normally create **list activity** posts in your feed. A large sync can therefore post many activities. AniList account settings can turn off list-activity creation per status. User decision (2026-10-03): leave activity on.

---

## MangaDex

Base: `https://api.mangadex.org`. Auth host: `https://auth.mangadex.org`.

### Rate limits and rules
- **Confirmed.** About 5 req/s per IP. Over the limit → **429 for all of `*.mangadex.org`**. Persisting → temporary IP ban (**403** on everything). Persisting further → the IP is "silenced" with an undocumented cooldown.
- **Confirmed.** `User-Agent` is mandatory and must not be spoofed. **New:** requests must not carry a `Via` header.
- **Confirmed.** Collections: `limit` max 100 (some feeds 500). `offset + limit` > 10,000 is rejected.
- **New.** `limit` **defaults to 10** on list endpoints, including when `ids[]` is passed. Always send `limit=100` with `ids[]`.
- **New.** Endpoint limits relevant to us: none of `/manga/status`, `/manga/read`, `/manga`, `/chapter` has a per-endpoint limit beyond the global one. Unused: `POST /auth/refresh` 60/h (legacy), `POST /auth/login` 30/h (legacy).

### Auth (personal client)
- **Confirmed.** Create it in mangadex.org/settings → API Clients. Only the owning account can use it. It may sit in "pending" until staff approve it, and cannot be used until then.
- **Confirmed.** `POST https://auth.mangadex.org/realms/mangadex/protocol/openid-connect/token`, `application/x-www-form-urlencoded`:
  - login: `grant_type=password&username&password&client_id&client_secret`
  - refresh: `grant_type=refresh_token&refresh_token&client_id&client_secret`
- **Confirmed.** Access token lasts 15 min. Refresh-token lifetime is not documented. If a refresh fails, log in again with the password grant.

### Endpoints we rely on
| Call | Verified shape | Notes |
|---|---|---|
| `GET /manga/status` | **Confirmed.** `{result, statuses: {<mangaUuid>: reading\|on_hold\|plan_to_read\|dropped\|re_reading\|completed}}`. One call, whole library. | Auth required. |
| `GET /manga?ids[]=…&limit=100` | **Confirmed.** ids[] "limited to 100 per request". | **New:** `contentRating[]` defaults to safe/suggestive/erotica. Always send all four values, or pornographic-rated library entries silently vanish. Use `includes[]=author&includes[]=cover_art` to get author names and cover file names in the same call. |
| Manga attributes | `title` (LocalizedString), `altTitles[]`, `links` (map of string→string), `originalLanguage`, `year` (nullable), `status`, `lastChapter` (nullable), **`chapterNumbersResetOnNewVolume`** (bool, **New**) | `links.al` = AniList id, `links.mal` = MAL id, both **stored as strings**. Parse defensively; non-numeric → ignore. |
| `GET /manga/read?ids[]=…&grouped=true` | **Confirmed.** `{result, data: {<mangaUuid>: [chapterUuid…]}}`. | ids[] cap **not documented**. Use 100 and verify live. Read markers for **deleted** chapters cannot be resolved to numbers (see risks). |
| `GET /chapter?ids[]=…&limit=100` | **Confirmed.** ids[] "limited to 100 per request". `attributes.chapter`: string, nullable, max length 8. | **New:** by default the endpoint hides `pornographic` chapters and **unavailable** ones (`includeUnavailable` defaults to 0). Always send `contentRating[]` × 4 and `includeUnavailable=1`. Leave the tri-state `includeEmptyPages`, `includeFuturePublishAt` and `includeExternalUrl` unset (no filter). |

Language codes: ISO 639-1 plus extensions (`ja`, `ko`, `zh`, `zh-hk`, `ja-ro`, `ko-ro`, `zh-ro`, `en`, `pt-br`…). These map to AniList `countryOfOrigin` (`JP`, `KR`, `CN`, `TW`).

---

## Risks found
1. **Read markers in bulk: retrievable.** `/manga/read?grouped=true` takes many manga per call. But it returns chapter *ids*, not numbers, so every read chapter id must be resolved through `/chapter`. A first sync with about 30k read chapters costs about 300 MangaDex requests. Mitigation: cache chapter id→number in SQLite forever (numbers rarely change), so later syncs resolve only new ids.
2. **Deleted chapters.** If a chapter you read was deleted (not just made unavailable), `/chapter` will not return it and its number is lost. Progress may then come out lower than you actually read. That never lowers AniList, because we never lower, but the series can show as "nothing to do". Report the count of unresolved read ids per series in the diff.
3. **AniList 429 as HTML / missing Retry-After.** Handled by status-code classification and a 60 s fallback.
4. **Server-side side effects of SaveMediaListEntry** (status/date changes) and **activity-feed posts.** Unverified. Detect them in the verify step and raise them before bulk writes.
5. **Complexity cap unknown.** Mitigated by halve-on-complexity-error, and by a read-only aliased-query test before any write.
6. **Cover images.** These are loaded by your browser from `uploads.mangadex.org` (a `*.mangadex.org` host). Load them lazily, only on the review screen, with `referrerpolicy="no-referrer"`. Never prefetch them in bulk.
