import pytest

from mdal.sync.rules import (
    AlEntry,
    AlMediaInfo,
    MdInfo,
    completion_default,
    evaluate,
    parse_chapter,
    status_change_label,
)

RELEASING = AlMediaInfo(chapters=None, status="RELEASING")


def ev(reads, al=5, status="CURRENT", media=RELEASING, md=MdInfo(), jump=200, unresolved=0, entry=True):
    e = AlEntry(1, status, al) if entry else None
    return evaluate(reads, unresolved, e, media, md, jump)


FIN100 = AlMediaInfo(chapters=100, status="FINISHED")


@pytest.mark.parametrize(
    "reads, al, action, md_progress, unresolved",
    [
        (["10.5", "3"], 5, "write", 10, 0),
        (["10.9"], 5, "write", 10, 0),
        ([None, "7"], 5, "write", 7, 1),
        (["abc", "4"], 0, "write", 4, 1),
        (["", "NaN", "Infinity", "-2", "4"], 0, "write", 4, 4),
        (["12"], 12, "skip", 12, 0),
        (["12"], 15, "skip", 12, 0),
    ],
)
def test_progress_table(reads, al, action, md_progress, unresolved):
    d = ev(reads, al=al)
    assert (d.action, d.md_progress, d.unresolved) == (action, md_progress, unresolved)


def test_nothing_read():
    d = ev([None], unresolved=2)
    assert (d.action, d.reason, d.unresolved) == ("skip", "nothing read", 3)


def test_skip_reason_never_lower():
    assert ev(["12"], al=15).reason == "AniList at/ahead"


def test_not_on_list():
    d = ev(["9"], entry=False)
    assert (d.action, d.md_progress) == ("not_on_list", 9)


def test_exceeds_total():
    d = ev(["101"], al=90, media=FIN100)
    assert (d.action, d.flag_kind, d.set_status) == ("flag", "exceeds_total", None)


# ---- completion -------------------------------------------------------------


@pytest.mark.parametrize("status", ["CURRENT", "PAUSED", "DROPPED", "PLANNING", "REPEATING"])
def test_completion_from_any_status(status):
    d = ev(["100"], al=90, status=status, media=FIN100)
    assert (d.action, d.md_progress, d.set_status, d.status_source) == ("write", 100, "COMPLETED", "AniList")
    assert d.hint is None  # no "will remain Planning" when completing


def test_already_completed_gets_no_status():
    d = ev(["100"], al=90, status="COMPLETED", media=FIN100)
    assert (d.action, d.set_status) == ("write", None)


def test_status_only():
    d = ev(["100"], al=100, media=FIN100)
    assert (d.action, d.set_status, d.reason) == ("write", "COMPLETED", "at final chapter; mark completed")


def test_status_only_needs_mangadex_confirmation():
    d = ev(["95"], al=100, media=FIN100)
    assert (d.action, d.set_status) == ("skip", None)


def test_no_completion_when_anilist_releasing():
    d = ev(["100"], al=90, media=AlMediaInfo(100, "RELEASING"))
    assert (d.action, d.md_progress, d.set_status) == ("write", 100, None)
    assert d.hint == "at last known chapter, but AniList lists the series as RELEASING; status unchanged"


MD_DONE = MdInfo(pub_status="completed", last_chapter="120.5")
FIN_NULL = AlMediaInfo(None, "FINISHED")


def test_mangadex_total_fallback():
    d = ev(["120"], al=100, media=FIN_NULL, md=MD_DONE)
    assert (d.action, d.set_status, d.status_source, d.total) == ("write", "COMPLETED", "MangaDex", 120)


def test_reads_beyond_mangadex_total():
    d = ev(["125"], al=100, media=FIN_NULL, md=MD_DONE)
    assert (d.action, d.md_progress, d.set_status) == ("write", 125, None)
    assert d.hint == "reads go beyond MangaDex's last chapter 120"


def test_mangadex_ongoing_no_completion():
    d = ev(["120"], al=100, media=FIN_NULL, md=MdInfo(pub_status="ongoing", last_chapter="120"))
    assert (d.action, d.set_status) == ("write", None)


def test_no_total_anywhere_no_completion():
    d = ev(["120"], al=100, media=FIN_NULL, md=MdInfo(pub_status="completed", last_chapter=None))
    assert (d.action, d.set_status) == ("write", None)


def test_implausible_row_keeps_completion():
    d = ev(["100"], al=90, media=FIN100, md=MdInfo(chapter_numbers_reset=True))
    assert (d.action, d.flag_kind, d.set_status) == ("flag", "implausible", "COMPLETED")


def test_no_total_check_without_chapters():
    assert ev(["500"], al=0, media=RELEASING).action == "write"


# ---- implausible ------------------------------------------------------------


@pytest.mark.parametrize(
    "reads, action",
    [
        (["1", "2", "3", "150"], "flag"),
        (["1", "2", "40"], "flag"),
        (["30", "31", "45"], "write"),
        (["1", "150", "151", "152"], "write"),
        (["12"], "write"),
        (["40", "40"], "write"),  # duplicates are one distinct value
    ],
)
def test_outlier(reads, action):
    d = ev(reads, al=0)
    assert d.action == action
    if action == "flag":
        assert d.flag_kind == "implausible"


def test_chapter_numbers_reset_flags():
    assert ev(["5"], al=0, md=MdInfo(chapter_numbers_reset=True)).flag_kind == "implausible"


@pytest.mark.parametrize("al, action", [(10, "flag"), (0, "write"), (100, "write")])
def test_jump_limit(al, action):
    assert ev(["300"], al=al, jump=200).action == action


def test_planning_hint():
    d = ev(["10"], al=5, status="PLANNING")
    assert d.hint == "status is Planning; will remain Planning"
    assert ev(["3"], al=5, status="PLANNING").hint == "status is Planning; will remain Planning"


# ---- helpers ----------------------------------------------------------------


def test_completion_default():
    assert completion_default(FIN100, MdInfo(), 100) is True
    assert completion_default(FIN100, MdInfo(), 99) is False
    assert completion_default(AlMediaInfo(100, "RELEASING"), MdInfo(), 100) is False
    assert completion_default(FIN_NULL, MD_DONE, 120) is True


def test_status_change_label():
    d = ev(["100"], al=90, media=FIN100)
    assert status_change_label(d, "CURRENT") == "Reading → Completed (AniList: finished, 100 ch)"
    assert status_change_label(ev(["10"]), "CURRENT") is None


@pytest.mark.parametrize("raw, expected", [("10.5", "10.5"), (" 7 ", "7"), ("abc", None), (None, None), ("1e400", "1E+400")])
def test_parse_chapter(raw, expected):
    d = parse_chapter(raw)
    assert (str(d) if d is not None else None) == expected
