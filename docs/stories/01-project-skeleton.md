# 01: Project skeleton, config, logging redaction, git

**Covers:** NFR-9, NFR-10, NFR-11, NFR-13 (test infra), NFR-15. Architecture §1–3.

## Context
This is the first commit, so secrets must be protected before anything else exists. `uv`, Python 3.12 and git must already be installed (README prerequisites).

## Tasks
- `git init`. The first commit contains `.gitignore`, with `.env`, `*.db`, `.venv/` and `__pycache__/` in it from the start.
- `uv init --package`, project name `mangadex-anilist-sync`, package `mdal` under `src/`. Add the dependencies from §1.
- `.env.example` with every key from §3, as empty placeholders plus comments.
- Move the user's existing `DEV/.env` to `mangadex-anilist-sync/.env` **after** `.gitignore` exists. Do not read or print its values; only check key names.
- `config.py`: `Settings` (pydantic-settings, `.env`). `db_path` defaults to `%LOCALAPPDATA%\mangadex-anilist-sync\sync.db` (on non-Windows, `~/.local/share/...`). The directory is created on start.
- `logsetup.py`: `RedactingFilter` masks the values of every secret setting, plus any `Bearer …`, `access_token=…`, `refresh_token=…`, `password=…` and `client_secret=…` patterns.
- `main.py`: runs uvicorn with `host="127.0.0.1"` (a constant, not a setting) and `port=settings.port` (default 8765). The FastAPI app serves `GET /` with a placeholder page.
- pytest config: `--allow-hosts=127.0.0.1,localhost,::1`, `asyncio_mode=auto`. (Changed from `--disable-socket`: on Windows the asyncio event loop needs a local socketpair, which `--disable-socket` would break.)

## Acceptance criteria
- [x] `git check-ignore .env` succeeds, and the first commit contains `.gitignore` and no `.env`.
- [x] `.env` exists in the project root with the 6 credential keys, and `DEV/.env` no longer exists.
- [x] `uv run mdal` starts the server on 127.0.0.1:8765, and `GET /` returns 200.
- [x] A log record containing the configured AniList token, the MangaDex password, or `Bearer xyz` is emitted with those values replaced by `***`.
- [x] With no `MDAL_DB_PATH`, the DB path is under the local app-data folder.
- [x] A test that opens a real socket to an external host fails.

## Tests
- `test_config.py`: default db path; `.env` parsing using a temp file; secrets absent from `repr`; `.gitignore` covers `.env`; User-Agent is app name only.
- `test_logsetup.py`: redaction of each secret kind, including inside tracebacks.
- `test_main.py`: the host constant is `127.0.0.1`; `run()` passes it to uvicorn; `/` returns 200 via TestClient.
- `test_no_network.py`: an outbound connection raises `SocketConnectBlockedError`.

## Dev notes
- Done 2026-10-03. 23 tests pass. A manual check confirmed `uv run mdal` responds with 200 and listens on 127.0.0.1 only.
- Python 3.12.15 is pinned via `.python-version`; uv downloaded it. Python 3.14 is also on the machine but unused.
- Git identity is set **repo-locally** (name `macbe`, the user's account email), because no global identity existed.
- The user had already moved `.env` into the project root before this story ran; no move was needed.
- **For later stories:**
  - `config.USER_AGENT` is the User-Agent string.
  - `Settings.secret_values()` feeds redaction. Pass `lambda: get_settings().secret_values()` so tokens stored after OAuth (story 06) are masked too.
  - `reload_settings()` exists for story 06.
  - uvicorn runs with `log_config=None`, so all logs go through the redacting root handler.
  - `httpx` logging is set to WARNING because it logs request lines at INFO.
- Tests use the `make_settings` fixture (a temp `.env`). An autouse fixture clears real env vars. Never construct `Settings()` bare in tests: it reads the real `.env`.
- Known warning: Starlette 1.7 deprecates using `httpx` inside `TestClient` ("install httpx2"). This is harmless for now. Revisit if it becomes an error. The app's own clients use `httpx` as planned.
