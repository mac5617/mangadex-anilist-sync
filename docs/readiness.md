# Readiness check (Phase 5)

Re-read `prd.md`, `architecture.md` and `stories/*` together on 2026-10-03.

## Traceability: every requirement has a story
| Req | Stories | Req | Stories |
|---|---|---|---|
| FR-1 | 13, 14 | NFR-1 | 04, 05 (guard test) |
| FR-2 | 13 | NFR-2 | 03, 04 |
| FR-3 | 14 | NFR-3 | 04 |
| FR-4 | 12, 13, 14 | NFR-4 | 04, 13 |
| FR-5 | 14, 16 | NFR-5 | 03, 05 |
| FR-6 | 02, 16 | NFR-6 | 13 |
| FR-7 | 16 | NFR-7 | 16 |
| FR-8 | 16 | NFR-8 | 05 |
| FR-9 | 16, 17 | NFR-9 | 01, 06 |
| FR-10, 11 | 07 | NFR-10 | 01 |
| FR-12 | 05, 07 | NFR-11 | 01 (+04, 05, 06 log checks) |
| FR-13 | 08 | NFR-12 | 06 |
| FR-14–16 | 11 | NFR-13 | 01, 19 |
| FR-17, 18 | 10 | NFR-14 | 09 |
| FR-19, 20 | 11, 15 | NFR-15 | 01 |
| FR-21–26 | 12 (+14 display) | NFR-16 | 13, 18 |
| FR-27 | 14, 16 (resume) | | |
| FR-28 | 14, 16 | | |
| FR-29 | 15 | | |
| FR-30 | 17 | | |
| FR-31 | 18 | | |
| FR-32 | 06, 18 | | |

No requirement is without a story.

## Contradictions found and fixed
1. **Request budget vs re-matching.** Architecture §7 re-tried review/unmatched series on every sync, so the story 13 test ("unchanged second run = 1 AniList request") could not pass. Fix: review/unmatched results are cached like auto ones, and a "retry matching" action was added (architecture §7, FR-19, stories 11 and 15).
2. **Outlier rule with a single read.** Undefined. Fix: the rule applies only with ≥ 2 distinct numeric reads (architecture §8; story 12 rows added). Story 12 also had a self-contradicting example row, now corrected.
3. **Verify needs the pre-write status**, but the schema had no column for it. Added `sync_item.al_status_before` (architecture §4, story 16).
4. **`sync/add_entry.py`** was used by story 17 but missing from the layout. Added to §2. First-write guard coverage for "add" stated in §9.
5. **The estimate after a subset selection** was not recomputed. Added a local `/estimate` endpoint (architecture §9, story 14).

## Open questions: resolved by the user (2026-10-03)
1. **AniList activity feed:** leave it on. No change.
2. **Database and `.env`:** the DB defaults to `%LOCALAPPDATA%`. The user's `DEV/.env` (6 credential keys set) moves into the project root in story 01.
3. **"Add to AniList" status:** a per-row dropdown is fine. **Amendment:** when a series is known finished and progress is at its final chapter, mark it Completed automatically, whatever the current status. Implemented as FR-25 (rewritten), architecture §8 step 5 and §9, and stories 12, 14, 16, 17 and 19. Safeguards:
   - "known finished" requires AniList media status FINISHED;
   - it is proposed in the diff and opt-out per row;
   - COMPLETED is the only status value ever sent.
4. **Implausible-flag override:** confirmed as designed.
5. **User-Agent:** app name and version only; `USER_AGENT_CONTACT` removed.
6. **Deleted chapters:** accepted as designed.

## Re-check after the amendment
- FR-25 → stories 12 (rules), 14 (display + opt-out), 16 (sending + verify), 17 (default dropdown), 19 (QA row). There are no orphans.
- The schema gains `set_status`, `status_source` and `status_approved` on `sync_item`. A CHECK constraint limits `set_status` to COMPLETED.
- The request estimate is unchanged: a status change rides in the same aliased mutation as the progress change.

## Items that will be verified live (story 09, read-only)
These are AniList perPage 50, aliased `Page` roots, the rough complexity cost of a 10-alias document, the MangaDex `/manga/read` ids[] cap, and whether the `127.0.0.1` OAuth redirect is accepted (story 06; pin-flow fallback ready). Server-side status changes on a progress save are learned from the first single approved write (story 16).

## Verdict
All questions are answered. Ready to start Phase 6 once `uv` and git are installed.
