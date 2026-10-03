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
- [x] The callback with a wrong or expired `state` returns 400 and makes no HTTP call.
- [x] A successful callback writes the token to `.env` (temp file in tests) and stores the viewer id/name. The token is not rendered in any HTML response.
- [x] Pin-flow submission behaves identically.
- [x] Disconnect removes the key from `.env`.
- [x] The MangaDex check reports the three outcomes correctly (mocked 200 / 401 / 403).
- [x] No token or password appears in logs.

## Tests
`test_auth_routes.py` (TestClient + respx, temp `.env`).

## Dev notes
- Done 2026-10-03; 90 tests pass in total. Smoke-tested against the real `.env`: `/settings` renders, MangaDex shows as configured, and no `.env` secret values appear in the HTML. No live API call was made.
- **New module `mdal/services.py`**, which the story didn't plan for. `Services` holds the settings, `Repo`, both `PacedQueue`s, `AniListClient`, `MangaDexClient` and `AniListOAuth`. There is one instance per process (`Services.from_env()` in `main.py`); tests use the `services` fixture (temp `.env` + temp DB). The clients read the token and credentials through `Services` on every call, so `.env` updates apply without a restart. Story 13 should **extend `Services`** (`request_counter` wiring) rather than create its own singletons.
- `create_app(services)` now requires `Services`. Its lifespan closes the HTTP clients. Use `with TestClient(create_app(services)) as c:` in tests.
- Logging redaction now uses `services.secret_values` (it reflects tokens stored after OAuth).
- The token is written with `dotenv.set_key(..., quote_mode="never")`; other keys are untouched (tested). Disconnect uses `unset_key`.
- `anilist_user_id` / `anilist_user_name` were added to `SETTING_DEFAULTS` (not user-editable; story 18 must hide them from the form).
- OAuth `state` is held in memory (`app.state.oauth_states`), lasts 10 minutes and is single-use. Any bad, missing or expired state → 400, and no HTTP call.
- Pin fallback: when `ANILIST_REDIRECT_URI` is the pin URL, the settings page links an implicit-grant URL (`response_type=token`), which shows the token on AniList's pin page. The paste form is always shown while disconnected.
- A rejected token (AuthError on `Viewer`) is removed from `.env` again. A network or rate-limit failure keeps the token and shows a warning.
- First real templates: `web/templates/base.html`, `settings.html`, `web/static/app.css`. Story 14 extends `base.html`'s nav. HTMX is not vendored yet; that comes in story 14.
