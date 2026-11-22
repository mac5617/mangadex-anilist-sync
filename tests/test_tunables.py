"""Tunables through their interface."""

import pytest

from mdal import tunables
from mdal.db.repo import SETTING_DEFAULTS
from mdal.tunables import TUNABLES


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
