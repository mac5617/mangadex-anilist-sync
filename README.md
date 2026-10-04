# MangaDex → AniList Sync

A local desktop application that synchronizes your MangaDex reading progress to your AniList manga list with full transparency and control. Every sync begins as a **dry run**, allowing you to preview exactly what would change before approving any modifications.

## Key Features

- **Local-only operation** - Runs at `http://127.0.0.1:8765`, never accessible from external devices
- **Conservative progress rules** - Progress only increases, never decreases. Status can only be set to **Completed** when AniList marks the series as finished and you've read the final chapter
- **Selective synchronization** - Matched series not on your AniList appear as **add** rows in the diff (Reading or Completed). All writes require approval, and the app re-verifies your list right before writing to avoid duplicates
- **Intelligent rate limiting** - Respects both APIs' rate limits with automatic pacing and request budgeting
- **Review workflow** - Approve or reject changes row-by-row via a clean web interface
- **History tracking** - View past sync operations and restore dismissed entries
- **Extensive testing** - 34 test files ensuring reliability with network isolation

## Technology Stack

### Languages

- **Python 3.12+** - Core application logic
- **SQL** - Database queries and migrations (SQLite)
- **HTML/CSS** - Web interface templates and styling
- **JavaScript (HTMX)** - Dynamic UI interactions without build tools

### Core Dependencies

| Category | Package | Purpose |
|---|---|---|
| **Web Framework** | `fastapi>=0.115` | Async web server and routing |
| **Server** | `uvicorn>=0.32` | ASGI HTTP server |
| ** templating** | `jinja2>=3.1` | HTML template engine |
| **HTTP Client** | `httpx>=0.28` | Async HTTP requests (both APIs) |
| **Fuzzy Matching** | `rapidfuzz>=3.10` | Manga title normalization and scoring |
| **Configuration** | `pydantic-settings>=2.6` | Settings management with validation |
| **Environment** | `python-dotenv>=1.0` | `.env` file loading and writing |
| **Forms** | `python-multipart>=0.0.20` | HTML form parsing |

### Development Dependencies

| Package | Purpose |
|---|---|
| `pytest>=8.3` | Test framework |
| `pytest-asyncio>=0.24` | Async test support |
| `pytest-cov>=6.0` | Code coverage reporting |
| `pytest-socket>=0.7` | Network socket blocking in tests |
| `respx>=0.22` | HTTP client mocking |

### Runtime Environment

- **Package Manager**: [`uv`](https://astral.sh/uv) - Fast Python package installer and resolver
- **Database**: SQLite 3 with WAL (Write-Ahead Logging) mode
- **Build System**: Hatchling (PEP 517/518 compliant)
- **Entrypoint**: `mdal` CLI command (`python -m mdal.main:run`)

## Quick Start

### Prerequisites

- **Windows 10/11** (macOS/Linux compatible; adjust PowerShell commands)
- **Git**: [Download](https://git-scm.com/download/win)
- **uv** (auto-installs Python 3.12):

  ```powershell
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```

  Close and reopen your terminal to add `uv` to your `PATH`.

### Installation

```powershell
# Clone the repository
git clone <this-repository-url> mangadex-anilist-sync
cd mangadex-anilist-sync

# Create virtual environment and install dependencies
uv sync
```

> If you move the project folder, delete `.venv` and run `uv sync` again (uv's launchers cache the old path).

## Setup Instructions

### 1. Register API Clients

#### MangaDex Personal API Client

1. Sign in at [mangadex.org](https://mangadex.org)
2. Navigate to **Settings → API Clients**
3. Create a **personal** client
4. Copy the **client id** (starts with `personal-client-`) and **client secret**

> ⏳ New clients show as **pending** until MangaDex staff approval. You cannot log in until approved.

#### AniList Application

1. Sign in at [anilist.co](https://anilist.co)
2. Go to **Settings → Developer → Create New Application**
3. Configure:
   - **Name**: Any (e.g., "MangaDex sync")
   - **Redirect URL**: `http://127.0.0.1:8765/auth/anilist/callback`
4. Save and copy the **client id** (numeric) and **client secret**

> 🚫 AniList applications **cannot be deleted**. Avoid creating multiple instances.
> 
> If AniList rejects `127.0.0.1`, register `https://anilist.co/api/v2/oauth/pin` instead and set `ANILIST_REDIRECT_URI` accordingly. The app will then display a token entry box.

### 2. Configure Environment

```powershell
# Copy the example file
Copy-Item .env.example .env

# Edit with your credentials
notepad .env
```

| Configuration Key | Description |
|---|---|
| `MANGADEX_USERNAME` | Your MangaDex username |
| `MANGADEX_PASSWORD` | Your MangaDex password |
| `MANGADEX_CLIENT_ID` | MangaDex personal client ID |
| `MANGADEX_CLIENT_SECRET` | MangaDex personal client secret |
| `ANILIST_CLIENT_ID` | AniList application client ID (numeric) |
| `ANILIST_CLIENT_SECRET` | AniList application client secret |
| `ANILIST_REDIRECT_URI` | Must match registered redirect URL |
| `ANILIST_ACCESS_TOKEN` | Leave empty; app populates via OAuth |
| `MDAL_DB_PATH` | (Optional) Custom database path |
| `MDAL_PORT` | (Optional) Default: `8765` |

**Security Notes**:
- `.env` is git-ignored; never commit it
- Regenerate leaked secrets immediately
- Update `.env` after regenerating

### 3. Launch the Application

```powershell
uv run mdal
```

Open your browser to [http://127.0.0.1:8765](http://127.0.0.1:8765).

### 4. Connect AniList

1. Navigate to **Settings** in the web UI
2. Click **Connect AniList** - you'll be redirected to AniList for authorization
3. Approve the application; you'll return to the app with your token stored
4. Tokens expire after **one year** (reconnect when prompted)

> ❌ "Client authentication failed" indicates incorrect client ID/secret in `.env`

## Project Structure

```
mangadex-anilist-sync/
├── src/mdal/                    # Main application package
│   ├── main.py                  # CLI entry point & uvicorn server
│   ├── config.py                # Settings management
│   ├── logsetup.py              # Logging configuration
│   ├── db/                      # SQLite database layer
│   │   ├── connection.py        # Database connection (WAL mode)
│   │   ├── repo.py              # Data access layer
│   │   └── migrations/          # Versioned SQL migrations
│   ├── clients/                 # API clients with rate limiting
│   │   ├── anilist.py           # AniList GraphQL client
│   │   ├── anilist_oauth.py     # OAuth flow handling
│   │   ├── mangadex.py          # MangaDex REST client
│   │   └── ratelimit.py         # Request pacing utilities
│   ├── fetch/                   # Data extraction modules
│   │   ├── anilist_list.py      # Fetch AniList manga list
│   │   └── mangadex_library.py  # Fetch MangaDex library
│   ├── matching/                # Title matching algorithms
│   │   ├── normalize.py         # Title normalization
│   │   ├── score.py             # Similarity scoring
│   │   └── pipeline.py          # Match pipeline
│   ├── sync/                    # Synchronization logic
│   │   ├── orchestrator.py      # Sync state machine
│   │   ├── rules.py             # Progress update rules
│   │   ├── writer.py            # Write operations
│   │   ├── add_entry.py         # Add single entry mutation
│   │   └── estimate.py          | Request counting
│   └── web/                     # Web interface
│       ├── app.py               # FastAPI application
│       ├── routes/              # Route handlers
│       ├── templates/           # Jinja2 HTML templates
│       └── static/              # CSS, JS, assets
├── tests/                       # Comprehensive test suite (34 files)
│   ├── factories.py             # Test data factories
│   ├── fakes.py                 # Mock implementations
│   ├── conftest.py              # Pytest fixtures
│   └── test_*.py                # Unit and integration tests
├── docs/                        # Design documentation
│   ├── architecture.md          # System architecture
│   ├── api-notes.md             # API integration notes
│   ├── brief.md                 # Project brief
│   ├── prd.md                   # Product requirements
│   ├── readiness.md             # Pre-flight checks
│   └── stories/                 # Development epics
├── scripts/
│   └── live_check.py            # Read-only health probe
├── pyproject.toml               # Project metadata & dependencies
├── uv.lock                      # Deterministic dependency pins
├── .env.example                 # Environment template
└── README.md                    # This file
```

## How It Works

1. **Fetch Phase**: Downloads your MangaDex library and AniList list with automatic rate limiting
2. **Normalization**: Cleans and standardizes manga titles for comparison
3. **Matching Pipeline**: Scores potential matches using fuzzy matching algorithms
4. **Diff Generation**: Creates a preview of proposed changes based on your reading progress
5. **Review**: Approve/reject individual rows through the web interface
6. **Write Phase**: Re-verifies your list and applies approved changes with batched mutations

## Development

### Running Tests

```powershell
# Run all tests (network calls blocked except localhost)
uv run pytest

# With coverage report
uv run pytest --cov=src/mdal --cov-report=html

# Run specific test file
uv run pytest tests/test_orchestrator.py -v
```

### Database Migrations

Migrations live in `src/mdal/db/migrations/`. Run automatically on startup. To view current schema:

```sql
SELECT * FROM schema_version;
SELECT * FROM settings;
```

### Logs

Check Python logs via terminal output. The app uses structured logging with automatic secret redaction.

## Troubleshooting

### "Client authentication failed"
- Verify AniList client ID/secret in `.env`
- Ensure redirect URL matches exactly

### MangaDex login fails
- Confirm your personal API client is approved (check MangaDex settings)
- Verify credentials in `.env`

### Database errors
- Try renaming/deleting `sync.db` to reset

### Rate limit errors
- App automatically paces requests; check `anilist_rpm` and `mangadex_rps` in settings
- Lower rates in UI if issues persist

## License

This project is for personal use. Respect MangaDex and AniList terms of service.

## Credits

Built with ❤️ for manga enthusiasts who want control over their reading data.

Stop the app with `Ctrl+C`.

## 5. Optional: the read-only live check

Before the first sync you can probe both APIs with a few read-only requests (about 5 per site). It never
changes anything:

```powershell
uv run python scripts/live_check.py --dry   # print the plan, send nothing
uv run python scripts/live_check.py         # run it
```

It needs AniList connected first (step 4). If it reports anything other than OK, see Troubleshooting.

## 6. First run, step by step

1. **Dashboard → Start sync.** The app reads your MangaDex library and read markers, your AniList list,
   and matches series. The first sync is the slowest: every read chapter has to be looked up once
   (about 100 chapters per request at 3 requests/s; a library of 500 series takes around 2 minutes).
   Later syncs only look up new chapters and take seconds.
2. **Review.** Series the app wasn't sure about wait on the **Review** page. Pick the right AniList entry,
   paste an AniList URL or id, or mark "Not on AniList". These decisions are permanent (use "Retry
   matching" or "Undo" to change them).
3. **Diff.** Open the sync from the dashboard. Each row shows AniList progress → MangaDex progress and why.
   - *To write*: safe updates.
   - *To add*: series you read on MangaDex that aren't on your AniList list yet. Each one creates a new
     entry (and, as with any AniList list change, an activity post).
   - *Flagged*: "exceeds total" rows can't be written; "implausible" rows (huge jumps, odd chapter numbering)
     can be written only by ticking **override**.
   - *Skipped*: already up to date, not matched, or not on your list.
   - Rows that would finish a series carry a **mark completed** box (ticked by default; untick to only
     update progress).
4. **Approve.** Every *to write* and *to add* row is ticked for you; untick anything you want to leave out
   (flagged rows stay unticked). The estimate above the table shows how many AniList requests and how long
   the write will take. When the dashboard shows `done`, History shows each row's verified result.
   If you'd rather start carefully, untick all but one row the first time and check it on AniList.

**Not on my list** shows the same not-yet-listed series one by one, for adding with a status and progress
you choose (for example a row the diff flagged). Each **Add** first re-checks your list, then creates one entry.

**History** lists every sync and add, with request counts and per-row results.

## 7. Rate limits and settings

Settings → *Sync settings*. The defaults are deliberately conservative:

| Setting | Default | Notes |
|---|---|---|
| AniList requests per minute | 20 | AniList currently allows 30/min. **Don't raise it above 30.** |
| MangaDex requests per second | 3 | MangaDex allows about 5/s per IP and bans IPs that keep exceeding it. **Don't go above 4.** |
| AniList entries per write request | 10 | Halved automatically if AniList rejects a batch for complexity. "Reset to 10" restores it. |
| Title searches per request | 5 | |
| AniList ids per lookup page | 50 | AniList's maximum. |
| Auto-accept score / review score / margin | 0.92 / 0.60 / 0.05 | Lower the auto-accept score and more matches are accepted without review, including wrong ones. |
| Largest believable jump | 200 | Bigger progress jumps over an existing AniList value are flagged. |

Rate changes apply immediately, even to a sync in progress; the others apply from the next sync.

## 8. Troubleshooting

| Symptom | What it means / what to do |
|---|---|
| Sync **halted**: "MangaDex returned 403 … temporary IP ban" | MangaDex has temporarily blocked your IP. The app pauses all MangaDex requests for an hour (dashboard countdown). Wait it out: retrying straight away prolongs the ban. |
| Sync halted: "MangaDex dropped the connection" | MangaDex closed the connection without answering. This is how a block looked on 2026-10-03, so the app does not retry and pauses MangaDex requests for an hour. If you are sure MangaDex is fine again, Settings → MangaDex → **Clear cooldown**. |
| Sync halted: "MangaDex kept answering 429" or "request budget nearly used up" | MangaDex rate-limited the app, or reported its budget almost spent with a long reset. One-hour pause as above. Consider lowering MangaDex requests per second. |
| AniList 429 | Handled automatically: all AniList requests pause for the time AniList asks (or 60 s), then continue. After 3 retries on one request the sync halts; try again later. |
| "AniList rejected the token: reconnect AniList" | The token expired (they last a year) or was revoked. Settings → Disconnect → Connect AniList. |
| "Client authentication failed" when connecting | `ANILIST_CLIENT_ID` / `ANILIST_CLIENT_SECRET` don't match. Copy them again from AniList → Settings → Developer. |
| "MangaDex refused the login (403)" | The personal API client is still pending approval, or the credentials are wrong. |
| Dashboard shows **Resume** | A write was interrupted (app closed, crash, halt). Resume re-reads AniList and sends only the rows not yet written. |
| A series' progress looks too low | Some read chapters may have been deleted on MangaDex, so their numbers can't be recovered. The diff shows "N unresolved" for those rows. Progress is never lowered on AniList. |
| `uv run …` fails with "trampoline failed to canonicalize script path" | The project folder moved. Delete `.venv` and run `uv sync`. |

## Development

```powershell
uv run pytest                 # full suite; outbound network is blocked in tests
uv run pytest --cov=mdal      # with coverage
```

Only `src/mdal/sync/writer.py` and `src/mdal/sync/add_entry.py` may contain AniList mutations; a test
enforces it. Stories and design notes are in `docs/stories/` and `docs/architecture.md`.
