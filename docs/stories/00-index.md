# Stories: build order

Read `architecture.md` before any story. Each story ends with a "Dev notes" section that the Developer fills in after implementing.

| # | Epic | Story | Touches AniList writes? |
|---|---|---|---|
| 01 | A Foundation | project skeleton, config, logging redaction, git | no |
| 02 | A Foundation | SQLite schema + repo | no |
| 03 | B Clients | PacedQueue | no |
| 04 | B Clients | AniList client (read path + error model) | no |
| 05 | B Clients | MangaDex client + token management | no |
| 06 | B Clients | Auth setup: AniList OAuth, MangaDex credential check | no (OAuth only) |
| 07 | C Fetch | MangaDex library fetch + chapter cache | no |
| 08 | C Fetch | AniList list fetch + bulk lookups + batched search | no |
| 09 | C Fetch | Live read-only check script (**first live contact**) | no |
| 10 | D Matching | Normalisation + scoring (pure) | no |
| 11 | D Matching | Matching pipeline (tiers 1–4) | no |
| 12 | E Diff | Progress rules + estimate (pure) | no |
| 13 | E Diff | Orchestrator: dry run to `diffed` | no |
| 14 | F UI | Dashboard + diff screen (approve disabled) | no |
| 15 | F UI | Match review queue | no |
| 16 | G Write | Writer: approve, batched writes, resume, verify, first-write guard | **yes** |
| 17 | G Write | Not-on-my-list + add to AniList | **yes** |
| 18 | F UI | Sync history + settings screens | no |
| 19 | H QA | QA sweep + README | no |

Epic A–F code cannot write to AniList. Story 04's client exposes `graphql()` generically, and story 04 adds a guard test: before story 16, no module contains the string `SaveMediaListEntry`. Story 16 removes the guard.
