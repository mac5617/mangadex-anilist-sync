# 10: Title normalisation and scoring (pure)

**Covers:** FR-17, FR-18. Architecture §7 "Normalise", "Score", "Classify".

## Context
A wrong match is worse than a missing one. Scoring must explain itself (reasons) and must refuse to auto-accept when any signal disagrees.

## Tasks
- `matching/normalize.py`: `normalize(s) -> str` per §7.
- `matching/score.py`:
  - `score(md: MdSeries, al: AlMedia, md_chapter_count: int) -> Scored(confidence, reasons, disagreements)`, using the signal table in §7;
  - `classify(scored: list[Scored], thresholds) -> ('auto'|'review'|'unmatched', top_n)`.
- Dataclasses `MdSeries` and `AlMedia` are built from DB rows (no I/O in this module).

## Acceptance criteria
- [x] `normalize("ＯＮＥ ＰＩＥＣＥ!")` == `normalize("One Piece")`; `normalize("Pokémon")` == `"pokemon"`; punctuation collapses to single spaces.
- [x] An exact normalised match on any title pair gives title_sim 1.0, and the reason names both strings.
- [x] Year Δ ≥ 3, country mismatch, NOVEL format and author mismatch each subtract the documented amount and set a disagreement.
- [x] Classification boundaries with margin satisfied and no disagreement: 0.92 → auto; 0.9199 → review; 0.60 → review; 0.5999 → unmatched.
- [x] Top 0.95, runner-up 0.91 (margin 0.04 < 0.05) → review.
- [x] Top 0.99 with any disagreement → review.
- [x] Confidence is clamped to [0, 1].

## Tests
`test_normalize.py`, `test_score.py` (parametrised boundary table).

## Dev notes
- Done 2026-10-03; 159 tests pass in total (45 new, including a new purity test in `test_architecture.py` for the §2 pure modules).
- Diacritics are stripped only from Latin letters. A blanket NFKD strip would also drop kana dakuten (ガ→カ) and merge distinct native titles.
- Author "overlap" means any shared normalised name **word** (2+ chars), not whole-name equality. AniList stores "Eiichiro Oda" while MangaDex often has "Oda Eiichirou"; whole-name equality would mark correct matches as disagreeing, which blocks auto-accept.
- Every signal is written to `reasons`, unknown ones too (e.g. `"year unknown"`), so the review screen shows what was and wasn't checked. Year Δ = 2 is recorded as `+0.00`.
- `zh` maps to `CN` only, per §7. A Taiwanese work tagged `zh` on MangaDex therefore gets the country penalty and lands in review, which is the safe direction.
- `classify` uses a 1e-9 tolerance so values exactly at a threshold (including the margin, e.g. 0.97 − 0.92) go to the higher class despite float error.
- `md_chapter_count` (for the ONE_SHOT check) is supplied by the pipeline (story 11); this module does not decide what counts.
