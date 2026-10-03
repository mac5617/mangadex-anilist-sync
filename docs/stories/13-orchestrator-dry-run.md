# 13: Orchestrator, dry run up to `diffed`

**Covers:** FR-1, FR-2, FR-4, NFR-6, NFR-16; the state machine in architecture §9 (up to `diffed`), and the arithmetic test in §11.

## Context
This wires 07, 08, 11 and 12 into one run. It ends at `diffed` and has no path to writing (that is story 16).

## Tasks
- `sync/orchestrator.py`: a `SyncOrchestrator` singleton with a global `asyncio.Lock`.
  - `start_run()` → creates `sync_run(state='fetching')` and launches the task. It returns `run_id`, or raises `SyncAlreadyRunning`.
  - Phases: `fetching` (MangaDex fetch, then AniList list fetch), `resolving` (pipeline), `diffing` (rules for each library series → `sync_item` rows), then `diffed` with `est_requests`/`est_seconds` computed over the default-selected items (action = write).
  - Owns the client singletons (`AniListClient`, `MangaDexClient`), built with PacedQueues from the DB tunables (moved here from stories 04/05), and wires their `request_counter` to the run's `req_*` columns.
  - `MangaDexBlocked`/`MangaDexRateLimited`/`AniListUnavailable`/`AniListRateLimited` → `halted` with a user-facing message. Other exceptions → `failed`.
  - `status(run_id)` gives the phase and phase_detail (e.g. "resolving chapters 300/3000") for UI polling.
  - On app startup: runs in `fetching`/`resolving`/`diffing` → `failed` ("interrupted; start a new sync").
  - `discard(run_id)` for a `diffed` run → `cancelled`.

## Acceptance criteria
- [x] With all APIs mocked, a run reaches `diffed` and creates one `sync_item` per library series (write/skip/flag), with reasons.
- [x] A second `start_run()` while one is active raises `SyncAlreadyRunning`.
- [x] `MangaDexBlocked` during fetching → state `halted`, the message mentions a temporary IP ban, and **no AniList request** is made after it.
- [x] **Arithmetic test:** a generated 500-series library per the §11 assumptions → AniList dry-run requests ≤ 24 and MangaDex ≤ 312. A second unchanged run → AniList exactly 1 request and MangaDex ≤ 13 (no `/chapter`).
- [x] `sync_run.req_anilist`/`req_mangadex` equal the respx call counts.
- [x] A restart with a run in `resolving` marks it `failed`.
- [x] No code path from this module can reach a mutation (the guard test still passes).

## Tests
`test_orchestrator.py`, `test_request_budget.py` (500-series generator in `tests/factories.py`).

## Dev notes
- Done 2026-10-03; 267 tests pass in total (13 new).
- Budget test (500 series, §11 assumptions): first run AniList 13 (Viewer 1, list 1, id_in 1, search 10) and MangaDex 312; second run AniList 1, MangaDex 11 (token still valid, no `/chapter`).
- The client singletons stay in `Services` (one per process since story 06); the orchestrator is `services.orchestrator` and borrows them. It wires each client's `request_counter` to the run's `req_*` columns for the duration of a run, then unsets it.
- Series with an auto/confirmed match that are **not on the AniList list** get a `skip` row with reason "not on your AniList list" (so every library series has exactly one row); story 17 lists them from `mapping` + `al_entry`.
- Mapping states without a usable match produce `skip` rows: review → "awaiting match review", unmatched → "no match", not_on_anilist → "marked not on AniList".
- `status_approved` defaults to 1 whenever `set_status` is proposed (the "mark completed" checkbox is checked by default).
- `est_requests`/`est_seconds` are computed over `action='write'` items.
- `AniListAuthError` → `failed` with "reconnect AniList"; `MangaDexAuthError` → `failed` with "check credentials".
- The app's lifespan calls `recover_interrupted()` on startup.
- New repo helper: `replace_items(run_id, rows)` (one transaction).
