import json

import pytest

from mdal.matching.score import AlMedia, MdSeries, Scored, Thresholds, classify, score, title_sim


def md(title="Sousou no Frieren", **kw) -> MdSeries:
    return MdSeries(md_id="md-1", title=title, **kw)


def al(media_id=1, romaji="Sousou no Frieren", **kw) -> AlMedia:
    return AlMedia(media_id=media_id, romaji=romaji, **kw)


def sc(confidence, disagreements=(), media_id=1) -> Scored:
    return Scored(media_id, confidence, (), tuple(disagreements))


# --- title ---------------------------------------------------------------


def test_exact_normalised_match_is_one_and_names_both_strings():
    sim, reason = title_sim(md("ＳＯＵＳＯＵ ＮＯ ＦＲＩＥＲＥＮ!"), al(romaji="Sousou no Frieren"))
    assert sim == 1.0
    assert "ＳＯＵＳＯＵ ＮＯ ＦＲＩＥＲＥＮ!" in reason and "Sousou no Frieren" in reason


def test_best_pair_over_alt_titles_and_synonyms():
    m = md("葬送のフリーレン", alt_titles=("Frieren: Beyond Journey's End",))
    a = al(romaji="Sousou no Frieren", english=None, synonyms=("Frieren: Beyond Journey's End",))
    sim, reason = title_sim(m, a)
    assert sim == 1.0
    assert "alt" in reason and "synonym" in reason


def test_fuzzy_title_below_one():
    sim, _ = title_sim(md("Frieren at the Funeral"), al(romaji="Sousou no Frieren", english="Frieren"))
    assert 0 < sim < 1


def test_no_comparable_titles():
    sim, reason = title_sim(md("!!!"), al(romaji=None))
    assert sim == 0.0 and "no comparable" in reason


# --- signals -------------------------------------------------------------

BASE = dict(year=2020, original_language="ja", authors=("Yamada Kanehito",))
AL_BASE = dict(start_year=2020, country="JP", format="MANGA", staff=("Kanehito Yamada", "Tsukasa Abe"))


def test_all_agree_clamped_to_one():
    s = score(md(**BASE), al(**AL_BASE), md_chapter_count=120)
    assert s.confidence == 1.0
    assert s.disagreements == ()
    assert "year 2020 vs 2020 +0.03" in s.reasons
    assert any(r.startswith("country ja vs JP +0.02") for r in s.reasons)
    assert any(r.startswith("author ") and r.endswith("+0.05") for r in s.reasons)


@pytest.mark.parametrize(
    "md_kw, al_kw, chapters, delta, label",
    [
        ({"year": 2020}, {"start_year": 2023}, 10, -0.15, "year 2020 vs 2023"),
        ({"year": 2023}, {"start_year": 2020}, 10, -0.15, "year 2023 vs 2020"),
        ({"original_language": "ko"}, {"country": "JP"}, 10, -0.15, "country ko vs JP"),
        ({}, {"format": "NOVEL"}, 10, -0.30, "format NOVEL"),
        ({}, {"format": "ONE_SHOT"}, 4, -0.10, "format ONE_SHOT vs 4 chapters"),
        ({"authors": ("Oda Eiichirou",)}, {"staff": ("Akira Toriyama",)}, 10, -0.10, "author Oda Eiichirou vs Akira Toriyama"),
    ],
)
def test_disagreeing_signal_subtracts_and_is_recorded(md_kw, al_kw, chapters, delta, label):
    # Fuzzy title + all other signals unknown, so only the signal under test moves the score.
    m = md("Sousou no Frieren", **md_kw)
    a = al(romaji="Sousou no Frieren", **al_kw)
    neutral = score(md("Sousou no Frieren"), al(romaji="Sousou no Frieren", format=al_kw.get("format") and "MANGA"), chapters)
    s = score(m, a, chapters)
    assert s.disagreements == (label,)
    assert f"{label} {delta:+.2f}" in s.reasons
    assert s.confidence == pytest.approx(neutral.confidence + delta)


def test_year_delta_two_is_neutral():
    s = score(md(year=2020), al(start_year=2022), 10)
    assert "year 2020 vs 2022 +0.00" in s.reasons and s.disagreements == ()


def test_one_shot_with_few_chapters_is_fine():
    s = score(md(), al(format="ONE_SHOT"), md_chapter_count=3)
    assert s.disagreements == () and "format ONE_SHOT" in s.reasons


@pytest.mark.parametrize("lang, country", [("zh-hk", "TW"), ("zh-hk", "HK"), ("zh-hk", "CN"), ("zh", "CN"), ("ko", "KR")])
def test_country_agree(lang, country):
    s = score(md(original_language=lang), al(country=country), 10)
    assert s.disagreements == ()


def test_unknown_signals_are_zero():
    s = score(md(original_language="en"), al(), 10)
    assert s.confidence == 1.0  # exact title, nothing added or removed
    for r in ("year unknown", "country unknown", "format unknown", "author unknown"):
        assert r in s.reasons


def test_author_word_order_and_romanisation_still_agree():
    s = score(md(authors=("Oda Eiichirou",)), al(staff=("Eiichiro Oda", "尾田栄一郎")), 10)
    assert s.disagreements == ()


def test_confidence_clamped_at_zero():
    m = md("Completely Different", year=1990, original_language="ko", authors=("A B",))
    a = al(romaji="Zzzz", start_year=2020, country="JP", format="NOVEL", staff=("C D",))
    assert score(m, a, 10).confidence == 0.0


def test_from_rows():
    m = MdSeries.from_row({
        "md_id": "x", "title": "T", "alt_titles": json.dumps(["A", ""]), "original_language": "ja",
        "year": None, "authors": json.dumps(["Au"]),
    })
    assert m.alt_titles == ("A",) and m.authors == ("Au",)
    a = AlMedia.from_row({
        "media_id": 5, "romaji": "R", "english": None, "native": None, "synonyms": "[]", "format": "MANGA",
        "country": "JP", "start_year": 2001, "staff": json.dumps(["S"]),
    })
    assert a.synonyms == () and a.staff == ("S",)


# --- classify ------------------------------------------------------------

T = Thresholds()


@pytest.mark.parametrize(
    "top, expected",
    [(0.92, "auto"), (0.9199, "review"), (0.60, "review"), (0.5999, "unmatched")],
)
def test_classification_boundaries(top, expected):
    assert classify([sc(top)], T)[0] == expected


def test_margin_exactly_at_threshold_is_auto():
    assert classify([sc(0.97), sc(0.92, media_id=2)], T)[0] == "auto"


def test_close_runner_up_goes_to_review():
    assert classify([sc(0.95), sc(0.91, media_id=2)], T)[0] == "review"


def test_disagreement_blocks_auto():
    assert classify([sc(0.99, disagreements=["year 2000 vs 2010"])], T)[0] == "review"


def test_empty_is_unmatched():
    assert classify([], T) == ("unmatched", [])


def test_top_n_sorted_and_capped():
    cls, top = classify([sc(i / 10, media_id=i) for i in range(8)], T)
    assert [s.media_id for s in top] == [7, 6, 5, 4, 3]
    assert cls == "review"


def test_thresholds_are_configurable():
    assert classify([sc(0.85)], Thresholds(auto=0.80, review=0.5, margin=0.05))[0] == "auto"
