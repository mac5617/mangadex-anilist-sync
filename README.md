# MangaDex → AniList sync

A local app that copies your MangaDex reading progress to your AniList manga list. Every sync starts as a
**dry run**: you see exactly what would change, approve the rows you want, and only then is anything written.

- Runs on your own machine at `http://127.0.0.1:8765` (never reachable from other devices).
- Progress is only ever raised, never lowered. The only status it can set is **Completed**, and only when
  AniList says the series is finished and you read the final chapter.
- It never adds series to your AniList list on its own. You add them one by one on the "Not on my list" page.
- Both APIs are rate-limited; the app paces itself well below the limits.

Design documents live in [`docs/`](docs/).

---

## 1. Prerequisites

- **Windows 10/11** (macOS/Linux work too; replace the PowerShell commands).
- **git**: <https://git-scm.com/download/win>
- **uv** (installs Python 3.12 for you). In PowerShell:

  ```powershell
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```

  Close and reopen the terminal afterwards so `uv` is on your `PATH`.

> **OneDrive warning.** Don't keep the project or its database in a OneDrive-synced folder (on many
> machines that includes `Documents`). OneDrive can lock or roll back the SQLite file while the app is
> using it. By default the database goes to `%LOCALAPPDATA%\mangadex-anilist-sync\sync.db`, which is not
> synced. If you set `MDAL_DB_PATH` yourself, pick a folder that isn't synced either.

Get the code and install dependencies:

```powershell
git clone <this repository> mangadex-anilist-sync
cd mangadex-anilist-sync
uv sync
```

If you later move the project folder, delete `.venv` and run `uv sync` again (uv's launchers remember the
old path and fail with "trampoline failed to canonicalize script path").

## 2. Register the API clients

You need your own API client on both sites. The app never asks for anything beyond what you put in `.env`.

### MangaDex personal API client

1. Sign in at <https://mangadex.org>, open **Settings → API Clients**, and create a **personal** client.
2. It may show as **pending** until MangaDex staff approve it. It cannot log in until then: the app's
   "Check login" button reports "is the personal API client approved?".
3. Note the **client id** (starts with `personal-client-`) and **client secret**.

### AniList application

1. Sign in at <https://anilist.co>, open **Settings → Developer → Create New Application**.
2. Name: anything (for example "MangaDex sync").
   Redirect URL: `http://127.0.0.1:8765/auth/anilist/callback`
3. Save, then note the **client id** (a number) and **client secret**.

AniList applications **cannot be deleted**, so don't create spare ones. If AniList ever refuses the
`127.0.0.1` redirect, register `https://anilist.co/api/v2/oauth/pin` instead and set the same value as
`ANILIST_REDIRECT_URI` (step 3). The app then shows a link and a box to paste the token AniList displays.

## 3. Fill in `.env`

```powershell
Copy-Item .env.example .env
notepad .env
```

| Key | Value |
|---|---|
| `MANGADEX_USERNAME`, `MANGADEX_PASSWORD` | your MangaDex login |
| `MANGADEX_CLIENT_ID`, `MANGADEX_CLIENT_SECRET` | from the personal API client |
| `ANILIST_CLIENT_ID`, `ANILIST_CLIENT_SECRET` | from the AniList application |
| `ANILIST_REDIRECT_URI` | exactly the redirect URL you registered |
| `ANILIST_ACCESS_TOKEN` | leave empty; the app fills it in when you connect |
| `MDAL_DB_PATH` | optional: a full path for the database file |
| `MDAL_PORT` | optional: default `8765` (if you change it, change the redirect URL on AniList to match) |

`.env` is git-ignored. Never commit it or paste it anywhere. If a secret leaks, regenerate it on the site
and update `.env`.

## 4. Start the app and connect AniList

```powershell
uv run mdal
```

Open <http://127.0.0.1:8765>, go to **Settings**:

1. **Connect AniList.** You are sent to AniList to approve, then back to the app, which stores the token in
   `.env`. Tokens last **one year**.
   "Client authentication failed" from AniList means the client id/secret pair in `.env` is wrong.
2. **Check login** under MangaDex (one token request).

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
   - *Flagged*: "exceeds total" rows can't be written; "implausible" rows (huge jumps, odd chapter numbering)
     can be written only by ticking **override**.
   - *Skipped*: already up to date, not matched, or not on your list.
   - Rows that would finish a series carry a **mark completed** box (ticked by default; untick to only
     update progress).
4. **Approve exactly one entry.** The first live write is limited to a single row on purpose. Approve it,
   wait for the dashboard to show `done`, then open that series on AniList and check it looks right
   (progress, status, and that nothing else changed). History shows the verification result.
5. **Normal use.** After the first verified write, select as many rows as you like. The estimate above
   the table shows how many AniList requests and how long the write will take.

**Not on my list** shows matched series that aren't on your AniList list yet. Each **Add** creates one
entry with the status and progress you choose (Completed is pre-selected only when the series is
finished and you've read the last chapter).

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
| Sync **halted**: "MangaDex returned 403 … temporary IP ban" | MangaDex has temporarily blocked your IP. **Stop and wait** (an hour or more) before syncing again. Retrying straight away prolongs the ban. |
| Sync halted: "MangaDex kept answering 429" | MangaDex rate-limited 4 times in a row. Wait a few minutes. Consider lowering MangaDex requests per second. |
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
