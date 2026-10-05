# Shiori

Shiori keeps your AniList and MyAnimeList manga lists in step with what you read on MangaDex. It runs on your
own computer, reads MangaDex and one list at a time, and lists every change it would make. Nothing is written
until you approve it.

## Key features

- **Local only.** Runs at `http://127.0.0.1:8765` and can't be reached from other devices.
- **AniList and MyAnimeList.** Each sync targets one site; both use the same rules, review screen and history.
- **Conservative progress rules.** Progress only ever goes up. The only status Shiori sets is **Completed**,
  and only when the site lists the series as finished and you've read its final chapter.
- **New entries on request.** Matched series that aren't on your list appear as new entries
  (Reading, or Completed when finished). Your list is re-read right before writing, so an entry that
  already exists is never added again or changed.
- **Careful with both APIs.** Requests are paced below each site's limit. After any sign of a MangaDex block
  (a 403, repeated 429s, or a dropped connection) Shiori stops and sends nothing to MangaDex for an hour.
- **Matching you can review.** Uncertain matches wait for you, with candidates, covers and scores.
- **Recommendations.** Series you don't have yet, ranked by your genres, tags and favourite creators and by
  what AniList readers recommend from your favourites, with picks and reasons written by a local model
  (Ollama), plus a map of what links each recommendation to your list.
- **New releases and Ask.** A scan of the latest MangaDex series you haven't read or followed, matched to your
  list by description, tags and creators, with picks by the local model; and a chat that suggests unread series.
  The scan can run in the background, and Home shows the picks you haven't seen.
- **Series pages and ratings.** Every recommended or listed series has a page with its details, your list
  status, why it's recommended, its English release (MangaUpdates) and what AniList and MyAnimeList readers
  recommend for it. Rate a series there (or tell Ask) and recommendations learn from it.
- **Rank and tidy your list.** Rank what you've read by comparing two series at a time (like Beli); each
  series' place sets its score, saved to AniList. Find series stalled as Reading, and finished series ready to binge.
- **Search, year in review, compare, backup.** Search any title from the header; see your year in review; compare
  your list with a friend's; back up everything only Shiori knows, and export your list as CSV.
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

**MyAnimeList API client** (optional; only for syncing to MyAnimeList)

1. Sign in at [myanimelist.net](https://myanimelist.net) and open [myanimelist.net/apiconfig](https://myanimelist.net/apiconfig) → **Create ID**.
2. Choose app type **web** (it gets a client secret) or **other** (no secret), and set the App Redirect URL to
   `http://127.0.0.1:8765/auth/mal/callback`.
3. Fill in the required name, description and homepage fields (for a personal tool, your profile URL is fine),
   then note the client id and, for a web app, the client secret.

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
| `MAL_CLIENT_ID`, `MAL_CLIENT_SECRET` | Optional: from the MyAnimeList API client (leave the secret empty for app type **other**) |
| `MAL_REDIRECT_URI` | Optional: default `http://127.0.0.1:8765/auth/mal/callback`; must match the registered URL |
| `MDAL_DB_PATH` | Optional: full path for the database file |
| `MDAL_PORT` | Optional: default `8765` (change the redirect URLs to match) |

`.env` is git-ignored. Never commit or share it; if a secret leaks, regenerate it on the site and update `.env`.

### 3. Start Shiori and connect your lists

```powershell
uv run mdal
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765), go to **Settings** and choose **Connect AniList**. AniList asks
you to approve, then sends you back with the token saved. Tokens last a year. For MyAnimeList, choose
**Connect MyAnimeList** the same way; its sign-in lasts about a month and renews itself while Shiori is used.
**Check login** under MangaDex confirms your MangaDex credentials with one request. Stop Shiori with `Ctrl+C`.

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
| **Home** | Latest sync, open items, accounts, and a start button per site |
| **Sync** | **Changes** for the latest sync, and **History** of every sync and single add, for both sites |
| **Matches** | **To review** (uncertain or missing matches) and **Unlisted** (matched series not on your AniList list) |
| **List** | **Rank** (compare two at a time; scores saved to AniList), **Your ranking**, **Stalled** and **Ready to binge** |
| **Stats** | **Library** (your AniList list), **Connections** (how your tags and genres go together), **Year** (your year in review), **Compare** (with a friend's list) and **Activity** (what syncs have written, per site or both) |
| **Discover** | **For you**, **Genres**, **Tags**, **Creators** (recommendations), **Map**, **New releases** (a MangaDex scan), **Ask** (chat) and **Ratings** (your verdicts) |

### First sync

1. **Home → Sync AniList.** Shiori reads your MangaDex library and read markers, your AniList list, and matches
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

### Syncing to MyAnimeList

**Home → Sync MyAnimeList** follows the same steps against your MyAnimeList list, and its changes are approved
on the same kind of page (**Write selected to MyAnimeList**). The two sites are independent: an AniList diff
waiting for approval stays open while you run a MyAnimeList sync, and the other way round.

- **Which MyAnimeList entry.** Shiori uses the MyAnimeList id that AniList stores for the matched series, and
  MangaDex's own MyAnimeList link for series without an AniList match. Series whose AniList match is waiting
  for review wait here too, and a series whose two ids disagree is skipped. With AniList connected, new
  series are matched first, so most of your library links without any extra MyAnimeList requests.
- **Totals.** For series on your MyAnimeList list, the chapter total and finished status come from
  MyAnimeList. For new entries they come from AniList until the series is on your list.
- **What is sent.** One request per entry: the chapters read, plus `completed` for approved completions or
  `reading` for new entries. Score, dates, volumes and other statuses are never touched.
- **MangaDex reuse.** A MyAnimeList sync started within 10 minutes of the last MangaDex read uses that read
  instead of asking MangaDex again, so syncing both sites back to back costs MangaDex nothing extra.

**Matches → Unlisted** lists the same not-yet-listed series one at a time, for adding with a status and progress
of your choice. Each add re-checks your AniList list first.

### Stats

- **Library** covers your AniList list as of the last sync: entries, chapters and volumes read, mean score,
  status, format, country, started and completed per year, scores, chapters read, progress on current series,
  publication status, release years, genres, tags and staff, and a grid comparing MangaDex and AniList
  statuses. Status, format and country filters apply to everything on the page. Every bar, column and grid
  square opens **Entries**, a sortable list of the series it counts.
- **Connections** is a network of the tags (or genres) on your list. Two are linked when they share series
  more often than chance (at least 1.25 times as often, and at least 3 series), and each keeps only its four
  strongest links, so related themes gather into clusters. Size is how many series have it, shade is how much
  you like it (the same measure Discover uses). The table below the graph has the strongest pairings, and the
  Library filters apply here too.

  Clicking a tag opens it: your 36 best-liked series with that tag appear as covers around it, linked to the
  other tags they share unusually often and to creators behind two or more of them. Click another tag to keep
  exploring (the breadcrumb leads back), a cover to open the series on AniList, a creator to list their series,
  or **List all** for the full table.
- **Activity** shows what syncs have written: chapters added, entries updated and added, completions,
  problems, and the largest updates, for all syncs or one.

Genres, scores, volumes, dates and tags come with the list request every sync already makes. Staff needs
separate lookups: up to 20 requests per sync (25 series each) until your whole list is covered, then only
for newly added series.

### Discover

**Discover → Refresh** builds recommendations from your AniList list, in under a minute and about 15 AniList
requests:

1. If your list has no tags yet, it re-reads the list once (tags come with it), and fetches the writers and
   artists of your 150 strongest series.
2. It works out your taste. Each entry counts by your score, or, when unscored (most of a big library), by its
   status and chapters read; a real score counts three times as much. A genre, tag or creator ranks high when
   you rate its series above your average *and* read a lot of it, so a handful of top-scored horror series
   can outrank hundreds of average fantasy ones, and a genre you mostly drop counts against a series.
3. It gathers candidates: what AniList readers recommend from your 30 favourite series, the top-scored manga
   for your 8 strongest tags and 4 strongest genres, and the most popular manga by your 8 favourite creators.
   Anything on your AniList list or in your MangaDex library is left out.
4. Your local model picks 12 for **For you** and explains each in a sentence, naming series you liked. It only
   chooses among the candidates, by id, so it can't invent a title.

| Page | What's on it |
|---|---|
| **For you** | A summary of your taste and the model's 12 picks, then more ranked by the combined score |
| **Genres / Tags / Creators** | Your strongest ones (each opens the series on your list) and the series that best match them, with the reason for each |
| **Map** | Your top 24 recommendations, shown as covers, linked to the genres, tags, creators and series on your list that led to them. A table below lists the same links. |

Both network graphs work the same way: scroll or **+**/**−** to zoom, drag the background to pan, drag a
node to move it, hover to trace its links, click to open it, double-click or **Fit** to see everything again.
They're laid out with [d3-force](https://d3js.org/d3-force), bundled in `static/vendor/` (no internet needed).

**Not interested** hides a series for good (**Show N hidden** brings them all back). Adult titles are left out
unless you tick **Include adult titles**. **Find on MangaDex** opens a MangaDex search for the title.

**The model.** Shiori uses [Ollama](https://ollama.com) at `http://127.0.0.1:11434` (`OLLAMA_URL` in `.env`
changes it; it must be on this computer). Pick any installed model from the list on the Discover pages;
`gpt-oss:20b` is the default and answers in a few seconds on a 16 GB GPU. Only titles, genres, tags, creators,
statuses and scores are sent, never account details. Without Ollama running, everything still works and
**For you** shows the top-scored series instead; **Ask the model again** retries without re-reading AniList.

### New releases and Ask

**Discover → New releases → Scan MangaDex** reads the 500 series with the latest English chapters and the 100
newest series, plus your MangaDex follows: about 25 paced MangaDex requests (6 pages of series, one page per
100 follows), under a minute. The first scan also re-reads your AniList list once to get descriptions. Anything
in your MangaDex library, followed, on your AniList or MyAnimeList list (by its AniList or MAL link, or by title),
or marked **Not interested** is left out. Each remaining series is scored by:

- **Description**: how close its description is to those of your 150 best-liked series, and how far from the
  ones you dropped. This needs an embedding model in Ollama (see below); without one it's skipped.
- **Tags**: MangaDex genres and themes matched by name to your AniList genres and tags.
- **Creators**: an author or artist of series you liked.

Your chat model then reads the descriptions of the best 40 and picks 12, with a reason for each.

**Ask** is a conversation about what to read next ("a dark fantasy with a clever lead, finished", "more like
that but funnier"). For each message Shiori shortlists the 30 series that best fit both the question and your
taste, from the new releases and the For you candidates; the model can only suggest from that shortlist, so
every answer is a real series you haven't read. A genre or tag you name is a filter ("an isekai", "no gore"),
and a series you name ("I just read X, another like it?") is the reference: it's never suggested back, and
AniList (and MyAnimeList, when connected) readers' recommendations for it join the shortlist. If you say what
you thought of it, Ask saves that as a rating. **New conversation** clears it. A large local model takes
10 to 60 seconds per answer.

**Background scans.** While Shiori runs, it scans MangaDex again when the last scan is older than
**Settings → Scan MangaDex for new releases every (hours)** (default 24; 0 turns it off). It never starts during
a sync or a MangaDex cooldown. Home shows the picks you haven't seen on New releases yet.

### Series pages

Clicking a title anywhere in Discover (or in **Stats → Library**) opens the series page: cover, titles,
description, genres and tags, creators, your AniList and MangaDex status, your rating and why it's recommended.
The first visit each week also looks the series up (a few seconds):

- **MangaUpdates**: English chapters, whether it's fully translated, licensing and English publishers, its score,
  and the categories its readers vote for. Found by the MangaUpdates link MangaDex keeps, else by title.
  No account needed; 1 request per second (**Settings → MangaUpdates requests per second**).
- **AniList readers also recommend**, with the readers' votes; a series too new to have any gets the top-scored
  manga sharing its main tags instead.
- **MyAnimeList readers also recommend**, when MyAnimeList is connected.

A lookup costs 1 to 3 AniList, 1 MyAnimeList and 1 or 2 MangaUpdates requests. After each scan, the model's
picks are looked up on MangaUpdates too, so their cards show English chapter counts.

### Ratings

Rate a series on its page (**Loved it**, **Liked it**, **Didn't like it**, **Read it**, **Not interested**), or
just tell Ask ("I read X and loved it"). A rating counts like a list score towards your genres, tags, creators
and description matching (loved 95, liked 75, didn't like 30; not interested a mild 45; read only stops it
being recommended), and a series you've read is never recommended again. **Not interested** on a card saves
that rating too. **Discover → Ratings** lists them all to change or remove; series on your AniList list keep
using their list entry.

**MyAnimeList in For you.** With MyAnimeList connected, **Refresh** also asks what MyAnimeList readers of your
15 favourites recommend (one request each, cached for a week), so candidates come from both communities.

**Embedding model.** Description matching uses `qwen3-embedding:0.6b` (about 640 MB) by default:

```powershell
ollama pull qwen3-embedding:0.6b
```

Any Ollama embedding model works; choose it next to the chat model on the New releases or Ask page. Vectors
are cached, so later scans only embed new or changed descriptions.

## Your list

### Rank

**List → Rank** shows the series you've read but not ranked, unscored and most-read first. Say how it felt
(**I liked it**, **It was fine**, **I didn't like it**), then pick which you liked more, two at a time, against
series already in that tier. It's a binary search, so ranking into a tier of 100 takes about 7 questions; **Too
close to call** places it right there. Keys: 1/2/3, ←/→, T, S to skip, Enter for the next series.

A series' score follows from its place: **I liked it** spans 6.8–10, **It was fine** 4.0–6.7, **I didn't like it**
0–3.9, best at the top and spread evenly. Placing one moves its neighbours a little, so every score that changes is
saved to your AniList list in the background, 10 per request, by entry id (an edit can never add or duplicate an
entry). **Or just give it a score** saves a number straight away without ranking it. **List → Your ranking** shows
the whole ranking, with **Re-rank** and **Remove** (which leaves the AniList score as it is). MyAnimeList scores
aren't changed.

### Notes

A series on your list has a **Your notes** box on its page: your AniList notes for that entry, read with your
list (or once, when the page first opens). **Save notes** writes them back to AniList by entry id. With
MyAnimeList connected and the series on your MAL list, **Also save to MyAnimeList** mirrors them to its comments;
Shiori checks the entry exists first (one request), because MyAnimeList's save would otherwise create one. Notes
on series you liked or dropped are quoted, shortened, to your local model, so Ask and the picks can use them.

### Stalled and Ready to binge

**Stalled** lists series marked Reading that haven't changed on AniList in 90 days, with **Pause** and **Drop**
(saved to AniList; the only statuses Shiori sets outside a sync). **Ready to binge** lists series you paused,
dropped or stalled on that have finished publishing, your highest-rated first, with chapters left and, loaded from
MangaUpdates as you scroll, the English release.

### Search, year in review, compare

The **search box** in the header finds any title (romaji, English, Japanese, synonyms) among your list, your
MangaDex library, new releases, recommendations and every series Shiori has seen, as you type. **Search AniList**
asks AniList itself (1 request); results open their series page. **Stats → Year** is your year in review from
your list's start and completion dates. **Stats → Compare** reads any public AniList list (1 request) and shows
what you share, how alike your scores and genres are, what they loved that you haven't read, and the other way round.

### Backup and export

**Settings → Backup and export** downloads a JSON backup of what only Shiori knows (match decisions, dismissed
flags, ratings, your ranking, hidden series, Ask conversations, friends, list edits and settings), never logins or
tokens, and restores one: rows in the backup replace the same rows, nothing else is removed. It also exports your
list and your ratings as CSV for Excel or Sheets.

## Rate limits and settings

**Settings → Limits and thresholds.** The defaults are deliberately conservative:

| Setting | Default | Notes |
|---|---|---|
| AniList requests per minute | 20 | AniList currently allows 30. Don't go above 30. |
| MangaDex requests per second | 3 | MangaDex allows about 5 per IP and blocks IPs that keep exceeding it. Don't go above 4. |
| AniList entries per write request | 10 | Halved automatically if AniList rejects a batch as too complex; **Reset write batch size to 10** restores it. |
| Title searches per AniList request | 5 | |
| AniList ids per lookup page | 50 | AniList's maximum. |
| MyAnimeList requests per minute | 30 | MyAnimeList publishes no limit. One request per entry written, so 300 entries take about 10 minutes. |
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
| Sync halted: "MyAnimeList answered 429" or "refused the request (403)" | MyAnimeList is limiting requests or down for maintenance. Nothing more is sent; wait a few minutes, then **Resume**. Lower MyAnimeList requests per minute if it repeats. |
| "MyAnimeList sign-in expired; reconnect MyAnimeList" | The sign-in wasn't renewed within about a month. **Settings → MyAnimeList → Connect MyAnimeList** again. |
| MyAnimeList connect fails or returns to Settings with an error | Check `MAL_CLIENT_ID` (and `MAL_CLIENT_SECRET` for a web app) and that the App Redirect URL matches `MAL_REDIRECT_URI` exactly. |
| "MangaDex refused the login (403)" | The personal API client is still pending approval, or the credentials are wrong. |
| Home shows **Resume** | A write stopped part-way (Shiori closed, crashed or halted). Resume re-reads AniList and sends only the rows not yet written. |
| A series' progress looks too low | Some read chapters were deleted on MangaDex, so their numbers can't be recovered; the row shows "N unresolved". AniList progress is never lowered. |
| `uv run …` fails with "trampoline failed to canonicalize script path" | The project folder moved. Delete `.venv` and run `uv sync`. |
| Database errors | Stop Shiori and move `sync.db` aside to start fresh; the next sync rebuilds it (match choices are lost). |

## How it works

1. **Fetch.** Reads the MangaDex library, read markers and chapter numbers (cached for good), and the AniList
   list in one request (or the MyAnimeList list, 1,000 entries per request).
2. **Match.** Uses MangaDex's AniList and MyAnimeList links first, then scored title searches; anything
   uncertain goes to review.
3. **Diff.** Applies the progress rules to each series and lists the changes.
4. **Approve and write.** Re-reads the list, drops anything that would lower progress or touch an entry that
   appeared meanwhile, writes (in batches on AniList, one entry per request on MyAnimeList), then checks each
   row against the site.

## Project structure

```
mangadex-anilist-sync/
├── src/mdal/
│   ├── main.py                  # entry point (`uv run mdal`), binds to 127.0.0.1
│   ├── config.py, logsetup.py   # settings from .env; logging with secret redaction
│   ├── db/                      # SQLite connection, repository, migrations/
│   ├── clients/                 # rate-limited AniList, MangaDex, MyAnimeList and MangaUpdates clients, OAuth, pacing; Ollama
│   ├── fetch/                   # MangaDex library, AniList and MyAnimeList lists, lookups, searches, staff
│   ├── matching/                # title normalisation, scoring, matching pipeline
│   ├── sync/                    # progress rules, sync state machine, writers, single adds, estimates
│   ├── recommend/               # taste profile, scoring, prompts, map, new-release scan, Ask chat, ratings, series pages
│   ├── stats.py, stats_graph.py # numbers and the Connections graph for the stats pages (database reads only)
│   └── web/                     # FastAPI app, routes/, templates/, static/
├── tests/                       # 36 test modules plus factories and fakes
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
`src/mdal/sync/add_entry.py` may contain AniList mutations, and only `src/mdal/sync/mal_writer.py` may write to
MyAnimeList; tests enforce both. Design notes and the build
stories are in `docs/`.

## License

For personal use. Respect the MangaDex, AniList and MyAnimeList terms of service.
