# 19: QA sweep and README

**Covers:** the Phase 7 list in the build prompt; NFR-13.

## Tasks
1. Build a coverage matrix in this file. For each required scenario, name the test that proves it, and add any that are missing:

| Scenario | Test(s) |
|---|---|
| AniList 429 → queue pause (JSON + HTML) | story 04 |
| MangaDex 429 → queue pause + 403 halt | story 05 |
| MangaDex token refresh (time + 401) | story 05 |
| Resume after interruption | story 16 |
| Never lower progress (diff + pre-write re-check) | stories 12, 16 |
| Decimal and null chapter numbers | story 12 |
| Each matching tier (1, 2, 3, 4) | story 11 |
| Threshold boundaries | story 10 |
| Exceeds total, implausible flags, status hints | story 12 |
| Auto-complete: only when known finished, only COMPLETED ever sent, opt-out per row | stories 12, 16 |
| Request budget for 500 series | story 13 |
| No secrets in logs / HTML | stories 01, 06, 18 |
| No live network in tests | story 01 |

2. Run `uv run pytest --cov=mdal`. Report coverage, and that each `sync/` and `clients/` module is ≥ 90%.
3. `README.md`:
   - prerequisites (uv, git; Windows notes; OneDrive warning);
   - MangaDex personal API client registration (settings → API Clients; may be pending staff approval);
   - AniList app registration (Settings → Developer → Create New Application; redirect `http://127.0.0.1:8765/auth/anilist/callback`, or the pin URL; apps cannot be deleted);
   - filling `.env`;
   - running `scripts/live_check.py`;
   - the first-run walkthrough (connect → sync → review queue → approve exactly one entry → check it on AniList → normal use);
   - the rate-limit settings and what not to change;
   - troubleshooting (403 from MangaDex = wait; 429 behaviour; reconnecting AniList after a year).

## Acceptance criteria
- [ ] Every row in the matrix names an existing, passing test.
- [ ] The full suite passes with network disabled.
- [ ] Following the README on a clean machine needs no step that is not written down.

## Dev notes
_(fill in after implementation)_
