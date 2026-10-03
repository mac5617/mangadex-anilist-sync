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
- [ ] `normalize("ＯＮＥ ＰＩＥＣＥ!")` == `normalize("One Piece")`; `normalize("Pokémon")` == `"pokemon"`; punctuation collapses to single spaces.
- [ ] An exact normalised match on any title pair gives title_sim 1.0, and the reason names both strings.
- [ ] Year Δ ≥ 3, country mismatch, NOVEL format and author mismatch each subtract the documented amount and set a disagreement.
- [ ] Classification boundaries with margin satisfied and no disagreement: 0.92 → auto; 0.9199 → review; 0.60 → review; 0.5999 → unmatched.
- [ ] Top 0.95, runner-up 0.91 (margin 0.04 < 0.05) → review.
- [ ] Top 0.99 with any disagreement → review.
- [ ] Confidence is clamped to [0, 1].

## Tests
`test_normalize.py`, `test_score.py` (parametrised boundary table).

## Dev notes
_(fill in after implementation)_
