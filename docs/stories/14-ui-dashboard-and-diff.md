# 14: UI dashboard and sync diff screen (read-only)

**Covers:** FR-3, FR-4, FR-26, FR-27, FR-28 (display; approval arrives in 16).

## Context
These are the first real screens. Server-rendered Jinja + HTMX, with no JS build. The approve controls render **disabled**, with the note "writing arrives in a later build" until story 16.

## Tasks
- `web/templates/base.html`: nav with Dashboard, Sync, Review (count), Not on my list (count), History, Settings. Minimal CSS in `static/app.css`, vendored `htmx.min.js`.
- `GET /`: dashboard with auth status (from story 06), the last run summary, the Sync button (`hx-post="/sync"`), and a status fragment `GET /sync/status` polled every 2 s while a run is active (stops polling when idle). Shows the review and not-on-list counts.
- `GET /sync/{run_id}`: diff table with columns MangaDex title (linked to `https://mangadex.org/title/{id}`), AniList title (linked to `siteUrl`), AL progress → MD progress, action badge, reason, hint, unresolved-read count, and a **status** column ("Reading → Completed (AniList: finished, 120 ch)") with a per-row "mark completed" checkbox, checked by default, on rows where `set_status` is set. Status-only rows show progress as "100 (unchanged)". Filter tabs: write / flagged / skipped / all. Summary header: counts per action, `est_requests` and `est_seconds` ("≈ 8 AniList requests, ≈ 24 s"). Checkboxes: writes checked, flags unchecked, `exceeds_total` not checkable. Changing the selection re-posts to `POST /sync/{run_id}/estimate`, which returns the updated estimate fragment (pure computation, no API call). A Discard button.
- Every user-supplied or API-supplied string is auto-escaped (Jinja autoescape on).

## Acceptance criteria
- [ ] The dashboard renders with no runs, with an active run, and with a `diffed` run.
- [ ] `POST /sync` starts a run and returns the status fragment. A second POST during a run shows "a sync is already running".
- [ ] The diff page shows the estimate and per-action counts, and the filters work.
- [ ] `exceeds_total` rows have a disabled checkbox. `implausible` rows are unchecked but enabled.
- [ ] The approve button is present and disabled.
- [ ] Rows with `set_status` show the status transition and a checked "mark completed" box. The summary header counts "N to mark completed".
- [ ] A series title containing `<script>` is rendered escaped.
- [ ] Rendering any page makes 0 outbound API calls (respx asserts no calls).

## Tests
`test_web_dashboard.py`, `test_web_diff.py`.

## Dev notes
_(fill in after implementation)_
