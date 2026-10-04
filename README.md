# Shiori

Shiori keeps your AniList manga list in step with what you read on MangaDex. It runs on your own computer,
reads both sites, and lists every change it would make. Nothing is written to AniList until you approve it.

## Key features

- **Local only.** Runs at `http://127.0.0.1:8765` and can't be reached from other devices.
- **Conservative progress rules.** Progress only ever goes up. The only status Shiori sets is **Completed**,
  and only when AniList lists the series as finished and you've read its final chapter.
- **New entries on request.** Matched series that aren't on your AniList list appear as new entries
  (Reading, or Completed when finished). Your list is re-read right before writing, so an entry that
  already exists is never added again or changed.
- **Careful with both APIs.** Requests are paced below each site's limit. After any sign of a MangaDex block
  (a 403, repeated 429s, or a dropped connection) Shiori stops and sends nothing to MangaDex for an hour.
- **Matching you can review.** Uncertain matches wait for you, with candidates, covers and scores.
- **Stats.** An AniList-style overview of your list (status, formats, scores, release years, genres, tags,
  staff, and how MangaDex and AniList statuses compare), plus a record of everything syncs have written.
  Every bar opens the series behind it.

## Technology

| Area | Choice |
|---|---|
| Language | Python 3.12+, SQL (SQLite), HTML/CSS, HTMX (no JavaScript build step) |
| Web | `fastapi`, `uvicorn`, `jinja2`, `python-multipart` |
| APIs | `httpx` for both sites, behind one rate-limited client each |
| Matching | `rapidfuzz` for title similarity |
| Configuration | `pydantic-settings`, `python-dotenv` |
| Storage | SQLite in WAL mode, versioned migrations applied on startup |
| Tests | `pytest`, `pytest-asyncio`, `pytest-cov`, `respx`, `pytest-socket` (outbound network blocked) |
| Tooling | [`uv`](https://astral.sh/uv) for Python and dependencies; Hatchling build; `mdal` console command |

## Install

You need Windows 10/11 (macOS and Linux work too; adjust the PowerShell commands), [Git](https://git-scm.com/download/win),
and uv, which also installs Python 3.12:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Close and reopen the terminal so `uv` is on your `PATH`. Then:

```powershell
git clone <this-repository-url> mangadex-anilist-sync
cd mangadex-anilist-sync
uv sync
```

If you move the project folder later, delete `.venv` and run `uv sync` again; uv's launchers remember the old path.

The database lives at `%LOCALAPPDATA%\mangadex-anilist-sync\sync.db` unless `MDAL_DB_PATH` in `.env` points elsewhere.

## Setup

### 1. Register the API clients

**MangaDex personal API client**

1. Sign in at [mangadex.org](https://mangadex.org) and open **Settings → API Clients**.
2. Create a **personal** client.
3. Note the client id (it starts with `personal-client-`) and the client secret.

New clients stay *pending* until MangaDex staff approve them, and can't log in until then.

**AniList application**

1. Sign in at [anilist.co](https://anilist.co) and open **Settings → Developer → Create New Application**.
2. Give it any name, and set the redirect URL to `http://127.0.0.1:8765/auth/anilist/callback`.
3. Save, then note the client id (a number) and the client secret.

AniList applications can't be deleted, so avoid creating spares. If AniList rejects the `127.0.0.1` address,
register `https://anilist.co/api/v2/oauth/pin` instead and set `ANILIST_REDIRECT_URI` to the same value;
Settings then shows a box to paste the token AniList gives you.

### 2. Fill in `.env`

```powershell
Copy-Item .env.example .env
notepad .env
```

| Key | Value |
|---|---|
| `MANGADEX_USERNAME`, `MANGADEX_PASSWORD` | Your MangaDex login |
| `MANGADEX_CLIENT_ID`, `MANGADEX_CLIENT_SECRET` | From the personal API client |
| `ANILIST_CLIENT_ID`, `ANILIST_CLIENT_SECRET` | From the AniList application |
| `ANILIST_REDIRECT_URI` | Exactly the redirect URL you registered |
| `ANILIST_ACCESS_TOKEN` | Leave empty; Shiori fills it in when you connect |
| `MDAL_DB_PATH` | Optional: full path for the database file |
| `MDAL_PORT` | Optional: default `8765` (change the AniList redirect URL to match) |

`.env` is git-ignored. Never commit or share it; if a secret leaks, regenerate it on the site and update `.env`.

### 3. Start Shiori and connect AniList

```powershell
uv run mdal
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765), go to **Settings** and choose **Connect AniList**. AniList asks
you to approve, then sends you back with the token saved. Tokens last a year. **Check login** under MangaDex
confirms your MangaDex credentials with one request. Stop Shiori with `Ctrl+C`.

### 4. Optional: the read-only live check

Before the first sync you can probe both APIs with about five read-only requests each:

```powershell
uv run python scripts/live_check.py --dry   # print the plan, send nothing
uv run python scripts/live_check.py         # run it
```

It needs AniList connected first.

## Using Shiori

The header has four sections:

| Section | Pages |
|---|---|
| **Home** | Latest sync, open items, accounts |
| **Sync** | **Changes** for the latest sync, and **History** of every sync and single add |
| **Matches** | **To review** (uncertain or missing matches) and **Unlisted** (matched series not on your AniList list) |
| **Stats** | **Library** (your AniList list) and **Activity** (what syncs have written) |

### First sync

1. **Home → Start sync.** Shiori reads your MangaDex library and read markers, your AniList list, and matches
   series. The first sync is the slowest: every read chapter is looked up once (about 100 per request at
   3 requests a second, so around 2 minutes for 500 series). Later syncs only look up new chapters.
2. **Matches → To review.** Pick the right AniList entry, paste an AniList URL or id, or mark the series as not
   on AniList. These choices are kept for good; **Retry matching** or **Undo** reverses one.
3. **Sync → Changes.** Each row shows AniList progress → MangaDex progress and the reason.
   - **Updates**: progress to raise on entries you already have.
   - **New entries**: series you read on MangaDex that aren't on your AniList list. Each one creates an entry,
     and like any AniList list change, an activity post.
   - **Flagged**: *over total* rows can't be written; *unusual* rows (very large jumps, chapter numbers that
     restart each volume) are written only with **override** ticked. **Dismiss** moves a flagged row to
     Skipped until your MangaDex progress for that series changes.
   - **Skipped**: already up to date, not matched, or dismissed.
   - Rows that would finish a series have a **mark completed** box, ticked by default.
4. **Write selected to AniList.** Updates and new entries start selected; untick anything to leave out. The
   line above the table estimates the AniList requests and time. Results, including a check of each row
   against AniList afterwards, are under **Sync → History**.

A discarded sync can be brought back with **Restore** from its page or from History.

**Matches → Unlisted** lists the same not-yet-listed series one at a time, for adding with a status and progress
of your choice. Each add re-checks your AniList list first.

### Stats

- **Library** covers your AniList list as of the last sync: entries, chapters and volumes read, mean score,
  status, format, country, started and completed per year, scores, chapters read, progress on current series,
  publication status, release years, genres, tags and staff, and a grid comparing MangaDex and AniList
  statuses. Status, format and country filters apply to everything on the page. Every bar, column and grid
  square opens **Entries**, a sortable list of the series it counts.
- **Activity** shows what syncs have written: chapters added, entries updated and added, completions,
  problems, and the largest updates, for all syncs or one.

Genres, scores, volumes, dates and tags come with the list request every sync already makes. Staff needs
separate lookups: up to 20 requests per sync (25 series each) until your whole list is covered, then only
for newly added series.

## Rate limits and settings

**Settings → Limits and thresholds.** The defaults are deliberately conservative:

| Setting | Default | Notes |
|---|---|---|
| AniList requests per minute | 20 | AniList currently allows 30. Don't go above 30. |
| MangaDex requests per second | 3 | MangaDex allows about 5 per IP and blocks IPs that keep exceeding it. Don't go above 4. |
| AniList entries per write request | 10 | Halved automatically if AniList rejects a batch as too complex; **Reset write batch size to 10** restores it. |
| Title searches per AniList request | 5 | |
| AniList ids per lookup page | 50 | AniList's maximum. |
| Auto-accept score / review score / margin | 0.92 / 0.60 / 0.05 | A lower auto-accept score accepts more matches without review, including wrong ones. |
| Largest believable jump in chapters | 200 | Bigger jumps over an existing AniList value are flagged. |

Rate changes apply immediately, even during a sync; the others apply from the next sync.

## Troubleshooting

| Symptom | What it means and what to do |
|---|---|
| Sync halted: "MangaDex returned 403 … temporary IP ban" | MangaDex has blocked your IP for a while. Shiori pauses MangaDex for an hour and shows the time on Home. Wait it out; retrying sooner prolongs the block. |
| Sync halted: "MangaDex dropped the connection" | MangaDex closed the connection without answering, which is how a block looked on 2026-10-03. Shiori doesn't retry and pauses MangaDex for an hour. If you're sure MangaDex is back, **Settings → MangaDex → Clear cooldown**. |
| Sync halted: "MangaDex kept answering 429" or "request budget nearly used up" | MangaDex rate-limited Shiori, or reported its budget almost spent with a long reset. One-hour pause as above; consider lowering MangaDex requests per second. |
| AniList 429 | Handled automatically: AniList requests pause for as long as AniList asks (or 60 s). After 3 retries on one request the sync halts; try again later. |
| "AniList rejected the token: reconnect AniList" | The token expired (they last a year) or was revoked. **Settings → Disconnect**, then **Connect AniList**. |
| "Client authentication failed" when connecting | `ANILIST_CLIENT_ID` and `ANILIST_CLIENT_SECRET` don't match. Copy both again from AniList → Settings → Developer, and check the redirect URL matches exactly. |
| "MangaDex refused the login (403)" | The personal API client is still pending approval, or the credentials are wrong. |
| Home shows **Resume** | A write stopped part-way (Shiori closed, crashed or halted). Resume re-reads AniList and sends only the rows not yet written. |
| A series' progress looks too low | Some read chapters were deleted on MangaDex, so their numbers can't be recovered; the row shows "N unresolved". AniList progress is never lowered. |
| `uv run …` fails with "trampoline failed to canonicalize script path" | The project folder moved. Delete `.venv` and run `uv sync`. |
| Database errors | Stop Shiori and move `sync.db` aside to start fresh; the next sync rebuilds it (match choices are lost). |

## How it works

1. **Fetch.** Reads the MangaDex library, read markers and chapter numbers (cached for good), and the AniList
   list in one request.
2. **Match.** Uses MangaDex's AniList and MyAnimeList links first, then scored title searches; anything
   uncertain goes to review.
3. **Diff.** Applies the progress rules to each series and lists the changes.
4. **Approve and write.** Re-reads AniList, drops anything that would lower progress or touch an entry that
   appeared meanwhile, writes in batches, then checks each row against AniList.

## Project structure

```
mangadex-anilist-sync/
├── src/mdal/
│   ├── main.py                  # entry point (`uv run mdal`), binds to 127.0.0.1
│   ├── config.py, logsetup.py   # settings from .env; logging with secret redaction
│   ├── db/                      # SQLite connection, repository, migrations/
│   ├── clients/                 # rate-limited AniList and MangaDex clients, OAuth, request pacing
│   ├── fetch/                   # MangaDex library and AniList list, lookups, searches, staff
│   ├── matching/                # title normalisation, scoring, matching pipeline
│   ├── sync/                    # progress rules, sync state machine, writer, single adds, estimates
│   ├── stats.py                 # numbers for the stats pages (database reads only)
│   └── web/                     # FastAPI app, routes/, templates/, static/
├── tests/                       # 33 test modules plus factories and fakes
├── docs/                        # brief, PRD, architecture, API notes, stories/
├── scripts/live_check.py        # read-only API probe
├── pyproject.toml, uv.lock
└── .env.example
```

## Development

```powershell
uv run pytest                                   # full suite; outbound network is blocked
uv run pytest --cov=mdal --cov-report=html      # with coverage
uv run pytest tests/test_orchestrator.py -v     # one file
```

Migrations in `src/mdal/db/migrations/` run on startup. Only `src/mdal/sync/writer.py` and
`src/mdal/sync/add_entry.py` may contain AniList mutations; a test enforces it. Design notes and the build
stories are in `docs/`.

## License

For personal use. Respect the MangaDex and AniList terms of service.
