# 13: Orchestrator, dry run up to `diffed`

**Covers:** FR-1, FR-2, FR-4, NFR-6, NFR-16; the state machine in architecture §9 (up to `diffed`), and the arithmetic test in §11.

## Context
This wires 07, 08, 11 and 12 into one run. It ends at `diffed` and has no path to writing (that is story 16).

## Tasks
- `sync/orchestrator.py`: a `SyncOrchestrator` singleton with a global `asyncio.Lock`.
  - `start_run()` → creates `sync_run(state='fetching')` and launches the task. It returns `run_id`, or raises `SyncAlreadyRunning`.
  - Phases: `fetching` (MangaDex fetch, then AniList list fetch), `resolving` (pipeline), `diffing` (rules for each library series → `sync_item` rows), then `diffed` with `est_requests`/`est_seconds` computed over the default-selected items (action = write).
  - Wires the clients' `request_counter` to the run's `req_*` columns.
  - `MangaDexBlocked`/`MangaDexRateLimited`/`AniListUnavailable`/`AniListRateLimited` → `halted` with a user-facing message. Other exceptions → `failed`.
  - `status(run_id)` gives the phase and phase_detail (e.g. "resolving chapters 300/3000") for UI polling.
  - On app startup: runs in `fetching`/`resolving`/`diffing` → `failed` ("interrupted; start a new sync").
  - `discard(run_id)` for a `diffed` run → `cancelled`.

## Acceptance criteria
- [ ] With all APIs mocked, a run reaches `diffed` and creates one `sync_item` per library series (write/skip/flag), with reasons.
- [ ] A second `start_run()` while one is active raises `SyncAlreadyRunning`.
- [ ] `MangaDexBlocked` during fetching → state `halted`, the message mentions a temporary IP ban, and **no AniList request** is made after it.
- [ ] **Arithmetic test:** a generated 500-series library per the §11 assumptions → AniList dry-run requests ≤ 24 and MangaDex ≤ 312. A second unchanged run → AniList exactly 1 request and MangaDex ≤ 13 (no `/chapter`).
- [ ] `sync_run.req_anilist`/`req_mangadex` equal the respx call counts.
- [ ] A restart with a run in `resolving` marks it `failed`.
- [ ] No code path from this module can reach a mutation (the guard test still passes).

## Tests
`test_orchestrator.py`, `test_request_budget.py` (500-series generator in `tests/factories.py`).

## Dev notes
_(fill in after implementation)_
