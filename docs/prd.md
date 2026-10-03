# PRD: MangaDex → AniList progress sync

Inputs: `brief.md`, `api-notes.md`. Trace codes: **P:<section>** = the original build prompt (sections: goal, decisions, rate, matching, progress, security, working); **A:<n>** = an Analyst finding (`api-notes.md`, item or section named).

## Functional requirements

### Sync lifecycle
- **FR-1** The user starts a sync from the dashboard with one click. Only one sync runs at a time. *(P:goal)*
- **FR-2** A sync runs fetch → resolve → diff and then stops in "awaiting approval". Nothing is written to AniList before approval. *(P:decisions, dry run)*
- **FR-3** The diff shows each series: MangaDex title, AniList title (linked), current AniList progress → proposed progress, and the action (write / skip / flagged) with its reason. *(P:goal)*
- **FR-4** Before approval, the diff shows how many AniList requests the write phase will make and its estimated duration at the configured budget. *(P:rate)*
- **FR-5** The user approves all or a selected subset. Flagged items are unselected by default. Implausible-jump flags may be overridden per item. The above-total-chapters flag can never be overridden. *(P:progress; decision recorded here)*
- **FR-6** The write phase records each successful write in SQLite immediately. An interrupted sync can be resumed and skips items already written. *(P:rate, resumable)*
- **FR-7** Immediately before writing, the app re-reads the AniList list (one request) and drops any approved item where AniList is now at or ahead of the proposed value. *(P:decisions never-lower; decision: protects against a stale diff)*
- **FR-8** After writing, a verify step re-reads the AniList list. It reports any entry whose progress differs from what was written, and any entry whose **status** changed. *(P:workflow state machine; A:Mutations side effects)*
- **FR-9** ~~The very first live write is restricted to exactly one entry.~~ Removed by user decision 2026-10-03: write and add rows are pre-selected in every diff (flagged rows are not), and any approval size is allowed from the start. *(P:working, amended)*

### Data reading
- **FR-10** Reads the MangaDex library (every reading status) via `/manga/status`, plus manga details and grouped read markers in batches of 100. *(P:api_facts; A:MangaDex endpoints)*
- **FR-11** Resolves read chapter ids to chapter numbers via `/chapter`, using a permanent SQLite cache so only unseen ids are requested. *(A:Risk 1)*
- **FR-12** Every MangaDex list call sends `limit=100`, all four `contentRating[]` values and, for `/chapter`, `includeUnavailable=1`. *(A:MangaDex endpoints)*
- **FR-13** Reads the full AniList manga list with one `MediaListCollection` request per sync. It iterates all lists, including custom lists, and de-duplicates by entry id. *(P:rate; A:Queries)*

### Matching
- **FR-14** Resolution order, first success wins: (1) stored user decision; (2) `links.al`; (3) `links.mal` → `idMal`; (4) scored title search. *(P:matching)*
- **FR-15** Tier 2–3 ids are validated in bulk to exist with type MANGA. Ids already present in the user's AniList list count as validated without an extra request. A failed validation falls through to the next tier. *(P:matching; decision: fewer requests)*
- **FR-16** Tier 2–3 matches go to review instead of auto-accept when: the AniList format is NOVEL; or `links.al` and `links.mal` resolve to different media. *(P:matching "when signals disagree")*
- **FR-17** Title scoring compares the MangaDex title and all alt titles against AniList romaji/english/native/synonyms after normalisation (case, punctuation, full/half width, diacritics). It adds signals: start year, country vs original language, format, and author. The output is a 0–1 confidence plus recorded reasons. *(P:matching)*
- **FR-18** Thresholds are configurable. Defaults: ≥ 0.92 with margin ≥ 0.05 over the runner-up and no disagreeing signal → auto. 0.60 ≤ score < 0.92, a close runner-up, or a disagreeing signal → review. < 0.60 → unmatched. *(P:matching)*
- **FR-19** Automatic, review and unmatched results are cached and reused on later syncs. They are re-evaluated only if the series' MangaDex `links` change, or when the user clicks "retry matching". *(decision: fewer requests)*
- **FR-20** Tier 1 decisions (confirmed, corrected, "not on AniList") are never re-matched. *(P:matching)*

### Progress rules
- **FR-21** Progress = floor(max numeric chapter among read chapters); null chapter numbers are ignored. Non-numeric strings are ignored and counted. *(P:progress)*
- **FR-22** Never lower progress; AniList ≥ proposed → skip. *(P:decisions)*
- **FR-23** If AniList knows the total chapters and proposed > total → flag "exceeds total" and do not write. *(P:progress)*
- **FR-24** Flag implausible values (overridable). Triggers: the series has `chapterNumbersResetOnNewVolume`; the highest read chapter is an outlier (> 2× and > 20 above the next-highest read chapter); or the jump over a non-zero AniList progress exceeds a configurable amount (default 200). *(P:progress; A:Manga attributes)*
- **FR-25** Status is never changed, except to set **COMPLETED**. That is proposed when the series is known to be finished (AniList media status FINISHED, with a total from AniList `chapters`, or from MangaDex `lastChapter` when the MangaDex status is completed) and proposed progress equals that total, from any current status. This includes status-only items, where progress already equals the total. In the diff it shows as its own approvable part of the row ("mark completed", checked by default). If AniList does not say FINISHED, no completion is proposed, and a hint explains why. COMPLETED is the only status value ever written by a sync. *(P:progress, amended by user decision 2026-10-03)*
- **FR-26** The diff shows, per series, how many read chapter ids could not be resolved (deleted or null). *(A:Risk 2)*

### Screens
- **FR-27 Dashboard:** auth status for both services, last sync summary, Sync button, live phase/progress of a running sync, a Resume button when a sync was interrupted, and counts for the review queue and not-on-my-list items. *(P:workflow screens)*
- **FR-28 Sync diff / approval:** FR-3, FR-4, FR-5, FR-26, filters by action. *(P:workflow screens)*
- **FR-29 Match review queue:** each MangaDex series beside its top candidates (title, cover, year, format, country, score + reasons, links to both sites). Actions: accept a candidate; paste an AniList URL or id (validated as MANGA); mark "not on AniList". *(P:matching)*
- **FR-30 Not on my list:** matched series absent from the AniList list, with a per-item "add to AniList". The add sends a status chosen in the row (default Completed when the FR-25 completion check holds, else Reading) and progress = proposed progress. Same rate-limited client; one request per add. *(P:decisions)*
- **FR-31 Sync history:** past runs with timestamps, phase reached, counts, request counts per API, errors; drill into a run's items. *(P:workflow screens)*
- **FR-32 Settings:** rate budgets, batch sizes, thresholds, jump limit, database path (read-only display); AniList connect/disconnect; MangaDex credential check. Secrets are never displayed. *(P:workflow screens)*

## Non-functional requirements

### Rate limiting (highest priority)
- **NFR-1** Exactly one client object per API. Every call goes through it. A test asserts that nothing outside the client modules imports `httpx`. *(P:rate)*
- **NFR-2** AniList: single-flight, evenly spaced; default 20 req/min. `X-RateLimit-Remaining` is never used to speed up. *(P:rate)*
- **NFR-3** AniList 429 (JSON or HTML body): pause the whole queue for `Retry-After` (else `X-RateLimit-Reset − now`, else 60 s) + 5 s margin. At most 3 retries per request. *(P:rate; A:Rate limits)*
- **NFR-4** An AniList response that says the API is disabled, or a 403, halts the sync with a message. *(A:Rate limits)*
- **NFR-5** MangaDex: single-flight, evenly spaced; default 3 req/s. On 429, pause all MangaDex traffic with backoff (honour `X-RateLimit-Retry-After`/`Retry-After`, else 10 s → 30 s → 60 s). A 4th consecutive 429 halts. On 403, halt immediately and tell the user. *(P:rate; A:Rate limits)*
- **NFR-6** Request minimisation is part of the design. Request counts are bounded per `architecture.md` §Request arithmetic, and a test asserts the bound for a mocked 500-series library. *(P:rate)*
- **NFR-7** Aliased write batches default to 10. A complexity error halves the batch and retries; the learned size is persisted. *(P:rate; A:Mutations)*
- **NFR-8** MangaDex access token refreshed proactively at 13 minutes and on any 401 (once); password grant if refresh fails. *(A:Auth)*

### Security
- **NFR-9** Secrets live only in `.env` (git-ignored from the first commit). `.env.example` is provided. *(P:security)*
- **NFR-10** The server binds to 127.0.0.1 only, and this is not configurable to 0.0.0.0 without code change. *(P:security)*
- **NFR-11** A logging filter redacts tokens, passwords and client secrets. A test proves a token never appears in log output. *(P:security)*
- **NFR-12** The OAuth callback checks a `state` parameter. *(decision: standard CSRF protection for a local callback)*

### Quality
- **NFR-13** Tests never call live APIs (respx; real network is disabled in pytest). *(P:QA)*
- **NFR-14** The first live AniList contact is a read-only check script. *(P:working)*
- **NFR-15** SQLite defaults to a path outside OneDrive. *(brief: environment)*
- **NFR-16** Each sync writes a per-run request count for each API to history. *(supports NFR-6, FR-31)*
