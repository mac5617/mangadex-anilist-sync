import pytest

from mdal.sync.rules import (
    AlEntry,
    AlMediaInfo,
    MdInfo,
    completion_default,
    evaluate,
    evaluate_series,
    library_info,
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


def test_not_on_list_becomes_add():
    d = ev(["9"], entry=False)
    assert (d.action, d.md_progress, d.site_progress, d.set_status) == ("add", 9, None, None)
    assert "add as Reading at 9" in d.reason


def test_add_completed_when_known_finished_at_total():
    d = ev(["100"], entry=False, media=FIN100)
    assert (d.action, d.set_status, d.status_source) == ("add", "COMPLETED", "AniList")


def test_add_over_total_is_flagged():
    d = ev(["101"], entry=False, media=FIN100)
    assert (d.action, d.flag_kind, d.set_status) == ("flag", "exceeds_total", None)


def test_add_implausible_is_flagged():
    d = ev(["1", "2", "40"], entry=False)
    assert (d.action, d.flag_kind) == ("flag", "implausible")


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


# ---- marked Completed on MangaDex (series that run on past the ending) -----------------------------------

DONE_ON_MD = MdInfo(reading_status="completed")
FIN36 = AlMediaInfo(chapters=36, status="FINISHED")


def test_marked_completed_adds_as_completed_before_the_last_chapter():
    d = ev(["28"], entry=False, media=FIN36, md=DONE_ON_MD)
    assert (d.action, d.md_progress, d.set_status, d.status_source) == ("add", 28, "COMPLETED", "your MangaDex status")
    assert "add as Completed at 28" in d.reason
    assert status_change_label(d, "CURRENT") == "Reading → Completed (marked Completed on MangaDex)"


def test_marked_completed_writes_progress_and_status():
    d = ev(["28"], al=20, media=FIN36, md=DONE_ON_MD)
    assert (d.action, d.md_progress, d.set_status) == ("write", 28, "COMPLETED")


def test_marked_completed_with_anilist_ahead_changes_only_the_status():
    d = ev(["28"], al=30, media=FIN36, md=DONE_ON_MD)
    assert (d.action, d.md_progress, d.site_progress, d.set_status) == ("write", 28, 30, "COMPLETED")
    assert "only the status changes" in d.reason


def test_marked_completed_leaves_completed_and_rereads_alone():
    assert ev(["28"], al=28, status="COMPLETED", media=FIN36, md=DONE_ON_MD).action == "skip"
    d = ev(["28"], al=28, status="REPEATING", media=FIN36, md=DONE_ON_MD)
    assert (d.action, d.set_status) == ("skip", None)


def test_marked_completed_still_flags_past_the_total():
    d = ev(["40"], al=20, media=FIN36, md=DONE_ON_MD)
    assert (d.action, d.flag_kind, d.set_status) == ("flag", "exceeds_total", None)


def test_other_mangadex_statuses_change_nothing():
    d = ev(["28"], entry=False, media=FIN36, md=MdInfo(reading_status="reading"))
    assert d.set_status is None and "add as Reading at 28" in d.reason


# ---- helpers ----------------------------------------------------------------


def test_completion_default():
    assert completion_default(FIN100, MdInfo(), 100) is True
    assert completion_default(FIN100, MdInfo(), 99) is False
    assert completion_default(AlMediaInfo(100, "RELEASING"), MdInfo(), 100) is False
    assert completion_default(FIN_NULL, MD_DONE, 120) is True
    assert completion_default(AlMediaInfo(36, "FINISHED"), MdInfo(reading_status="completed"), 28) is True


def test_status_change_label():
    d = ev(["100"], al=90, media=FIN100)
    assert status_change_label(d, "CURRENT") == "Reading → Completed (AniList: finished, 100 ch)"
    assert status_change_label(ev(["10"]), "CURRENT") is None


@pytest.mark.parametrize("raw, expected", [("10.5", "10.5"), (" 7 ", "7"), ("abc", None), (None, None), ("1e400", "1E+400")])
def test_parse_chapter(raw, expected):
    d = parse_chapter(raw)
    assert (str(d) if d is not None else None) == expected


LIBRARY_ROW = {"pub_status": "completed", "last_chapter": "12", "chapter_numbers_reset": 0, "reading_status": "reading"}


def read(chapter, missing=0):
    return {"chapter": chapter, "missing": missing}


@pytest.mark.parametrize("reads, md_progress, unresolved", [
    ([read("9"), read("10.5")], 10, 0),
    ([read("9"), read(None)], 9, 1),                  # no number
    ([read("9"), read("50", missing=1)], 9, 1),       # MangaDex no longer returns it
    ([read("9"), read("50", missing=None)], 50, 0),   # never looked up as missing
    ([read("9"), read("abc")], 9, 1),                 # not a number
])
def test_evaluate_series_filters_reads(reads, md_progress, unresolved):
    d = evaluate_series(LIBRARY_ROW, reads, AlEntry(1, "CURRENT", 5), RELEASING, 200)
    assert (d.md_progress, d.unresolved) == (md_progress, unresolved)


def test_evaluate_series_uses_the_library_row():
    assert library_info(LIBRARY_ROW) == MdInfo("completed", "12", False, "reading")
    reset = {**LIBRARY_ROW, "chapter_numbers_reset": 1}
    d = evaluate_series(reset, [read("9")], AlEntry(1, "CURRENT", 5), RELEASING, 200, "MyAnimeList")
    assert (d.action, d.flag_kind) == ("flag", "implausible")
