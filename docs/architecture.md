# Architecture

Inputs: `brief.md`, `api-notes.md`, `prd.md`. Requirement ids (FR-/NFR-) refer to `prd.md`.

## 1. Stack decision
The suggested stack is kept as-is. Additions, each with its reason:

| Piece | Choice | Why |
|---|---|---|
| Runtime | Python 3.12, `uv` (uv installs Python itself) | as suggested |
| Web | FastAPI + Jinja2 + HTMX (vendored `static/htmx.min.js`, no CDN) | no JS build; works offline |
| HTTP | `httpx.AsyncClient`, one instance per API, owned by the client module | NFR-1 |
| DB | stdlib `sqlite3`, WAL mode, hand-written SQL, versioned migration files | single user; no ORM needed |
| Config | `pydantic-settings` reading `.env`; `python-dotenv.set_key` to store the AniList token | NFR-9 |
| Matching | `rapidfuzz` | as suggested |
| Forms | `python-multipart` | FastAPI form posts |
| Tests | `pytest`, `pytest-asyncio`, `respx`, `pytest-socket` (`--allow-hosts=127.0.0.1,localhost,::1`) | NFR-13 |

Background work runs as an `asyncio` task inside the app process. There is no worker process and no queue library. One global `asyncio.Lock` enforces one sync at a time (FR-1). The UI polls a status fragment every 2 s via HTMX; the polling is local and never touches the APIs.

Server: `uvicorn` started from `mdal/main.py` with `host="127.0.0.1"` hard-coded (NFR-10). The port comes from settings, default 8765.

## 2. Layout and module boundaries
```
pyproject.toml  .env.example  .gitignore  README.md
scripts/live_check.py            # read-only live probe (story 09)
src/mdal/
  main.py                        # uvicorn entry, 127.0.0.1
  config.py                      # Settings (secrets) + tunables from DB settings table
  logsetup.py                    # logging config + RedactingFilter
  db/connection.py  db/migrations/001_init.sql  db/repo.py
  clients/ratelimit.py           # PacedQueue: single-flight, min-interval, pause_until
  clients/anilist.py             # AniListClient: graphql(), errors, 429, budget
  clients/mangadex.py            # MangaDexClient: get(), token mgmt, 429/403
  clients/anilist_oauth.py       # authorize URL, code exchange (uses AniListClient's transport rules)
  fetch/mangadex_library.py      # library + details + read markers + chapter numbers
  fetch/anilist_list.py          # Viewer, MediaListCollection, Page lookups/search
  matching/normalize.py  matching/score.py  matching/pipeline.py
  sync/rules.py                  # pure progress rules -> DiffItem
  sync/estimate.py               # request count + duration
  sync/orchestrator.py           # state machine, run persistence
  sync/writer.py                 # batched aliased mutations, resume, verify
  sync/add_entry.py              # single "add to AniList" mutation (FR-30)
  web/app.py  web/routes/{dashboard,sync,review,notlisted,history,settings,auth}.py
  web/templates/*.html  web/static/{htmx.min.js,app.css}
tests/ (mirrors src)
```
Dependency rules, enforced by a test that scans imports:
- Only `clients/*` may import `httpx`.
- `matching/normalize.py`, `matching/score.py`, `sync/rules.py` and `sync/estimate.py` are pure: no I/O, no DB.
- `web/*` calls `sync/*`, `matching/pipeline` and `db/repo`, never `clients/*` directly. The one exception is `web/routes/auth.py`, which uses `clients/anilist_oauth`.

## 3. Configuration
- `.env` (secrets + identity): `MANGADEX_USERNAME`, `MANGADEX_PASSWORD`, `MANGADEX_CLIENT_ID`, `MANGADEX_CLIENT_SECRET`, `ANILIST_CLIENT_ID`, `ANILIST_CLIENT_SECRET`, `ANILIST_REDIRECT_URI` (default `http://127.0.0.1:8765/auth/anilist/callback`), `ANILIST_ACCESS_TOKEN` (written by the app after OAuth), `MDAL_DB_PATH` (optional), `MDAL_PORT` (optional).
- User-Agent: `mangadex-anilist-sync/<version>`. No contact info (user decision, 2026-10-03). It is honest, not a browser string.
- `.env` lives in the project root (`mangadex-anilist-sync/.env`). The user's existing `DEV/.env` is moved there in story 01.
- Tunables live in the `settings` table, editable in the UI. Defaults:

| key | default |
|---|---|
| `anilist_rpm` | 20 |
| `anilist_write_batch` | 10 (lowered automatically on complexity error) |
| `anilist_search_batch` | 5 (aliased `Page` searches per request) |
| `anilist_page_size` | 50 |
| `mangadex_rps` | 3 |
| `match_auto` | 0.92 |
| `match_review` | 0.60 |
| `match_margin` | 0.05 |
| `jump_limit` | 200 |
| `first_write_done` | false |

Validation: `anilist_rpm` 1–30, `mangadex_rps` 0.2–4; out-of-range values are rejected by the settings form.

## 4. SQLite schema (`001_init.sql`)
```sql
CREATE TABLE schema_version(version INTEGER NOT NULL);
CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);

-- MangaDex snapshot (replaced each fetch, except md_chapter which is a permanent cache)
CREATE TABLE md_manga(
  md_id TEXT PRIMARY KEY, in_library INTEGER NOT NULL, reading_status TEXT,
  title TEXT NOT NULL, alt_titles TEXT NOT NULL,          -- JSON list of strings
  original_language TEXT, year INTEGER, pub_status TEXT, last_chapter TEXT,
  links TEXT NOT NULL, links_hash TEXT NOT NULL,           -- JSON map; sha1 of sorted JSON
  authors TEXT NOT NULL, cover_file TEXT,
  chapter_numbers_reset INTEGER NOT NULL DEFAULT 0, fetched_at TEXT NOT NULL);
CREATE TABLE md_read(md_id TEXT NOT NULL, chapter_id TEXT NOT NULL, PRIMARY KEY(md_id, chapter_id));
CREATE TABLE md_chapter(chapter_id TEXT PRIMARY KEY, md_id TEXT, chapter TEXT, volume TEXT,
  missing INTEGER NOT NULL DEFAULT 0,                      -- 1 = /chapter did not return it
  fetched_at TEXT NOT NULL);

-- AniList
CREATE TABLE al_media(media_id INTEGER PRIMARY KEY, id_mal INTEGER, type TEXT, format TEXT,
  status TEXT, chapters INTEGER, country TEXT, start_year INTEGER,
  romaji TEXT, english TEXT, native TEXT, synonyms TEXT NOT NULL, staff TEXT NOT NULL,
  cover_url TEXT, site_url TEXT, fetched_at TEXT NOT NULL);
CREATE TABLE al_entry(entry_id INTEGER PRIMARY KEY, media_id INTEGER NOT NULL UNIQUE,
  status TEXT NOT NULL, progress INTEGER NOT NULL, fetched_at TEXT NOT NULL);

-- Matching
CREATE TABLE mapping(md_id TEXT PRIMARY KEY, al_media_id INTEGER,
  state TEXT NOT NULL CHECK(state IN ('confirmed','auto','review','unmatched','not_on_anilist')),
  tier INTEGER NOT NULL,            -- 1 user, 2 links.al, 3 links.mal, 4 title
  confidence REAL, reasons TEXT NOT NULL, links_hash TEXT, updated_at TEXT NOT NULL);
CREATE TABLE match_candidate(md_id TEXT NOT NULL, al_media_id INTEGER NOT NULL,
  score REAL NOT NULL, reasons TEXT NOT NULL, rank INTEGER NOT NULL,
  PRIMARY KEY(md_id, al_media_id));

-- Sync runs
CREATE TABLE sync_run(run_id INTEGER PRIMARY KEY, started_at TEXT NOT NULL,
  state TEXT NOT NULL, phase_detail TEXT, error TEXT,
  est_requests INTEGER, est_seconds INTEGER, req_anilist INTEGER NOT NULL DEFAULT 0,
  req_mangadex INTEGER NOT NULL DEFAULT 0, approved_at TEXT, finished_at TEXT);
CREATE TABLE sync_item(run_id INTEGER NOT NULL, md_id TEXT NOT NULL,
  al_media_id INTEGER, al_entry_id INTEGER, al_progress INTEGER, md_progress INTEGER,
  al_status_before TEXT,           -- set by the pre-write re-read; verify compares against it
  set_status TEXT CHECK(set_status IS NULL OR set_status = 'COMPLETED'),
  status_source TEXT,              -- 'AniList' | 'MangaDex' (where the total came from)
  status_approved INTEGER NOT NULL DEFAULT 0,
  action TEXT NOT NULL CHECK(action IN ('write','skip','flag')),
  flag_kind TEXT,                  -- exceeds_total (hard) | implausible (overridable)
  reason TEXT, hint TEXT, unresolved_reads INTEGER NOT NULL DEFAULT 0,
  approved INTEGER NOT NULL DEFAULT 0,
  write_state TEXT NOT NULL DEFAULT 'none' CHECK(write_state IN ('none','pending','done','failed','dropped')),
  written_at TEXT, verify_note TEXT,
  PRIMARY KEY(run_id, md_id));
```
Not-on-my-list items are derived: `mapping.state IN ('confirmed','auto') AND al_media_id NOT IN al_entry`.

## 5. Rate-limited clients
### PacedQueue (`clients/ratelimit.py`), shared mechanism
- Constructed with `min_interval` (seconds) and injectable `clock`/`sleep` (for tests).
- `async with queue.slot():` acquires an `asyncio.Lock` (single-flight) and waits until `max(last_start + min_interval, paused_until)`. It records `last_start`, and the caller holds the slot until its response is read.
- `pause_for(seconds)` sets `paused_until = max(paused_until, now + seconds)`. Every waiter respects it, so a 429 stops the whole queue.

### AniListClient
- `min_interval = 60 / anilist_rpm` (3.0 s at 20 rpm). Spacing is applied even after a pause.
- `graphql(query, variables) -> dict`:
  1. Acquire a slot and POST. Increment the run's `req_anilist`.
  2. Status 429, whatever the body: `wait = Retry-After or (X-RateLimit-Reset − now) or 60`, plus 5 s. Call `pause_for(wait)`, then retry (max 3 retries, then `AniListRateLimited`).
  3. Status 403, or a body mentioning the API being disabled: raise `AniListUnavailable` (halts the sync, no retry).
  4. 5xx or network error: retry with 5/15/45 s backoff (shares the 3-retry cap).
  5. 200 with `errors`: raise `AniListGraphQLError(errors)`. A message containing "complexity" becomes the subclass `AniListComplexityError`. 401 → `AniListAuthError` (UI prompts to reconnect).
- `X-RateLimit-Remaining` is logged at debug level only and never read for pacing.

### MangaDexClient
- `min_interval = 1 / mangadex_rps` (0.334 s). Headers: User-Agent; never `Via`.
- Token: password grant on first use. Keep `access`, `refresh` and `expires_at` in memory only. Refresh when `now > issued + 13 min`; on 401, refresh once and retry. If the refresh fails, use the password grant again. Token calls go through the **same** PacedQueue, because the auth host counts toward `*.mangadex.org`.
- `get(path, params)`:
  - 429: `consecutive_429 += 1`. Wait `X-RateLimit-Retry-After` (Unix time) or `Retry-After`, else 10/30/60 s. `pause_for(wait)`, then retry. A 4th consecutive 429 raises `MangaDexRateLimited` (halt).
  - 403 (not on the token endpoint): raise `MangaDexBlocked` immediately. The orchestrator halts with "MangaDex returned 403, likely a temporary IP ban; wait before retrying".
  - Success resets `consecutive_429`.
- Helper `get_by_ids(path, ids, extra_params)` chunks into groups of 100 and always sets `limit=100`.

## 6. Data fetching
**MangaDex** (`fetch/mangadex_library.py`):
1. `/manga/status` → library ids and statuses.
2. `/manga?ids[]…&limit=100&contentRating[]=safe&…=suggestive&…=erotica&…=pornographic&includes[]=author&includes[]=cover_art` per 100 ids → `md_manga`.
3. `/manga/read?ids[]…&grouped=true` per 100 ids → `md_read`.
4. Unknown chapter ids = `md_read.chapter_id` minus `md_chapter` → `/chapter?ids[]…&limit=100&contentRating[]×4&includeUnavailable=1` per 100. Ids that were requested but not returned get `missing=1` and are retried at most once more, on the next sync, before being permanent.

**AniList** (`fetch/anilist_list.py`):
- `viewer_id()` is cached in settings.
- `list_collection(user_id)` makes one request. Every list is flattened and de-duplicated by entry id; media go to `al_media`, entries to `al_entry`.
- `media_by_ids(ids)` and `media_by_mal_ids(ids)` use `Page(perPage:50){media(id_in:…, type:MANGA)}`. Media found in `al_media` from the list fetch are not re-requested.
- `search_batch(queries)` sends one document with up to `anilist_search_batch` aliased `Page(perPage:5){media(search:$qN, type:MANGA){… staff(perPage:3){nodes{name{full native}}}}}`.

## 7. Matching pipeline (`matching/pipeline.py`)
For each library series without a usable mapping, i.e. no mapping, or `state IN ('auto','review','unmatched')` with a changed `links_hash`. Review and unmatched results are cached like auto ones, so they cost no requests on later syncs. The review screen has a per-series "retry matching" action that deletes the mapping (decision: fewer requests).
1. **Tier 1:** `state IN ('confirmed','not_on_anilist')` → keep, never touched (FR-20).
2. **Tier 2:** parse `links.al` as an int. Collect ids across all series, then validate in one bulk pass (ids already in `al_media` from the list count as validated; the rest go through `media_by_ids`). Valid MANGA → `auto`, unless FR-16 applies → `review`, with the linked media as the sole candidate.
3. **Tier 3:** the same with `links.mal` via `media_by_mal_ids`. If several AniList media share an idMal → `review`.
4. **Tier 4:** title search, scored (below). Search with the primary title. If no candidate scores ≥ `match_review`, make one more search with the best alt title (English first, then ja-ro), in the next batch. Two searches per series at most.

**Normalise** (`normalize.py`): NFKC (fixes full/half width) → casefold → strip diacritics (NFKD, drop combining marks) → replace punctuation/symbols with spaces → collapse whitespace. Romanisation variants such as "ou/o/ō" are partly handled by the diacritic strip; nothing more is attempted.

**Score** (`score.py`, pure). `title_sim` = the maximum over (MD title ∪ alt titles) × (romaji, english, native, synonyms) of `rapidfuzz.fuzz.token_sort_ratio / 100`; an exact normalised equality scores 1.0. Then adjust:

| signal | agree | disagree | unknown |
|---|---|---|---|
| start year (MD `year` vs AL `startDate.year`) | |Δ| ≤ 1: +0.03 | |Δ| ≥ 3: −0.15 | 0 |
| country (`ja`→JP, `ko`→KR, `zh`→CN, `zh-hk`→TW/HK/CN) | +0.02 | −0.15 | 0 |
| format (AL MANGA/ONE_SHOT expected) | 0 | NOVEL: −0.30; ONE_SHOT when MD has > 3 read or listed chapters: −0.10 | 0 |
| author (any normalised name overlap) | +0.05 | −0.10 | 0 |

`confidence = clamp(title_sim + Σ, 0, 1)`. Every term is appended to `reasons`, for example `"title 0.97 (en 'Frieren' ~ english)"` and `"year 2020 vs 2020 +0.03"`. A **disagreeing** signal blocks auto-accept.

**Classify:** `top ≥ match_auto` and `top − second ≥ match_margin` and no disagreement → `auto`. Else `top ≥ match_review` → `review`. Else → `unmatched`. Boundaries: values exactly at a threshold go to the higher class. The top 5 candidates are stored in `match_candidate` for the review screen.

## 8. Progress rules and diff (`sync/rules.py`, pure)
Input per series: read chapter strings; the AniList entry (or none); AniList media `chapters` and `status`; MangaDex `pub_status` and `last_chapter`; `chapter_numbers_reset`; `jump_limit`.
1. Parse every read chapter with `Decimal`. Null, empty or non-numeric → count as `unresolved` (together with `missing` ids).
2. No numeric read chapters → skip, "nothing read".
3. `md = floor(max)`.
4. No AniList entry → not-on-my-list (no diff row; shown on that screen).
5. **Completion check** (decision 2026-10-03, see `brief.md`). The series counts as *known complete* only when AniList media `status == FINISHED` and a total is known:
   - `total = chapters` if AniList has it (source "AniList");
   - else `total = floor(last_chapter)` if MangaDex `pub_status == completed` and `last_chapter` is numeric (source "MangaDex").

   AniList saying RELEASING, HIATUS, CANCELLED or NOT_YET_RELEASED always means "not known complete", whatever MangaDex says. A wrong completion is worse than a missing one.

   `complete = known_complete and md == total and entry.status != COMPLETED`.
6. `md ≤ al.progress`:
   - if `complete` and `al.progress == md` → write, **status only** (`set_status='COMPLETED'`, progress unchanged), reason "at final chapter; mark completed";
   - otherwise → skip, "AniList at/ahead".
7. AniList `chapters` known and `md > chapters` → flag `exceeds_total` (hard). If only the MangaDex total is known and `md > total` → no flag, but no completion, and the hint "reads go beyond MangaDex's last chapter <n>".
8. Implausible → flag `implausible` (overridable), when any of:
   - `chapter_numbers_reset`;
   - max > 2 × second-highest and max − second > 20 (only when there are ≥ 2 distinct numeric reads);
   - `al.progress > 0` and `md − al.progress > jump_limit`.
9. Otherwise → write. In every write or flag row, `set_status='COMPLETED'` when `complete`, else NULL. Any current status (CURRENT, PLANNING, PAUSED, DROPPED, REPEATING) is eligible. The status is shown in the diff as e.g. "Reading → Completed (AniList: finished, 120 ch)", and the row has a "mark completed" checkbox, checked by default, that can drop the status part.
10. Hints, independent of action: `md == total` but not `known_complete` → "at last known chapter, but AniList lists the series as <status>; status unchanged". Status PLANNING and not completing → "status is Planning; will remain Planning".

`COMPLETED` is the **only** status value the sync ever writes. Score, notes, dates, repeat count and privacy are never sent.

Mapping states `review`/`unmatched` produce skip rows with reason "awaiting match review" / "no match" so the diff is complete.

## 9. Sync state machine (`sync/orchestrator.py`)
```
idle → fetching → resolving → diffed(awaiting_approval) → writing → verifying → done
any active state ──error──→ failed (resumable if it failed in writing)
any            ──MangaDexBlocked / AniListUnavailable──→ halted
diffed ──user discards──→ cancelled
```
- The `sync_run.state` change is committed before each phase starts. On startup, a run left in `fetching`/`resolving` is marked `failed`; these phases are cheap to redo, so starting a new sync is the recovery. A run left in `writing`/`verifying` gets a **Resume** button.
- `diffed`: computes `est_requests` and `est_seconds` (§11) and waits for the user.
- Approve: marks the selected items `approved=1, write_state='pending'`. Validation rejects `exceeds_total` items and enforces FR-9: while `first_write_done` is false, exactly one approved item is allowed.
- **writing** (`sync/writer.py`):
  1. Re-fetch the AniList list (1 request) and store `al_status_before` on each pending item. An item is `dropped`, with a reason, if:
     - the entry is gone; or
     - it is a progress item and AniList is now ≥ `md_progress`, unless the only remaining change is an approved completion that is still valid (then it becomes status-only); or
     - it is a status-only item and AniList is already COMPLETED or its progress has moved away from the total.
  2. Chunk the pending items into batches of `anilist_write_batch`. Each batch is one document, with one alias per item: `m0: SaveMediaListEntry(id: $e0, progress: $p0, status: $s0){id progress status}`. `progress` is included only for progress changes. `status` is included only when `set_status='COMPLETED'` and `status_approved=1`, and its value is always the literal `COMPLETED`, never anything else.
  3. On success, mark each aliased item `done` + `written_at` in **one transaction per batch**, committed immediately.
  4. Per-alias errors (partial data): affected items are marked `failed` with the message; the others are `done`.
  5. `AniListComplexityError` → halve the batch size (min 1), persist it, retry the same items. Nothing was written, because the complexity check rejects the whole document.
  6. Resume = run writing again. Only `pending` items are sent; `done` items are never re-sent.
- **verifying:** re-fetch the list (1 request). For each `done` item, compare progress (≠ written → `verify_note`) and status. The expected status is COMPLETED if we sent it, else `al_status_before`; anything else → `verify_note` "status changed by AniList: X→Y". Completion dates are reported if AniList set them itself (informational). On the first-ever write, success sets `first_write_done=true`, and the UI shows the status-change result prominently.
- "Add to AniList" (FR-30) is not part of a run. The status dropdown defaults to Completed when the §8 completion check holds, else Reading. It is one `SaveMediaListEntry(mediaId, status, progress)` through the same client, recorded in history as a one-item run with `state='done'`. While `first_write_done` is false, it counts as the first write (FR-9).
- The diff page recomputes the estimate locally when checkboxes change (`POST /sync/{id}/estimate`, no API calls).

## 10. Error handling summary
| Condition | Behaviour |
|---|---|
| MangaDex 429 | queue paused, backoff, max 3 consecutive retries, then halt |
| MangaDex 403 | halt immediately, message to user, no retry |
| MangaDex 401 | refresh once, then password grant, then fail with "check credentials" |
| AniList 429 | queue paused per §5, max 3 retries/request |
| AniList 403 / disabled | halt, message |
| AniList 401 | fail run; dashboard shows "Reconnect AniList" |
| AniList complexity | halve batch, retry |
| Per-alias mutation error | item `failed`, others continue |
| Unexpected exception | run `failed`, traceback logged (redacted), resumable if in writing |

## 11. Request arithmetic (library of 500 series)
Assumptions: 500 library series; 85% have `links.al`, 5% only `links.mal`, 10% neither. 450 of the matched series are already on the AniList list. Average 60 read chapters per series (30,000 ids). 60 entries need updating.

**MangaDex**, at 3 req/s:

| Call | First sync | Later syncs |
|---|---|---|
| token | 1 | 1 |
| `/manga/status` | 1 | 1 |
| `/manga` (100/req) | 5 | 5 |
| `/manga/read` grouped (100/req) | 5 | 5 |
| `/chapter` (100/req) | 300 (30,000 ids) | ≈ 1–10 (only new ids) |
| **Total** | **≈ 312 → ≈ 104 s** | **≈ 13–22 → ≈ 5–8 s** |

**AniList**, at 20 req/min (3 s spacing):

| Call | First sync | Later syncs |
|---|---|---|
| `Viewer` | 1 | 0 (cached) |
| `MediaListCollection` | 1 | 1 |
| validate tier-2/3 ids not on the list (≈ 25 + 25, 50/page) | 2 | 0 (mappings cached) |
| title search, 50 series (5 aliased/req, ≤ 2 passes) | 10–20 | only new or unmatched series |
| **Dry-run subtotal** | **14–24 → ≈ 42–72 s** | **≈ 1–3** |
| pre-write re-read | 1 | 1 |
| writes, 60 items / 10 per batch | 6 | 6 |
| verify re-read | 1 | 1 |
| **Write phase** | **8 → ≈ 24 s** | **8 → ≈ 24 s** |

Worst case, all 500 need updating: 52 write-phase requests ≈ 2.6 min. Estimate formula shown before approval: `est_requests = 2 + ceil(n_approved / batch)`, `est_seconds = est_requests × 60 / anilist_rpm`.

A test (story 13) runs the whole dry run against a mocked 500-series library, asserts the AniList dry-run count ≤ 24 and MangaDex ≤ 312, and asserts zero AniList requests on a second, unchanged run apart from the single list fetch.

## 12. Test strategy
- **Unit (pure):** normalisation, scoring signals, threshold boundaries (0.92/0.60 exactly, margin edge), progress rules (decimal floor, null, never-lower, exceeds-total, each implausible trigger, hints), estimate.
- **Client:** `respx` routes plus a fake clock/sleep injected into PacedQueue. Cases: spacing between calls, single-flight under concurrent callers, 429 JSON and 429 HTML both pause the queue, the Retry-After/Reset/60 s fallback chain, retry cap, 403 halt, MangaDex token refresh at 13 min and on 401, refresh failure → password grant, and that the User-Agent is always present.
- **Pipeline:** each matching tier, links validation via the list cache (zero requests), FR-16 disagreement → review, tier-1 never re-matched, `links_hash` change → re-match.
- **Writer:** batching, alias mapping, partial errors, complexity halving, resume after a simulated crash mid-run (the exception raised after batch 2 is committed; resume sends only batches 3+), the pre-write drop, verify status-change detection, the first-write guard, the completion alias carrying `status: COMPLETED` only when approved, and status-only items sending no `progress`.
- **Web:** FastAPI `TestClient` per screen (renders, actions change DB). OAuth `state` check.
- **Architecture:** import-boundary test (NFR-1); the log-redaction test (NFR-11); the bind-host test (NFR-10).
- Network is disabled in pytest (`--allow-hosts=127.0.0.1,localhost,::1`; `--disable-socket` breaks the Windows asyncio loop), and respx is run with `assert_all_mocked=True`.
- **Live:** only `scripts/live_check.py` (read-only, run manually), and the first single-entry approved write via the UI.
