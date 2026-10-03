# 05: MangaDex client and token management

**Covers:** NFR-1, NFR-5, NFR-8, FR-12 (helper). Architecture §5 "MangaDexClient", §10.

## Context
Continuing to send requests through 429s escalates to an IP ban, so the client stops, rather than retries, beyond a small cap. Token calls go to `auth.mangadex.org` and share the same queue.

## Tasks
- `clients/mangadex.py`: `MangaDexClient(settings, queue, transport=None, request_counter=None, clock=…)`.
- Token handling per §5. Tokens are held in memory only and never persisted or logged.
- `async get(path, params)` handles 429/403/401 per §5. A 403 from the token endpoint raises `MangaDexAuthError` (bad credentials, or a client not yet approved); a 403 from any other endpoint raises `MangaDexBlocked`.
- `async get_by_ids(path, ids, extra_params, with_limit=True)` chunks into groups of 100 and adds `limit=100` (pass `with_limit=False` for `/manga/read`, which has no `limit` parameter), merging results. Array params are encoded as repeated `ids[]=…`.
- Constants `ALL_CONTENT_RATINGS = ["safe","suggestive","erotica","pornographic"]`.
- Sets the User-Agent on every request, including token requests, and never sets `Via`.

## Acceptance criteria
- [x] The first `get()` performs a password grant (form-encoded, with all 5 fields) and then sends `Authorization: Bearer`.
- [x] At fake time +13 min, the next `get()` performs a `refresh_token` grant first.
- [x] A 401 on a data call triggers one refresh and one retry. If the refresh returns 400, a password grant is attempted.
- [x] A 429 with `X-RateLimit-Retry-After = now+12` pauses the queue ≥ 12 s. A 429 without headers pauses 10 s, then 30 s, then 60 s. The 4th consecutive 429 raises `MangaDexRateLimited`.
- [x] A 403 on `/manga` raises `MangaDexBlocked` after exactly one attempt, with no retry.
- [x] `get_by_ids` with 250 ids makes 3 requests, each with `limit=100` and ≤ 100 `ids[]`.
- [x] Every recorded request (token and data) carries the configured User-Agent and no `Via` header.
- [x] At 3 rps, 5 sequential calls take ≥ 1.33 s of fake time.
- [x] Neither the password nor any token appears in captured logs.

## Tests
`test_mangadex_client.py` (respx + fake clock).

## Dev notes
- Done 2026-10-03; 76 tests pass in total.
- The constructor takes `credentials: Callable[[], MangaDexCredentials]`, so `.env` changes are picked up. Story 13 builds it from `Settings`.
- The 429 counter is **client-wide and consecutive**; any non-429 response resets it. Wait time: `X-RateLimit-Retry-After` (Unix time) → `Retry-After` (seconds) → 10/30/60 s. The 4th consecutive 429 raises `MangaDexRateLimited`.
- Network errors back off 5/15/45 s (3 retries) without pausing the queue.
- Token endpoint: 403 → `MangaDexAuthError` ("is the personal API client approved?"); any other non-200 → `MangaDexAuthError` ("check username…"). A 403 on data endpoints → `MangaDexBlocked`.
- `check_login()` does a password grant only (1 request), for story 06.
- `get_by_ids` merges `data`: lists are concatenated (`/manga`, `/chapter`), dicts are merged (`/manga/read?grouped=true`).
- Tokens live only in memory (`_access`, `_refresh`); nothing is persisted.
- Deviation from the story text: `get_by_ids` gained `with_limit`, because `/manga/read` documents no `limit` parameter and an unknown parameter may be rejected.
