# 09: Live read-only check script (first live contact)

**Covers:** NFR-14; resolves the **Unverified** items in `api-notes.md`.

## Context
The prompt requires that the first live AniList test is read-only. This script is also the only place that probes undocumented behaviour. The **user** runs it, with their credentials; it is never run in pytest.

## Tasks
`scripts/live_check.py` (`uv run python scripts/live_check.py`) uses the real clients at their default budgets and prints a report. It performs **only reads**, in this order, at most about 15 AniList and about 10 MangaDex requests:
1. AniList `Viewer`. MangaDex token grant plus `/manga/status` (prints the library size only).
2. AniList `MediaListCollection` → entry count, list names, the custom-list-only count.
3. AniList `Page(perPage:50){media(id_in:[…first 50 list ids…])}` → confirms perPage 50 is accepted (or reports the cap).
4. AniList aliased search: 5 aliased `Page` roots in one document → confirms aliases are allowed and reports any complexity error.
5. A single read-only document with 10 aliased `Media(id:…)` roots shaped like the write batch (the same number of root fields) → a rough complexity signal for batch size 10.
6. MangaDex `/manga/read?grouped=true` with 100 library ids (or all, if fewer) → confirms the ids[] cap.
7. MangaDex `/chapter` with up to 100 of those read ids → confirms field shapes and the `includeUnavailable` effect (count with vs without the parameter: 1 extra request).
8. It prints the `X-RateLimit-*` headers it saw, never tokens.

Results go into `docs/api-notes.md` under "Live check YYYY-MM-DD" (manually, by the developer, from the printed report).

## Acceptance criteria
- [x] The script contains no `mutation` keyword (enforced by a test that greps it).
- [x] With `--dry` it prints the planned requests without sending anything (tested).
- [x] It refuses to run unless `.env` has the required keys, and prints which are missing (names only).
- [x] It uses the shared clients (no direct `httpx`; the architecture test covers `scripts/` too).

## Tests
`test_live_check_static.py` (grep for `mutation`; `--dry` output; missing-env message).

## Dev notes
- Script done 2026-10-03; 114 tests pass in total. **Live run pending: the user runs it.** Record the report in `api-notes.md`.
- Prerequisite: AniList must be connected first (the app's Settings → Connect AniList stores `ANILIST_ACCESS_TOKEN`). Without it, the script exits with code 2 and lists the missing key names. Verified against the real `.env`.
- It makes 5 AniList and 5 MangaDex requests, at the default budgets (about 15 s on the AniList side). It uses `Services` and the real paced clients, never `httpx` directly.
- Step 5 uses 10 aliased `Media(id)` read roots as a rough complexity signal. The real write-batch check is still the halve-on-complexity logic in story 16.
- New: `AniListClient.last_rate_headers` (X-RateLimit-* / Retry-After of the last response), for diagnostics only and never used for pacing.
- The safety test greps the script for `mutation`, `SaveMediaListEntry` and any `.post/.put/.delete/.patch(` call. Keep those words out of the script, even in comments.
- `FakeAniList` now also answers aliased `Media(id: N)` roots and defaults `perPage` to 50.
