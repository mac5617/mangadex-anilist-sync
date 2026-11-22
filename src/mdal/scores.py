"""Scores on each site's scale. Pure.

A Score is out of 10 with one decimal. AniList stores 1-100 and MyAnimeList a whole number from 1 to 10; 0 or nothing
means no score. A MangaDex rating is a whole number from 1 to 10.
"""

from __future__ import annotations


def to_anilist(score: float) -> int:
    """A Score -> AniList's 1-100 (0 would mean "no score")."""
    return max(1, min(100, round(score * 10)))


def to_mal(score: float) -> int:
    """A Score -> MyAnimeList's whole number, by way of AniList's 1-100 (84.5 rounds half to even: 8)."""
    return max(1, min(10, round(to_anilist(score) / 10)))


def rating_on_site(rating: int, site: str) -> int:
    """A MangaDex rating on this site's scale ("AniList" or "MyAnimeList")."""
    return rating * 10 if site == "AniList" else rating


def score_offer(rating: int | None, target: str, *, anilist: float | None, mal: int | None) -> int | None:
    """The Score offer for `target` ("anilist" or "mal"), given each site's score on its own scale: your rating, for
    an entry that has no score, unless the other site scored it differently (agreeing means it came from MangaDex on
    an earlier sync)."""
    mal_points = (mal or 0) * 10
    own, other = (anilist, mal_points) if target == "anilist" else (mal_points, anilist)
    if rating is None or own or (other and round(other) != rating * 10):
        return None
    return rating
