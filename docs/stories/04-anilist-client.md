# 04: AniList client (transport and error model)

**Covers:** NFR-1, NFR-2, NFR-3, NFR-4, NFR-16. Architecture §5 "AniListClient", §10.

## Context
This is the only way the app talks to `graphql.anilist.co`. 429s may arrive as Cloudflare HTML (`api-notes.md`), so classify by status code first. This story must not add any mutation.

## Tasks
- `clients/anilist.py`: `AniListClient(token_provider, queue, transport=None, request_counter=None)`. It owns one `httpx.AsyncClient` (timeout 30 s, User-Agent from §3).
- `async graphql(query, variables) -> dict` implements the steps in §5, including the exception hierarchy: `AniListError` → `AniListRateLimited`, `AniListUnavailable`, `AniListAuthError`, `AniListGraphQLError` → `AniListComplexityError`.
- `request_counter` is a callable invoked once per HTTP attempt; the orchestrator later wires it to `sync_run.req_anilist`.
- A module-level factory `get_anilist_client()` returns a singleton built from settings.
- Architecture guard tests (they live in `tests/test_architecture.py` and grow over time):
  1. Only `mdal/clients/*` imports `httpx`.
  2. Until story 16: no source file outside tests contains `SaveMediaListEntry`.

## Acceptance criteria
- [ ] A 429 with a JSON body and `Retry-After: 30` pauses the queue for 35 s, then retries and returns data.
- [ ] A 429 with an **HTML body** and no headers pauses the queue for 65 s.
- [ ] A 429 with only `X-RateLimit-Reset = now+40` pauses for 45 s.
- [ ] While paused, a second concurrent `graphql()` call does not hit the network until the pause ends.
- [ ] After a 4th consecutive 429 for one request, `AniListRateLimited` is raised, and exactly 4 HTTP attempts were made.
- [ ] A 403 raises `AniListUnavailable` after exactly 1 attempt.
- [ ] HTTP 200 with `errors: [{message: "Max query complexity…"}]` raises `AniListComplexityError`.
- [ ] HTTP 200 with other `errors` raises `AniListGraphQLError` carrying the messages.
- [ ] 401 raises `AniListAuthError`.
- [ ] `X-RateLimit-Remaining: 89` does not shorten spacing (the next call is still ≥ 3 s later at 20 rpm).
- [ ] The token never appears in log output during these scenarios.
- [ ] Both architecture guard tests pass.

## Tests
`test_anilist_client.py` (respx + fake clock); `test_architecture.py`.

## Dev notes
_(fill in after implementation)_
