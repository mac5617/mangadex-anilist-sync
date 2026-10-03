import pytest

from mdal.matching.pipeline import parse_anilist_ref


@pytest.mark.parametrize(
    "text, expected",
    [
        ("123", 123),
        ("  123 ", 123),
        ("https://anilist.co/manga/123/Sousou-no-Frieren", 123),
        ("https://anilist.co/manga/123/", 123),
        ("http://www.anilist.co/manga/123", 123),
        ("anilist.co/manga/123", 123),
        ("ANILIST.CO/manga/123?x=1", 123),
    ],
)
def test_accepts(text, expected):
    assert parse_anilist_ref(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "https://anilist.co/anime/123/Frieren",
        "anilist.co/anime/123",
        "https://anilist.co/manga/",
        "https://evil.example/anilist.co/manga/123",
        "https://notanilist.co/manga/123",
        "anilist.co/manga/12x",
        "0",
        "-1",
        "abc",
        "",
        None,
    ],
)
def test_rejects(text):
    assert parse_anilist_ref(text) is None
