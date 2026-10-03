# 16: Writer (approve, batched writes, resume, verify, first-write guard)

**Covers:** FR-5, FR-6, FR-7, FR-8, FR-9, NFR-7. Architecture §9 (writing, verifying).

## Context
This is the first code that can change your AniList account. Every safety mechanism converges here. Remove the "no `SaveMediaListEntry`" guard test in this story and replace it with: "`SaveMediaListEntry` appears only in `sync/writer.py` and `sync/add_entry.py`". The only status a sync may write is COMPLETED (FR-25; user decision 2026-10-03).

## Tasks
- `POST /sync/{run_id}/approve` (selected md_ids):
  - Validate: the run is `diffed`; there are no `exceeds_total` items; implausible items require the explicit override checkbox (present in the form).
  - If `first_write_done` is false, the selection must be exactly 1 item; otherwise show "First live write is limited to one entry. Pick one."
  - Set `approved=1, write_state='pending'`, and `status_approved=1` where the "mark completed" box was submitted checked. A status-only row with the box unchecked has nothing to send and is not approved. Then start writing.
- `sync/writer.py`, per §9 steps 1–6:
  - the pre-write re-read and drop, storing `al_status_before` on each item;
  - aliased batch documents with variables `$e0:Int $p0:Int …`;
  - selection `{id mediaId progress status}`;
  - commit per batch;
  - per-alias error mapping (GraphQL `errors[].path[0]` → alias);
  - complexity halving persisted to `anilist_write_batch`.
- Verifying per §9. When the first-ever write verifies, set `first_write_done=true`.
- Resume: `POST /sync/{run_id}/resume` for runs in `writing`/`verifying`/`failed`-in-writing. It is shown on the dashboard.
- The diff page's approve button is enabled. During writing, the status fragment shows "batch k/n, next request in ~3 s".

## Acceptance criteria
- [x] **Never lower, re-checked:** an approved item whose pre-write re-read shows AniList ≥ proposed is `dropped`, and no mutation is sent for it.
- [x] Mutations pass the entry `id`, plus `progress` for progress changes, plus `status` only for approved completions. A test inspects every sent document and its variables:
  - no `score`, `notes`, dates, `repeat`, `private` or `mediaId`;
  - any `status` value is exactly `COMPLETED`;
  - status-only items carry no `progress`;
  - an item with "mark completed" unchecked carries no `status`.
- [x] Pre-write re-read: a status-only item whose entry is already COMPLETED is `dropped`. A progress+completion item whose AniList progress already reached the total becomes status-only.
- [x] Verify expects COMPLETED for sent completions and `al_status_before` for everything else.
- [x] 25 pending items at batch 10 → 3 mutation requests, aliases mapped back correctly.
- [x] **Resume:** simulate a crash (exception) after batch 2 commits. Resume sends only the remaining 5 items, and the total mutation requests across both runs = 3.
- [x] A complexity error on a batch of 10 → retried as 5 + 5. `anilist_write_batch` is now 5. Items end `done`.
- [x] A partial error on alias `m3` → that item is `failed` with the message, and the others are `done`.
- [x] A 429 during writing pauses and resumes per story 04, and no item is double-written.
- [x] Verify flags a status change (CURRENT → COMPLETED in the mocked re-read) in `verify_note`.
- [x] With `first_write_done=false`, approving 2 items is rejected, and approving 1 writes exactly one mutation. After verify, `first_write_done=true`.
- [x] Writing makes exactly `2 + ceil(n/batch)` AniList requests when there are no errors (it matches the estimate).

## Tests
`test_writer.py`, `test_web_approve.py`; the updated `test_architecture.py`.

## Dev notes
- Done 2026-10-03; 340 tests pass in total (34 new). **No live write has been made**: the first one is yours, from the diff screen, one entry.
- The status is written as the enum literal `status: COMPLETED` in the document, never as a variable, so no other value can be sent. Variables are only `$eN: Int` (entry id) and `$pN: Int` (progress). `test_architecture.py` now asserts that `SaveMediaListEntry`/`mutation` appear only in `sync/writer.py` and `sync/add_entry.py`, and that the writer contains no other status literal and no `mediaId:` argument.
- Retrying is safe: `SaveMediaListEntry(id, progress: N)` sets absolute values, so the client's 5xx/network retry cannot double-count. A 429 means nothing executed.
- Pre-write re-read: stores `al_status_before` and refreshes `al_progress` to AniList's current value. Drops: entry gone; progress item now at/above the proposal (unless an approved completion still applies at exactly the total → becomes status-only); status-only item already COMPLETED or progress moved. If the entry became COMPLETED meanwhile, the status part is dropped (`status_approved=0`) and only progress is written. Drop/failure reasons go in `verify_note` ("dropped: …", "write failed: …").
- Per-alias errors map `errors[].path[0]` → alias. An alias with neither data nor a path-specific error is `failed` with the document-level message (never left pending, so resume cannot re-send something whose outcome is unknown).
- Complexity: halve (min 1), persist `anilist_write_batch`, retry the same items. At batch size 1 the error is raised (run `failed`).
- Approval (`POST /sync/{id}/approve`, fields `sel`, `mc` = mark completed, `ov` = override for implausible): only from `diffed`; rejects skip and `exceeds_total` rows; implausible rows need `ov`; status-only rows without `mc` are not approved. Before the first write: exactly one item, and the diff pre-selects nothing.
- Verify: progress must equal what was sent (or be unchanged for status-only); status must be COMPLETED if sent, else `al_status_before`; otherwise "status changed by AniList: X→Y". The run summary counts items that need a look. Completion dates are not re-read (the list query has no dates); this stays informational.
- Resume (`POST /sync/{id}/resume`; dashboard button): runs in writing/verifying (after a restart), or failed/halted after approval, with pending or done items. It re-reads, sends only `pending`, then verifies.
- During writing the status fragment shows "batch k/n, next request in ~3 s".
