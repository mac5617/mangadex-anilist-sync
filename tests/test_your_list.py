"""Your list (mdal/lists): the policy through in-memory sites, each site adapter against its fake API,
and one contract both kinds of site keep."""

import json
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from mdal.clients.anilist import ANILIST_URL
from mdal.clients.myanimelist import API_URL as MAL_URL
from mdal.lists import BUSY, Series, SiteResult, YourList
from mdal.lists.anilist import AniListSite
from mdal.lists.myanimelist import MalSite
from tests.factories import al_media_row, connect_mal, md_manga_row


# ---- an in-memory site: the test adapter at Your list's seam --------------------------------------
class MemorySite:
    def __init__(self, name: str, listed: dict[str, float | None], fail: dict[str, str] | None = None) -> None:
        self.name, self.listed, self.fail = name, dict(listed), dict(fail or {})
        self.statuses = {k: "CURRENT" for k in listed}
        self.notes: dict[str, str] = {}
        self.sent: list[dict[str, float]] = []

    def entry(self, series: Series):
        return {"score": self.listed[series.key]} if series.key in self.listed else None

    async def set_scores(self, scores: dict[Series, float]) -> SiteResult:
        result = SiteResult(self.name)
        todo = {}
        for s, score in scores.items():
            if s.key not in self.listed:
                result.skipped[s] = f"not on your {self.name} list"
            elif self.listed[s.key] == score:
                result.unchanged.append(s)
            else:
                todo[s] = score
        self.sent.append({s.key: v for s, v in todo.items()})
        for s, score in todo.items():
            if s.key in self.fail:
                result.failed[s] = self.fail[s.key]
            else:
                self.listed[s.key] = score
                result.saved.append(s)
        return result

    async def set_status(self, series: Series, status: str) -> SiteResult:
        result = SiteResult(self.name)
        if series.key not in self.statuses:
            result.skipped[series] = f"not on your {self.name} list"
        elif self.statuses[series.key] == status:
            result.unchanged.append(series)
        elif series.key in self.fail:
            result.failed[series] = self.fail[series.key]
        else:
            self.sent.append({series.key: status})
            self.statuses[series.key] = status
            result.saved.append(series)
        return result


    async def set_notes(self, series: Series, notes: str) -> SiteResult:
        result = SiteResult(self.name)
        if series.key not in self.statuses:
            result.skipped[series] = f"not on your {self.name} list"
        elif series.key in self.fail:
            result.failed[series] = self.fail[series.key]
        else:
            self.notes[series.key] = notes
            result.saved.append(series)
        return result


@pytest.fixture
def world(services):
    """AniList media 1-3; MangaDex series a, b, c matched to them; 1 and 2 linked to MyAnimeList 501 and 502."""
    repo = services.repo
    repo.upsert_media([al_media_row(1, "One", id_mal=501), al_media_row(2, "Two", id_mal=502), al_media_row(3, "Three")])
    repo.replace_md_snapshot([md_manga_row("a", "One"), md_manga_row("b", "Two", links={"mal": "999"}),
                              md_manga_row("c", "Three", links={"mal": "503"})], {})
    for md_id, media_id in (("a", 1), ("b", 2), ("c", 3)):
        repo.upsert_mapping({"md_id": md_id, "al_media_id": media_id, "state": "confirmed", "tier": 1,
                             "confidence": 1.0, "reasons": "[]", "links_hash": None, "updated_at": "x"})
    return repo


def make(repo, main, mirror, *, busy=False, mirroring=True):
    refreshed: list[str] = []
    lists = YourList(repo, main, mirror, busy=lambda: busy, mirroring=lambda: mirroring,
                     refresh=lambda _repo, md_id: refreshed.append(md_id))
    return lists, refreshed


# ---- naming series -------------------------------------------------------------------------------
def test_a_series_is_named_by_key_and_gets_syncs_myanimelist_link(world):
    lists, _ = make(world, MemorySite("AniList", {}), MemorySite("MyAnimeList", {}))
    s = lists.series(["al:1", "md:c", "al:2", "md:nope"])
    assert (s["al:1"].media_id, s["al:1"].md_ids, s["al:1"].mal_id) == (1, ("a",), 501)
    assert (s["md:c"].media_id, s["md:c"].mal_id) == (3, 503)            # MangaDex's link when AniList has none
    assert s["al:2"].mal_id is None and "different" in s["al:2"].mal_problem   # 502 vs 999: never guessed
    assert s["md:nope"].media_id is None


# ---- policy --------------------------------------------------------------------------------------
async def test_a_score_goes_to_anilist_then_myanimelist_and_refreshes_open_diffs(world):
    al, mal = MemorySite("AniList", {"al:1": None}), MemorySite("MyAnimeList", {"al:1": None})
    lists, refreshed = make(world, al, mal)
    edit = await lists.set_score("al:1", 8.5)
    assert edit.ok and edit.note == "Saved 8.5/10 to AniList and MyAnimeList."
    assert al.listed["al:1"] == mal.listed["al:1"] == 8.5
    assert refreshed == ["a"]


async def test_nothing_is_written_while_shiori_is_busy(world):
    al, mal = MemorySite("AniList", {"al:1": None}), MemorySite("MyAnimeList", {"al:1": None})
    lists, refreshed = make(world, al, mal, busy=True)
    edit = await lists.set_score("al:1", 7)
    assert edit.refused and edit.note == BUSY and not edit.ok
    assert al.sent == mal.sent == [] and refreshed == []


async def test_scores_outside_0_to_10_are_refused(world):
    lists, _ = make(world, MemorySite("AniList", {"al:1": None}), MemorySite("MyAnimeList", {}))
    for bad in (0, -1, 10.5):
        edit = await lists.set_score("al:1", bad)
        assert edit.refused and edit.note == "A score is a number from 0.1 to 10."


async def test_the_mirror_switch_keeps_myanimelist_out(world):
    mal = MemorySite("MyAnimeList", {"al:1": None})
    lists, _ = make(world, MemorySite("AniList", {"al:1": None}), mal, mirroring=False)
    edit = await lists.set_score("al:1", 6)
    assert edit.note == "Saved 6/10 to AniList." and mal.sent == []


async def test_myanimelist_is_not_tried_when_anilist_fails(world):
    al = MemorySite("AniList", {"al:1": None}, fail={"al:1": "AniList said no"})
    mal = MemorySite("MyAnimeList", {"al:1": None})
    lists, refreshed = make(world, al, mal)
    edit = await lists.set_score("al:1", 6)
    assert not edit.ok and edit.note == "AniList didn't save it: AniList said no"
    assert mal.sent == [{}] and mal.listed["al:1"] is None and refreshed == []


async def test_a_myanimelist_failure_after_anilist_saved_says_both(world):
    mal = MemorySite("MyAnimeList", {"al:1": None}, fail={"al:1": "MAL is down"})
    lists, _ = make(world, MemorySite("AniList", {"al:1": None}), mal)
    edit = await lists.set_score("al:1", 6)
    assert not edit.ok and edit.note == "Saved 6/10 to AniList; MyAnimeList: MAL is down."
    assert edit.problems() == ["MyAnimeList: MAL is down"]


async def test_many_scores_get_a_counting_note(world):
    al = MemorySite("AniList", {"al:1": None, "al:2": 5.0, "al:3": None}, fail={"al:3": "no"})
    mal = MemorySite("MyAnimeList", {"al:1": None, "al:2": None})
    lists, _ = make(world, al, mal)
    edit = await lists.set_scores({"al:1": 8, "al:2": 5, "al:3": 9})
    # al:2 was already 5 on AniList (unchanged), so it still goes on to MyAnimeList
    assert edit.note == "1 scores saved to AniList, 1 failed; 2 on MyAnimeList"
    assert edit.problems() == ["no"]


async def test_a_status_goes_to_both_sites(world):
    al, mal = MemorySite("AniList", {"al:1": None}), MemorySite("MyAnimeList", {"al:1": None})
    lists, refreshed = make(world, al, mal)
    edit = await lists.set_status("al:1", "PAUSED")
    assert edit.ok and edit.note == "Marked Paused on AniList and MyAnimeList."
    assert al.statuses["al:1"] == mal.statuses["al:1"] == "PAUSED" and refreshed == ["a"]
    again = await lists.set_status("al:1", "PAUSED")
    assert again.ok and again.note == "Already Paused."


async def test_statuses_are_refused_while_busy_and_rereading_never(world):
    al = MemorySite("AniList", {"al:1": None})
    lists, _ = make(world, al, MemorySite("MyAnimeList", {}), busy=True)
    assert (await lists.set_status("al:1", "DROPPED")).note == BUSY
    lists, _ = make(world, al, MemorySite("MyAnimeList", {}))
    edit = await lists.set_status("al:1", "REPEATING")
    assert edit.refused and edit.note == "That status can't be set here." and al.sent == []


async def test_a_status_anilist_refuses_never_reaches_myanimelist(world):
    mal = MemorySite("MyAnimeList", {"al:1": None})
    lists, _ = make(world, MemorySite("AniList", {"al:1": None}, fail={"al:1": "nope"}), mal)
    edit = await lists.set_status("al:1", "DROPPED")
    assert not edit.ok and edit.note == "AniList didn't save it: nope" and mal.sent == []


async def test_a_status_myanimelist_refuses_is_named(world):
    mal = MemorySite("MyAnimeList", {"al:1": None}, fail={"al:1": "MAL is down"})
    lists, _ = make(world, MemorySite("AniList", {"al:1": None}), mal)
    edit = await lists.set_status("al:1", "DROPPED")
    assert not edit.ok and edit.note == "Marked Dropped on AniList; MyAnimeList: MAL is down."


async def test_notes_are_trimmed_and_mirrored_unless_this_save_says_not(world):
    al, mal = MemorySite("AniList", {"al:1": None}), MemorySite("MyAnimeList", {"al:1": None})
    lists, _ = make(world, al, mal)
    edit = await lists.set_notes("al:1", "  " + "x" * 6000 + "  ")
    assert edit.note == "Saved to AniList and MyAnimeList." and len(al.notes["al:1"]) == 5000 == len(mal.notes["al:1"])
    edit = await lists.set_notes("al:1", "Only here", mirror=False)
    assert edit.note == "Saved to AniList." and mal.notes["al:1"] != "Only here"


async def test_notes_wait_while_busy_and_stop_at_anilist(world):
    mal = MemorySite("MyAnimeList", {"al:1": None})
    lists, _ = make(world, MemorySite("AniList", {"al:1": None}), mal, busy=True)
    assert (await lists.set_notes("al:1", "x")).note == BUSY
    lists, _ = make(world, MemorySite("AniList", {"al:1": None}, fail={"al:1": "AniList refused the notes: no"}), mal)
    edit = await lists.set_notes("al:1", "x")
    assert edit.note == "AniList refused the notes: no" and mal.notes == {}


# ---- the AniList adapter -------------------------------------------------------------------------
class FakeAniListScores:
    def __init__(self, complexity_over: int = 99):
        self.sent, self.complexity_over = [], complexity_over

    def handle(self, request):
        body = json.loads(request.content)
        self.sent.append(body)
        if "scoreRaw" not in body["query"]:                 # a status or notes save
            return httpx.Response(200, json={"data": {"SaveMediaListEntry": {"id": body["variables"]["e"]}}})
        n = len(body["variables"]) // 2
        if n > self.complexity_over:
            return httpx.Response(200, json={"errors": [{"message": "Max query complexity"}], "data": None})
        v = body["variables"]
        return httpx.Response(200, json={"data": {f"m{i}": {"id": v[f"e{i}"], "mediaId": 0, "status": "CURRENT",
                                                              "progress": 0, "score": v[f"s{i}"]} for i in range(n)}})


@pytest.fixture
def anilist(services, world):
    services.store_anilist_token("al-token-xyz")
    services.anilist_queue.set_interval(0)
    world.replace_al_entries([{"entry_id": 100 + i, "media_id": i, "status": "CURRENT", "progress": 0,
                               "score": 70 if i == 2 else None, "fetched_at": "x"} for i in (1, 2, 3)])
    fake = FakeAniListScores(complexity_over=1)
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        router.post(ANILIST_URL).mock(side_effect=fake.handle)
        yield AniListSite(world, services.anilist, batch=lambda: 10), fake


async def test_anilist_edits_by_entry_id_halving_a_batch_too_complex(world, anilist):
    site, fake = anilist
    lists, _ = make(world, site, MemorySite("MyAnimeList", {}), mirroring=False)
    s = lists.series(["al:1", "al:2", "al:3", "md:nope"])
    result = await site.set_scores({s["al:1"]: 8.5, s["al:2"]: 7, s["al:3"]: 9, s["md:nope"]: 5})
    assert [x.key for x in result.saved] == ["al:1", "al:3"]
    assert [x.key for x in result.unchanged] == ["al:2"]                     # already 70: not sent
    assert result.failed == {s["md:nope"]: "not on your AniList list"}
    assert "mediaId:" not in fake.sent[0]["query"]              # addressed by entry id: nothing can be added
    assert [len(b["variables"]) // 2 for b in fake.sent] == [2, 1, 1]       # 2 too complex: halved to 1
    assert world.al_entries()[1]["score"] == 85
    assert ("anilist", "score", "done") in {(e["site"], e["field"], e["state"]) for e in world.list_edits()}


class FakeAniListStatus:
    def __init__(self):
        self.sent = []

    def handle(self, request):
        body = json.loads(request.content)
        self.sent.append(body)
        status = body["query"].split("status: ")[1].split(")")[0]
        return httpx.Response(200, json={"data": {"SaveMediaListEntry": {
            "id": body["variables"]["e"], "mediaId": 0, "status": status, "score": 0,
            "progress": 100 if status == "COMPLETED" else None}}})        # AniList fills in the total


async def test_anilist_status_by_entry_id_and_what_anilist_filled_in(services, world):
    services.store_anilist_token("al-token-xyz")
    services.anilist_queue.set_interval(0)
    world.replace_al_entries([{"entry_id": 101, "media_id": 1, "status": "CURRENT", "progress": 40, "score": None,
                               "fetched_at": "x"}])
    fake = FakeAniListStatus()
    with respx.mock(assert_all_mocked=True) as router:
        router.post(ANILIST_URL).mock(side_effect=fake.handle)
        site = AniListSite(world, services.anilist)
        lists, _ = make(world, site, MemorySite("MyAnimeList", {}), mirroring=False)
        edit = await lists.set_status("al:1", "COMPLETED")
    assert edit.note == "Marked Completed on AniList (it set progress to 100)."
    assert "SaveMediaListEntry(id: $e, status: COMPLETED)" in fake.sent[0]["query"] and fake.sent[0]["variables"] == {"e": 101}
    entry = world.al_entries()[1]
    assert (entry["status"], entry["progress"]) == ("COMPLETED", 100)


# ---- the MyAnimeList adapter ---------------------------------------------------------------------
class FakeMalScores:
    def __init__(self, listed, down=False):
        self.listed, self.down, self.calls = set(listed), down, []

    def get(self, request):
        mal_id = int(request.url.path.rsplit("/", 1)[-1])
        self.calls.append(("GET", mal_id))
        if self.down:
            return httpx.Response(403, text="refused")
        status = {"my_list_status": {"status": "reading", "score": 0}} if mal_id in self.listed else {}
        return httpx.Response(200, json={"id": mal_id, **status})

    def patch(self, request):
        mal_id = int(request.url.path.split("/")[-2])
        self.calls.append(("PATCH", mal_id, {k: v[0] for k, v in parse_qs(request.content.decode()).items()}))
        return httpx.Response(200, json={})


@pytest.fixture
def mal(services, world):
    services.mal_queue.set_interval(0)
    connect_mal(services)
    world.replace_mal_entries([{"mal_id": m, "status": "CURRENT", "progress": 0, "score": 8 if m == 503 else None,
                                "fetched_at": "x"} for m in (501, 503)])

    def site(listed, down=False):
        fake = FakeMalScores(listed, down)
        router.get(url__regex=rf"{MAL_URL}/manga/\d+(\?|$)").mock(side_effect=fake.get)
        router.patch(url__regex=rf"{MAL_URL}/manga/\d+/my_list_status").mock(side_effect=fake.patch)
        return MalSite(world, services.mal), fake

    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        yield site


async def test_myanimelist_reads_each_entry_before_writing_a_whole_number(world, mal):
    site, fake = mal({501, 503})
    lists, _ = make(world, MemorySite("AniList", {}), site)
    s = lists.series(["al:1", "md:c", "al:2"])
    result = await site.set_scores({s["al:1"]: 8.4, s["md:c"]: 7.6, s["al:2"]: 9})
    assert fake.calls == [("GET", 501), ("PATCH", 501, {"score": "8"})]
    assert [x.key for x in result.saved] == ["al:1"] and [x.key for x in result.unchanged] == ["md:c"]   # already 8
    assert "different" in result.skipped[s["al:2"]]
    assert world.mal_entries()[501]["score"] == 8


async def test_myanimelist_never_creates_an_entry_that_left_the_list(world, mal):
    site, fake = mal(set())                       # 501 is on the list as last read, but not any more
    lists, _ = make(world, MemorySite("AniList", {}), site)
    s = lists.series(["al:1"])
    result = await site.set_scores({s["al:1"]: 6})
    assert result.failed == {s["al:1"]: "not on your MyAnimeList list"}
    assert [c[0] for c in fake.calls] == ["GET"]


async def test_a_myanimelist_error_stops_the_rest(world, mal):
    world.replace_mal_entries([{"mal_id": m, "status": "CURRENT", "progress": 0, "score": None, "fetched_at": "x"}
                               for m in (501, 503)])
    site, fake = mal({501, 503}, down=True)
    lists, _ = make(world, MemorySite("AniList", {}), site)
    s = lists.series(["al:1", "md:c"])
    result = await site.set_scores({s["al:1"]: 6, s["md:c"]: 7})
    assert set(result.failed) == {s["al:1"], s["md:c"]}
    assert all(c[0] == "GET" for c in fake.calls) and len({c[1] for c in fake.calls}) == 1


async def test_myanimelist_status_in_its_own_words_after_reading_the_entry(world, mal):
    site, fake = mal({501, 503})
    lists, _ = make(world, MemorySite("AniList", {}), site)
    s = lists.series(["al:1", "md:c", "al:2"])
    saved = await site.set_status(s["al:1"], "PAUSED")
    assert saved.saved == [s["al:1"]] and fake.calls == [("GET", 501), ("PATCH", 501, {"status": "on_hold"})]
    assert world.mal_entries()[501]["status"] == "PAUSED"                 # the local copy keeps AniList's words
    assert (await site.set_status(s["md:c"], "CURRENT")).unchanged == [s["md:c"]]
    assert "different" in (await site.set_status(s["al:2"], "PAUSED")).skipped[s["al:2"]]
    assert len(fake.calls) == 2
    with pytest.raises(ValueError):
        await site.set_status(s["al:1"], "REPEATING")


async def test_myanimelist_notes_go_to_comments_after_reading_the_entry(world, mal):
    site, fake = mal({501})
    lists, _ = make(world, MemorySite("AniList", {}), site)
    s = lists.series(["al:1", "al:2"])
    assert (await site.set_notes(s["al:1"], "Great")).saved == [s["al:1"]]
    assert fake.calls == [("GET", 501), ("PATCH", 501, {"comments": "Great"})]
    assert "different" in (await site.set_notes(s["al:2"], "x")).skipped[s["al:2"]]
    fake.listed.clear()                                   # left the list since it was last read
    assert (await site.set_notes(s["al:1"], "Again")).failed == {s["al:1"]: "not on your MyAnimeList list"}
    assert [c[0] for c in fake.calls] == ["GET", "PATCH", "GET"]       # nothing sent that would create it


# ---- the contract every site keeps -----------------------------------------------------------------
@pytest.fixture(params=["memory", "anilist", "myanimelist"])
def any_site(request, world):
    if request.param == "memory":
        return MemorySite("Memory", {"al:1": None})
    if request.param == "anilist":
        return request.getfixturevalue("anilist")[0]
    return request.getfixturevalue("mal")({501})[0]


async def test_contract_a_new_score_is_saved_then_unchanged(world, any_site):
    lists, _ = make(world, MemorySite("AniList", {}), MemorySite("MyAnimeList", {}))
    s = lists.series(["al:1", "md:nope"])
    first = await any_site.set_scores({s["al:1"]: 7})
    assert first.saved == [s["al:1"]] and not first.failed
    again = await any_site.set_scores({s["al:1"]: 7})
    assert again.unchanged == [s["al:1"]] and not again.saved
    other = await any_site.set_scores({s["md:nope"]: 7})       # not on this site's list
    assert not other.saved and not other.unchanged
    assert any_site.entry(s["al:1"]) is not None and any_site.entry(s["md:nope"]) is None


async def test_contract_a_new_status_is_saved_then_unchanged(world, any_site):
    lists, _ = make(world, MemorySite("AniList", {}), MemorySite("MyAnimeList", {}))
    s = lists.series(["al:1", "md:nope"])
    first = await any_site.set_status(s["al:1"], "DROPPED")
    assert first.saved == [s["al:1"]] and not first.failed
    again = await any_site.set_status(s["al:1"], "DROPPED")
    assert again.unchanged == [s["al:1"]] and not again.saved
    other = await any_site.set_status(s["md:nope"], "DROPPED")
    assert not other.saved and not other.unchanged


# ---- busy, and the Ranker's save that waits for it ------------------------------------------------
def test_busy_covers_the_gap_before_a_queued_site(services):
    orch = services.orchestrator
    assert not orch.busy()
    orch.queued = ["mal"]                     # AniList's dry run done, MyAnimeList's not started yet
    assert orch.busy()


async def test_ranker_scores_wait_while_syncing_and_go_out_when_it_stops(services, monkeypatch):
    import asyncio

    orch, ranker = services.orchestrator, services.ranker
    services.store_anilist_token("al-token-xyz")
    monkeypatch.setattr(ranker, "pending", lambda: {1: 50})
    orch.queued = ["mal"]
    ranker.start_save()
    assert ranker.task is None                # held back

    started = []
    monkeypatch.setattr(ranker, "start_save", lambda: started.append(True))

    async def site_sync():
        return None

    orch.task = orch._watch(asyncio.create_task(site_sync()))
    await orch.task
    await asyncio.sleep(0)
    assert started == []                      # a site is still queued: still busy
    orch.queued = []
    orch.task = orch._watch(asyncio.create_task(site_sync()))
    await orch.task
    await asyncio.sleep(0)
    assert started == [True]


async def test_contract_notes_are_saved_only_where_listed(world, any_site):
    lists, _ = make(world, MemorySite("AniList", {}), MemorySite("MyAnimeList", {}))
    s = lists.series(["al:1", "md:nope"])
    assert (await any_site.set_notes(s["al:1"], "Kaneki")).saved == [s["al:1"]]
    other = await any_site.set_notes(s["md:nope"], "Kaneki")
    assert not other.saved
