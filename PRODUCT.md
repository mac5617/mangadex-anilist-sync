# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

One person: the developer, who reads manga on MangaDex and tracks it on AniList (and optionally
MyAnimeList). Shiori is a personal tool, not built for other people. The README is written as install docs
out of habit, but design decisions only need to suit this one reader's routine: syncing after a batch of
reading, clearing matches that need a decision, and browsing for what to read next. They use it at a desktop
browser on the same Windows machine that runs it.

## Product Purpose

Shiori keeps AniList and MyAnimeList manga lists in step with what was read on MangaDex. It works out every
change it would make and shows the whole thing as a diff. Nothing is written until the user approves it.
Success means: start a sync, see exactly what will change (with the request count and estimated time),
approve, done; progress ends up equal to the highest chapter read, and neither API is ever rate-limited or
blocked.

Around the sync it has grown a home for the user's reading: recommendations from their own taste (ranked,
with picks and reasons from a local model), new MangaDex releases matched to their list, an Ask chat,
series pages, head-to-head ranking that sets scores, stats, year in review, friend comparison, and backup.
**The reviewed sync stays the core.** Discovery, stats and ranking are extras built around the list.

## Positioning

It writes only what the user has seen and approved, and it is more careful with someone else's API than
it needs to be. Other sync tools write blindly or ignore rate limits. Shiori treats a wrong match as worse
than a missing one, never lowers progress, sets no status except Completed (and only when the series is
finished and fully read), re-reads the list right before writing, and stops all MangaDex traffic for an
hour at any sign of a block.

## Operating Context

- Runs locally at `http://127.0.0.1:8765` (`uv run mdal`), started and stopped from a terminal. It can't be
  reached from other devices.
- Writes go to AniList or MyAnimeList, one site per sync; nothing is ever written to MangaDex.
- The workflow is Home → Sync → review Changes (Updates, New entries, Flagged, Skipped) → Write selected →
  History, with Matches → To review for uncertain matches.
- Optional local model through Ollama on the same machine. Everything works without it.
- Header sections: Home, Sync, Matches, List, Stats, Discover (see README "Using Shiori").

## Capabilities and Constraints

- Stack: Python 3.12, FastAPI, Jinja2 templates, HTMX, plain CSS in `src/mdal/web/static/app.css`; no
  JavaScript build step. The d3-force bundle is vendored so no internet is needed for the UI.
- API budgets are part of the product: AniList about 20 requests a minute, single-flight; MangaDex 3 a
  second. Estimates of requests and time are shown before any write.
- Terminology in use: sync, Changes, Updates, New entries, Flagged, Skipped, override, mark completed,
  To review, Unlisted, Not interested, Ratings, Ask.
- Adult titles are excluded from recommendations unless the user opts in.
- Secrets live in `.env` and are never displayed in the UI.

## Brand Commitments

- Name: **Shiori** (the app); the package and command are `mdal`.
- Voice: plain, concise and factual. Labels are short real names; copy says what is there or where a link
  goes. No generic AI-product phrasing.
- Existing favicon and touch icon in `src/mdal/web/static/`.

## Evidence on Hand

- The user's own MangaDex library, AniList and MyAnimeList lists, read through the app; covers and metadata
  come from those sites.
- Product docs: `README.md`, `docs/brief.md`, `docs/prd.md`, `docs/architecture.md`, `docs/api-notes.md`.
- There are no other users, testimonials or public claims, and none should be invented.

## Product Principles

1. **Show before writing.** Every change is visible and approvable before it is sent, and history records
   what was sent and checked afterwards.
2. **A wrong change is worse than a missing one.** When unsure, ask the user instead of guessing.
3. **Respect the APIs.** Fewer requests, paced below the limits, and stop at the first sign of trouble.
4. **Sync first.** Extras must not crowd out or slow down the sync and review path.
5. **One reader's tool.** Optimise for the daily routine of the person who runs it, not for onboarding
   strangers.
