"""Tunables through their interface."""

import json

import pytest

from fastapi.testclient import TestClient

from mdal import titles, tunables
from mdal.backup import restore
from mdal.db.repo import SETTING_DEFAULTS
from mdal.tunables import TUNABLES
from mdal.web.app import create_app


def form(**changes):
    return {**{t.key: SETTING_DEFAULTS[t.key] for t in TUNABLES}, **changes}


def test_the_defaults_pass():
    values, errors = tunables.validate(form())
    assert errors == [] and values == {t.key: SETTING_DEFAULTS[t.key] for t in TUNABLES}


@pytest.mark.parametrize("key, value", [
    ("anilist_rpm", 0), ("anilist_rpm", 31), ("mangadex_rps", 0.1), ("mangadex_rps", 4.1), ("mal_rpm", 61),
    ("mangaupdates_rps", 3), ("anilist_rpm", "fast"), ("anilist_rpm", 2.5),
])
def test_rates_stay_in_range(key, value):
    values, errors = tunables.validate(form(**{key: value}))
    assert key not in values and errors


def test_the_review_score_stays_below_auto_accept():
    assert tunables.validate(form(match_review=0.95))[1]


def test_rates_apply_to_the_live_queues(services):
    services.repo.set_setting("anilist_rpm", 10)
    services.repo.set_setting("mangadex_rps", 2)
    services.repo.set_setting("mal_rpm", 12)
    services.repo.set_setting("mangaupdates_rps", 0.5)
    tunables.apply(services)
    assert services.anilist_queue.min_interval == 6
    assert services.mangadex_queue.min_interval == 0.5
    assert services.mal_queue.min_interval == 5
    assert services.mangaupdates_queue.min_interval == 2


def test_reset_is_the_default(services):
    services.repo.set_setting("anilist_write_batch", 2)
    assert tunables.reset(services.repo, "anilist_write_batch") == SETTING_DEFAULTS["anilist_write_batch"]
    assert services.repo.get_setting("anilist_write_batch") == SETTING_DEFAULTS["anilist_write_batch"]


def backup(**settings):
    return {"format": "shiori-backup", "tables": {}, "settings": settings}


@pytest.mark.parametrize("key, value", [
    ("anilist_rpm", 1000), ("anilist_rpm", 0), ("anilist_rpm", "20"), ("anilist_rpm", True), ("anilist_rpm", 2.5),
    ("mangadex_rps", 5), ("mal_rpm", None), ("match_margin", float("nan")),
    ("title_language", "klingon"), ("title_language", 3),
    ("sync_targets", 5), ("sync_targets", ["anilist", "elsewhere"]), ("sync_targets", [["mal"]]),
    ("rank_skipped", 7), ("new_seen", "x"), ("mal_mirror", "no"), ("llm_auto", 1), ("stalled_days", "soon"),
    ("ollama_model", 3), ("rec_llm", True),
])
def test_an_invalid_restored_setting_keeps_yours(services, key, value):
    before = services.repo.get_setting(key)
    restored = restore(services.repo, backup(**{key: value}))
    assert services.repo.get_setting(key) == before and restored.kept == [tunables.name(key)]


def test_the_rest_of_the_backup_still_restores(services):
    restored = restore(services.repo, backup(anilist_rpm=1000, jump_limit=50, stalled_days=30, sync_targets=["mal"]))
    assert services.repo.get_setting("jump_limit") == 50 and services.repo.get_setting("stalled_days") == 30
    assert services.repo.get_setting("sync_targets") == ["mal"]
    assert restored.kept == ["AniList requests per minute"]


def test_a_review_score_above_auto_accept_keeps_both(services):
    restored = restore(services.repo, backup(match_review=0.95, match_auto=0.9))
    assert services.repo.get_setting("match_review") == SETTING_DEFAULTS["match_review"]
    assert services.repo.get_setting("match_auto") == SETTING_DEFAULTS["match_auto"]
    assert len(restored.kept) == 2


def test_a_restored_review_score_is_checked_against_your_auto_accept(services):
    restored = restore(services.repo, backup(match_review=0.95))
    assert services.repo.get_setting("match_review") == SETTING_DEFAULTS["match_review"] and restored.kept


def test_a_restore_applies_live_and_names_what_it_kept(services):
    try:
        with TestClient(create_app(services)) as client:
            body = json.dumps(backup(anilist_rpm=10, title_language="romaji", mal_rpm=1000))
            page = client.post("/backup/restore", files={"file": ("b.json", body, "application/json")})
        assert page.status_code == 200 and "Kept your current MyAnimeList requests per minute" in page.text
        assert services.anilist_queue.min_interval == 6 and titles.language() == "romaji"
        assert services.mal_queue.min_interval == 60.0 / SETTING_DEFAULTS["mal_rpm"]
    finally:
        titles.set_language("english")


def test_a_whole_number_comes_back_whole(services):
    restore(services.repo, backup(anilist_write_batch=5.0))
    value = services.repo.get_setting("anilist_write_batch")
    assert value == 5 and isinstance(value, int)                 # it's used to slice batches
