# 12: Progress rules and estimate (pure)

**Covers:** FR-4, FR-21–FR-26. Architecture §8, §11 (formula).

## Context
This is the core safety logic. It is pure and table-tested. Read `architecture.md` §8 for the exact rule order.

## Tasks
- `sync/rules.py`: `evaluate(read_chapters: list[str|None], unresolved: int, entry: AlEntry|None, media: AlMediaInfo(chapters, status), md: MdInfo(pub_status, last_chapter, chapter_numbers_reset), jump_limit: int) -> DiffItem` (action, flag_kind, reason, hint, md_progress, al_progress, set_status, status_source). Follow architecture §8 steps 1–10 exactly, including the completion check.
- `completion_default(…)`: the same completion check, exposed for story 17's dropdown default.
- `sync/estimate.py`: `estimate(n_items, batch, rpm) -> (requests, seconds)` = `2 + ceil(n/batch)`, `× 60/rpm`. `n=0` gives `(0, 0)`, because there is nothing to write and no re-read is needed.

## Acceptance criteria (each a row in a parametrised test)
- [x] reads `["10.5","3"]`, AL 5 → write 10.
- [x] reads `["10.9"]` → 10 (floor, not round).
- [x] reads `[None, "7"]` → 7, with the null counted in `unresolved`.
- [x] reads `[None]` → skip "nothing read".
- [x] reads `["abc","4"]` → 4, with 1 unresolved.
- [x] AL 12, MD 12 → skip. AL 15, MD 12 → skip (never lower).
- [x] chapters 100, MD 101 → flag `exceeds_total`, with `set_status` NULL.
- [x] **Completion:**
  - media FINISHED, chapters 100, AL CURRENT 90, MD 100 → write progress 100, `set_status='COMPLETED'`, `status_source='AniList'`.
  - Same with AL status PAUSED, DROPPED, PLANNING or REPEATING → `set_status='COMPLETED'`.
  - AL already COMPLETED → `set_status` NULL.
- [x] **Status-only:** media FINISHED, chapters 100, AL CURRENT 100, MD 100 → write, `set_status='COMPLETED'`, reason "at final chapter; mark completed". AL CURRENT 100, MD 95 → skip (MangaDex does not confirm).
- [x] **No completion when AniList isn't finished:** media RELEASING, chapters 100, MD 100 → write progress, `set_status` NULL, hint "at last known chapter, but AniList lists the series as RELEASING; status unchanged".
- [x] **MangaDex total fallback:** media FINISHED, chapters null, MD pub_status completed, last_chapter "120.5" → total 120; MD 120 → `set_status='COMPLETED'`, `status_source='MangaDex'`. MD 125 → write 125, no completion, hint "reads go beyond MangaDex's last chapter 120". MD pub_status ongoing → no completion.
- [x] Media FINISHED, chapters null, MangaDex last_chapter null → no completion.
- [x] An implausible-flagged row still carries `set_status` when the check holds (it is sent only if the user overrides).
- [x] chapters null, MD 500 → no total check.
- [x] `chapter_numbers_reset` → flag `implausible`.
- [x] reads `["1","2","3","150"]` → flag `implausible` (150 > 2×3 and 150 − 3 > 20).
- [x] reads `["1","2","40"]` → flag `implausible` (40 > 2×2 and 38 > 20).
- [x] reads `["30","31","45"]` → write (45 < 2×31).
- [x] reads `["1","150","151","152"]` → write (only the *second-highest* is compared, so an early stray read never flags).
- [x] reads `["12"]` (single read) → write (no second-highest → outlier rule not applied).
- [x] AL 10, MD 300, jump_limit 200 → flag. AL 0, MD 300 → write.
- [x] status PLANNING and not completing → hint "status is Planning; will remain Planning".
- [x] `estimate(60, 10, 20)` == `(8, 24)`; `estimate(0, 10, 20)` == `(0, 0)`.

## Tests
`test_rules.py`, `test_estimate.py`.

## Dev notes
- Done 2026-10-03; 254 tests pass in total (49 new).
- `evaluate` returns `action='not_on_list'` for §8 step 4 (no AniList entry). The orchestrator does not store those as `sync_item` rows; they feed the not-on-my-list screen (story 17).
- Chapter strings that are empty, non-numeric, NaN/Infinity or negative count as unresolved.
- The "at last known chapter … status unchanged" hint uses the best known total whatever the AniList status (AniList `chapters`, else MangaDex `last_chapter` when MangaDex says completed).
- Skip rows never carry `set_status`; write/flag rows carry it whenever the completion check holds (an `implausible` flag keeps it, an `exceeds_total` flag cannot have it).
- `DiffItem.total` carries the known total, for `status_change_label()` ("Reading → Completed (AniList: finished, 120 ch)").
- `estimate()` rounds seconds up and treats a batch size of 0 as 1.
