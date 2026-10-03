# 06: Auth setup (AniList OAuth, MangaDex credential check)

**Covers:** NFR-9, NFR-12, FR-27 (auth status), FR-32 (connect). Architecture §3, §5; `api-notes.md` Auth.

## Context
AniList tokens last 1 year, with no refresh. The app obtains one through the authorization-code grant and stores it in `.env`. If AniList rejects the `http://127.0.0.1` redirect (unverified), fall back to the pin flow: the user pastes the token into a form.

## Tasks
- `clients/anilist_oauth.py`: `authorize_url(state)` and `exchange_code(code)`. The token POST goes to `anilist.co`, not to GraphQL, but uses the AniList PacedQueue anyway, to be conservative.
- `web/routes/auth.py`:
  - `GET /auth/anilist/start`: generate a random `state`, keep it in memory with a 10-min expiry, and redirect.
  - `GET /auth/anilist/callback?code&state`: verify `state`, exchange the code, write `ANILIST_ACCESS_TOKEN` to `.env` via `dotenv.set_key`, reload settings, run `Viewer { id name }` (1 request), and store `anilist_user_id`/`anilist_user_name` in the settings table.
  - `POST /auth/anilist/token` (pin fallback): same storage and Viewer check.
  - `POST /auth/anilist/disconnect`: remove the token from `.env` and clear the cached user.
  - `POST /auth/mangadex/check`: perform a token grant only (1 request) and report OK, bad credentials, or "client not approved / 403".
- A minimal settings page section shows the connection status of both services (no secrets).

## Acceptance criteria
- [ ] The callback with a wrong or expired `state` returns 400 and makes no HTTP call.
- [ ] A successful callback writes the token to `.env` (temp file in tests) and stores the viewer id/name. The token is not rendered in any HTML response.
- [ ] Pin-flow submission behaves identically.
- [ ] Disconnect removes the key from `.env`.
- [ ] The MangaDex check reports the three outcomes correctly (mocked 200 / 401 / 403).
- [ ] No token or password appears in logs.

## Tests
`test_auth_routes.py` (TestClient + respx, temp `.env`).

## Dev notes
_(fill in after implementation)_
