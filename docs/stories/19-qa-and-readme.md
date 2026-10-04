# 19: QA sweep and README

**Covers:** the Phase 7 list in the build prompt; NFR-13.

## Tasks
1. Build a coverage matrix in this file. For each required scenario, name the test that proves it, and add any that are missing:

| Scenario | Test(s) |
|---|---|
| AniList 429 → queue pause (JSON + HTML) | `test_anilist_client.py::test_429_json_with_retry_after_pauses_35s`, `::test_429_html_without_headers_pauses_65s`, `::test_429_with_only_reset_header_pauses_45s`, `::test_pause_blocks_concurrent_caller`; during writing: `test_writer.py::test_429_during_writing_retries_without_double_write` |
| MangaDex 429 → queue pause + 403 halt | `test_mangadex_client.py::test_429_with_retry_after_unix_time`, `::test_429_backoff_ladder_then_halt`, `::test_403_on_data_endpoint_halts_immediately`; run level: `test_orchestrator.py::test_mangadex_ban_halts_before_any_anilist_request` |
| MangaDex token refresh (time + 401) | `test_mangadex_client.py::test_refresh_after_13_minutes`, `::test_401_refreshes_once_and_falls_back_to_password` |
| Resume after interruption | `test_writer.py::test_resume_after_crash_sends_only_remaining`, `::test_restart_while_writing_can_resume`; `test_web_approve.py::test_resume_button_and_endpoint`; `test_orchestrator.py::test_restart_marks_interrupted_runs_failed` |
| Never lower progress (diff + pre-write re-check) | `test_rules.py::test_progress_table` (AL 12/15 vs MD 12 rows), `::test_skip_reason_never_lower`; `test_writer.py::test_never_lower_recheck_drops_without_mutation` |
| Decimal and null chapter numbers | `test_rules.py::test_progress_table`, `::test_nothing_read`, `::test_parse_chapter` |
| Each matching tier (1, 2, 3, 4) | `test_pipeline.py::test_confirmed_is_never_rematched_even_with_changed_links` (1), `::test_links_al_on_list_is_auto_with_zero_requests` (2), `::test_links_al_absent_from_anilist_falls_to_tier3` (3), `::test_tier4_auto`/`::test_tier4_review_on_disagreement`/`::test_tier4_unmatched_keeps_low_candidates` (4) |
| Threshold boundaries | `test_score.py::test_classification_boundaries`, `::test_margin_exactly_at_threshold_is_auto`, `::test_close_runner_up_goes_to_review` |
| Exceeds total, implausible flags, status hints | `test_rules.py::test_exceeds_total`, `::test_outlier`, `::test_chapter_numbers_reset_flags`, `::test_jump_limit`, `::test_planning_hint`, `::test_no_completion_when_anilist_releasing`, `::test_reads_beyond_mangadex_total` |
| Auto-complete: only when known finished, only COMPLETED ever sent, opt-out per row | `test_rules.py::test_completion_from_any_status`, `::test_no_completion_when_anilist_releasing`, `::test_mangadex_total_fallback`, `::test_no_total_anywhere_no_completion`; `test_writer.py::test_documents_contain_only_allowed_fields` (unticked row has no status; status literal only COMPLETED), `::test_status_only_without_completion_is_not_approved`; `test_architecture.py::test_writer_never_sends_other_statuses` |
| Request budget for 500 series | `test_request_budget.py::test_500_series_budget` |
| No secrets in logs / HTML | `test_logsetup.py` (all), `test_anilist_client.py::test_token_never_logged`, `test_mangadex_client.py::test_secrets_never_logged`, `test_auth_routes.py::test_no_secrets_in_logs`, `::test_settings_page_hides_secrets`, `test_web_settings.py::test_no_secret_values_in_html`, `::test_no_secret_values_on_any_page` |
| No live network in tests | `test_no_network.py::test_outbound_connection_is_blocked` (pytest `--allow-hosts` loopback only); every respx router uses `assert_all_mocked=True`; `test_web_diff.py::test_every_page_makes_no_api_calls` |

2. Run `uv run pytest --cov=mdal`. Report coverage, and that each `sync/` and `clients/` module is ≥ 90%.
3. `README.md`:
   - prerequisites (uv, git; Windows notes);
   - MangaDex personal API client registration (settings → API Clients; may be pending staff approval);
   - AniList app registration (Settings → Developer → Create New Application; redirect `http://127.0.0.1:8765/auth/anilist/callback`, or the pin URL; apps cannot be deleted);
   - filling `.env`;
   - running `scripts/live_check.py`;
   - the first-run walkthrough (connect → sync → review queue → approve exactly one entry → check it on AniList → normal use);
   - the rate-limit settings and what not to change;
   - troubleshooting (403 from MangaDex = wait; 429 behaviour; reconnecting AniList after a year).

## Acceptance criteria
- [x] Every row in the matrix names an existing, passing test.
- [x] The full suite passes with network disabled.
- [x] Following the README on a clean machine needs no step that is not written down.

## Dev notes
- Done 2026-10-03; 376 tests pass with outbound network blocked.
- Coverage (`uv run pytest --cov=mdal`): 96% total. `clients/`: anilist 93%, anilist_oauth 95%, mangadex 90%, ratelimit 97%. `sync/`: add_entry 96%, estimate 100%, orchestrator 92%, rules 100%, writer 94%.
- Added in this story: `test_web_settings.py::test_no_secret_values_on_any_page` (every screen, with a stored token).
- README rewritten as the setup and usage guide (prerequisites, both client registrations, `.env`, live check, first-run walkthrough with the one-entry first write, rate settings, troubleshooting).
- **Real dry run on the user's accounts (read-only), 2026-10-03:** 1,929 series, 1,778 AniList entries; 1,797 matched by `links.al`, 27 by `links.mal`, 24 by title, 13 to review, 68 unmatched; 104 writes, 1 implausible flag, 7 completions proposed. Cost: AniList 53, MangaDex 377 (33,425 chapter ids), about 7 minutes. Second run: AniList 1, MangaDex 60 (one retry of 1,876 unresolved chapter ids, now permanent). Pages render in under 0.1 s with the full library.
- Found by the real run and fixed: starting a new sync now cancels older `diffed` runs ("superseded by sync #N"), and the run summary shows overall match totals plus how many were newly matched (it read "matched 0 auto" on cached runs).
- 1,876 of 33,425 read chapter ids (5.6%) could not be resolved, more than the 1% live sample; the diff shows them per row as "unresolved".
- Nothing has been written to AniList by development or QA. The first write is the user's: one entry from the diff screen.
