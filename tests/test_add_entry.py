import re

import pytest
import respx

from mdal.sync import add_entry
from mdal.sync.add_entry import AddEntryError, add, not_listed_rows
from tests.factories import FakeAniList, al_media, al_media_row, md_manga_row


@pytest.fixture
def mock():
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        yield router


@pytest.fixture
def fake(mock):
    f = FakeAniList()
    f.add_media(al_media(1, "Listed"), al_media(2, "Finished Total 50", chapters=50, status="FINISHED"),
                al_media(3, "Releasing"))
    f.add_list("Reading", [(10, 1, "CURRENT", 1)])
    return f.install(mock)


@pytest.fixture
def repo(services):
    services.anilist_queue.set_interval(0)
    r = services.repo
    r.replace_md_snapshot(
        [md_manga_row("on", "On List"), md_manga_row("fin", "Finished"), md_manga_row("rel", "Releasing MD"),
         md_manga_row("rev", "In Review"), md_manga_row("over", "Over Total")],
        {"fin": ["f1", "f2"], "rel": ["r1"], "over": ["o1"]},
    )
    r.upsert_chapters([
        {"chapter_id": "f1", "md_id": "fin", "chapter": "50", "volume": None, "missing": 0, "fetched_at": "x"},
        {"chapter_id": "f2", "md_id": "fin", "chapter": None, "volume": None, "missing": 0, "fetched_at": "x"},
        {"chapter_id": "r1", "md_id": "rel", "chapter": "7.5", "volume": None, "missing": 0, "fetched_at": "x"},
        {"chapter_id": "o1", "md_id": "over", "chapter": "60", "volume": None, "missing": 0, "fetched_at": "x"},
    ])
    r.upsert_media([al_media_row(1, "Listed"), al_media_row(2, "Finished Total 50", chapters=50, status="FINISHED"),
                    al_media_row(3, "Releasing"), al_media_row(4, "Over", chapters=50, status="FINISHED")])
    r.replace_al_entries([{"entry_id": 10, "media_id": 1, "status": "CURRENT", "progress": 1, "fetched_at": "x"}])
    for md_id, media_id, state in [("on", 1, "auto"), ("fin", 2, "confirmed"), ("rel", 3, "auto"),
                                   ("rev", 3, "review"), ("over", 4, "auto")]:
        r.upsert_mapping({"md_id": md_id, "al_media_id": media_id if state != "review" else None, "state": state,
                          "tier": 2, "confidence": 1.0, "reasons": [], "links_hash": "h"})
    return r


async def uid():
    return 1


def test_rows_are_exactly_matched_and_unlisted(repo):
    rows = {r.md_id: r for r in not_listed_rows(repo)}
    assert set(rows) == {"fin", "rel", "over"}
    assert (rows["fin"].proposed, rows["fin"].default_status, rows["fin"].unresolved) == (50, "COMPLETED", 1)
    assert (rows["rel"].proposed, rows["rel"].default_status) == (7, "CURRENT")
    assert (rows["over"].proposed, rows["over"].progress, rows["over"].over_total) == (60, 50, True)


async def test_add_sends_one_mutation_with_three_fields(services, repo, fake):
    entry = await add(repo, services.anilist, "rel", "CURRENT", 7, uid)
    assert fake.call_count == 2  # list re-read + the add
    assert len(fake.mutations) == 1
    q, v = fake.mutations[0]["query"], fake.mutations[0]["variables"]
    args = re.search(r"SaveMediaListEntry\(([^)]*)\)", q).group(1)
    assert [a.split(":")[0].strip() for a in args.split(",")] == ["mediaId", "status", "progress"]
    assert v == {"m": 3, "p": 7}
    assert 3 in repo.al_entries() and repo.al_entries()[3]["entry_id"] == entry["id"]
    assert "rel" not in {r.md_id for r in not_listed_rows(repo)}


async def test_add_is_recorded_as_one_item_run(services, repo, fake):
    await add(repo, services.anilist, "fin", "COMPLETED", 50, uid)
    run = repo.latest_run()
    assert (run["state"], run["req_anilist"]) == ("done", 2)
    items = repo.items(run["run_id"])
    assert len(items) == 1
    assert (items[0]["md_id"], items[0]["write_state"], items[0]["set_status"]) == ("fin", "done", "COMPLETED")
    assert "Completed" in run["phase_detail"]


async def test_add_counts_as_first_write(services, repo, fake):
    assert repo.get_setting("first_write_done") is False
    await add(repo, services.anilist, "rel", "CURRENT", 7, uid)
    assert repo.get_setting("first_write_done") is True


@pytest.mark.parametrize(
    "md_id, status, progress, message",
    [
        ("on", "CURRENT", 3, "Already on your list"),
        ("rev", "CURRENT", 3, "no confirmed AniList match"),
        ("fin", "COMPLETED", 51, "exceeds AniList's total"),
        ("rel", "REPEATING", 3, "Unsupported status"),
        ("rel", "CURRENT); x: SaveMediaListEntry(id: 1", 3, "Unsupported status"),
        ("rel", "CURRENT", -1, "cannot be negative"),
    ],
)
async def test_refusals_make_no_request(services, repo, fake, md_id, status, progress, message):
    with pytest.raises(AddEntryError, match=re.escape(message)):
        await add(repo, services.anilist, md_id, status, progress, uid)
    assert fake.call_count == 0


async def test_anilist_refusal_fails_run(services, repo, fake):
    del fake.catalogue[3]
    with pytest.raises(AddEntryError, match="refused"):
        await add(repo, services.anilist, "rel", "CURRENT", 7, uid)
    assert repo.latest_run()["state"] == "failed"
    assert repo.get_setting("first_write_done") is False


def test_statuses_never_include_repeating():
    assert add_entry.ADD_STATUSES == ("CURRENT", "PLANNING", "PAUSED", "COMPLETED", "DROPPED")


async def test_series_added_on_anilist_since_last_sync_is_never_overwritten(services, repo, fake):
    # The local snapshot says "not on list", but the user has since added it on AniList as Completed at 9.
    fake.lists[0]["entries"].append({"id": 77, "status": "COMPLETED", "progress": 9, "media": fake._public(fake.catalogue[3])})
    with pytest.raises(AddEntryError, match=r"Already on your list now \(COMPLETED, progress 9\)"):
        await add(repo, services.anilist, "rel", "CURRENT", 7, uid)
    assert fake.mutations == []
    assert fake.entry(77)["status"] == "COMPLETED" and fake.entry(77)["progress"] == 9
    assert repo.latest_run()["state"] == "cancelled"
