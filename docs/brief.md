# Project brief: MangaDex → AniList progress sync

## Problem
I read manga on MangaDex and track it on AniList. Keeping AniList chapter progress current by hand is tedious. Existing tools either write blindly, which risks wrong-series updates, or ignore AniList's rate limits, which risks throttling or an IP block.

## Product
A single-user, local web app (bound to 127.0.0.1). It reads my MangaDex library and read markers, maps each series to an AniList media id, and proposes progress updates as a diff. It writes to AniList only after I approve.

## Fixed decisions (from the requirements conversation)
- One direction, MangaDex → AniList. Nothing is ever written to MangaDex.
- Chapter progress only. AniList score, dates and notes are not touched. Status is not touched either, with **one exception** (user decision, 2026-10-03): when the series is known to be finished and my progress reaches its final chapter, the diff proposes setting status to **Completed**, whatever the current status is. It is shown in the diff and approved like everything else.
- Progress is never lowered. AniList at or ahead of MangaDex → skip.
- Series not on my AniList list appear in the diff as "add" rows (Reading, or Completed when known finished and fully read), approved like any other write. Before writing, the list is re-read and any series that is already on it is never added (that would overwrite the entry). A per-item "add to AniList" action also remains. *(changed by user decision 2026-10-03; originally: not created automatically)*
- Confident matches are automatic. Uncertain ones go to a review queue. A wrong match is worse than a missing one.
- Every sync is a dry run until I approve the diff.

## Success criteria
1. Start the app, click Sync, see exactly what will change, including the AniList request count and estimated duration, approve, done.
2. After approval, AniList progress equals floor(highest read chapter on MangaDex) for every confidently matched series, except entries skipped by the never-lower rule or flagged by the safety rules.
3. A sync never trips a 429 on either API under default settings, and an interrupted sync resumes without repeating writes.

## Constraints that shape the design (detail in `api-notes.md`)
- AniList: effectively 30 req/min; budget 20/min, single-flight and evenly spaced. 429s may come as HTML. Tokens last 1 year with no refresh.
- MangaDex: about 5 req/s per IP; budget 3/s. Escalation goes 429 → 403 IP ban. A real User-Agent is required. Access tokens last 15 min.
- MangaDex read markers come back as chapter *ids*. Turning them into chapter numbers needs one `/chapter` request per 100 ids, so cache ids→numbers permanently.
- `/manga` and `/chapter` hide pornographic and unavailable content by default, so every call must override those filters.

## Environment findings (this machine, 2026-10-03)
- Windows 11. `uv`, Python and git are **not installed**. They must be installed before Phase 6.
- The database defaults to `%LOCALAPPDATA%\mangadex-anilist-sync\sync.db` (configurable).

## Key risks
See `api-notes.md` → "Risks found". User decisions (2026-10-03):
- AniList list-activity posts per update: **accepted**.
- Deleted MangaDex chapters that cannot be resolved to numbers: **accepted** (a count is shown in the diff).
- AniList may still change status or dates server-side on save. The verify step reports it.
