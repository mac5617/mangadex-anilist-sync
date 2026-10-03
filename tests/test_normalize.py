import pytest

from mdal.matching.normalize import normalize


def test_full_width_and_case():
    assert normalize("ＯＮＥ ＰＩＥＣＥ!") == normalize("One Piece") == "one piece"


def test_diacritics_stripped():
    assert normalize("Pokémon") == "pokemon"
    assert normalize("Shōnen Ōji") == "shonen oji"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Re:Zero -  Kara  Hajimeru", "re zero kara hajimeru"),
        ("Kaguya-sama: Love Is War!?", "kaguya sama love is war"),
        ("  ...Hello,,,World★★  ", "hello world"),
        ("Fullmetal Alchemist — 鋼の錬金術師", "fullmetal alchemist 鋼の錬金術師"),
    ],
)
def test_punctuation_collapses_to_single_spaces(raw, expected):
    assert normalize(raw) == expected


def test_kana_voicing_marks_kept():
    # Dakuten are combining marks under NFKD; dropping them would merge distinct titles.
    assert normalize("ガ") != normalize("カ")
    assert normalize("ドラゴンボール") == "ドラゴンボール"


def test_half_width_katakana_becomes_full_width():
    assert normalize("ﾄﾞﾗｺﾞﾝ") == "ドラゴン"


@pytest.mark.parametrize("raw", [None, "", "   ", "!?…"])
def test_empty(raw):
    assert normalize(raw) == ""
