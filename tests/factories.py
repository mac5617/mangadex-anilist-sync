"""In-memory fakes of the MangaDex API for respx (reused by the request-budget test, story 13)."""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx
import respx

import json
import re

from mdal.clients.anilist import ANILIST_URL
from mdal.clients.mangadex import API_URL, TOKEN_URL


@dataclass
class FakeSeries:
    md_id: str
    title: str
    status: str = "reading"
    links: dict | list = field(default_factory=dict)
    alt_titles: list[dict] = field(default_factory=list)
    year: int | None = 2020
    original_language: str = "ja"
    pub_status: str = "ongoing"
    last_chapter: str | None = None
    numbers_reset: bool = False
    authors: list[str] = field(default_factory=lambda: ["Author A"])
    cover: str | None = "cover.jpg"
    content_rating: str = "safe"
    reads: list[str] = field(default_factory=list)  # chapter ids


@dataclass
class FakeMangaDex:
    series: dict[str, FakeSeries] = field(default_factory=dict)
    chapters: dict[str, dict] = field(default_factory=dict)  # chapter id -> {"chapter":..., "volume":..., "manga": md_id}
    hidden_chapters: set[str] = field(default_factory=set)   # ids /chapter never returns (deleted)
    routes: dict[str, respx.Route] = field(default_factory=dict)

    def add(self, s: FakeSeries, numbers: dict[str, str | None] | None = None) -> FakeSeries:
        self.series[s.md_id] = s
        for ch_id, num in (numbers or {}).items():
            self.chapters[ch_id] = {"chapter": num, "volume": None, "manga": s.md_id}
        return s

    # ---- handlers -------------------------------------------------------
    def _status(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": "ok", "statuses": {k: s.status for k, s in self.series.items()}})

    def _manga(self, request: httpx.Request) -> httpx.Response:
        params = request.url.params
        ratings = set(params.get_list("contentRating[]")) or {"safe", "suggestive", "erotica"}
        data = []
        for md_id in params.get_list("ids[]")[: int(params.get("limit", 10))]:
            s = self.series.get(md_id)
            if not s or s.content_rating not in ratings:
                continue
            rels = [{"id": f"a-{n}", "type": "author", "attributes": {"name": n}} for n in s.authors]
            if s.cover:
                rels.append({"id": "cov", "type": "cover_art", "attributes": {"fileName": s.cover}})
            data.append({
                "id": s.md_id, "type": "manga",
                "attributes": {
                    "title": {"en": s.title}, "altTitles": s.alt_titles, "links": s.links,
                    "originalLanguage": s.original_language, "year": s.year, "status": s.pub_status,
                    "lastChapter": s.last_chapter, "chapterNumbersResetOnNewVolume": s.numbers_reset,
                    "contentRating": s.content_rating,
                },
                "relationships": rels,
            })
        return httpx.Response(200, json={"result": "ok", "data": data})

    def _read(self, request: httpx.Request) -> httpx.Response:
        ids = request.url.params.get_list("ids[]")
        grouped = {i: self.series[i].reads for i in ids if i in self.series and self.series[i].reads}
        return httpx.Response(200, json={"result": "ok", "data": grouped})

    def _chapter(self, request: httpx.Request) -> httpx.Response:
        params = request.url.params
        include_unavailable = params.get("includeUnavailable") == "1"
        data = []
        for ch_id in params.get_list("ids[]")[: int(params.get("limit", 10))]:
            ch = self.chapters.get(ch_id)
            if ch is None or ch_id in self.hidden_chapters:
                continue
            if ch.get("unavailable") and not include_unavailable:
                continue
            data.append({
                "id": ch_id, "type": "chapter",
                "attributes": {"chapter": ch["chapter"], "volume": ch["volume"]},
                "relationships": [{"id": ch["manga"], "type": "manga"}],
            })
        return httpx.Response(200, json={"result": "ok", "data": data})

    def install(self, router: respx.MockRouter) -> FakeMangaDex:
        self.routes["token"] = router.post(TOKEN_URL).respond(
            200, json={"access_token": "md-access-1111", "refresh_token": "md-refresh-1111"}
        )
        self.routes["status"] = router.get(f"{API_URL}/manga/status").mock(side_effect=self._status)
        self.routes["read"] = router.get(f"{API_URL}/manga/read").mock(side_effect=self._read)
        self.routes["manga"] = router.get(f"{API_URL}/manga").mock(side_effect=self._manga)
        self.routes["chapter"] = router.get(f"{API_URL}/chapter").mock(side_effect=self._chapter)
        return self

    def data_calls(self) -> dict[str, int]:
        return {name: route.call_count for name, route in self.routes.items()}


# ---- AniList ---------------------------------------------------------------

def al_media(media_id: int, romaji: str, *, english: str | None = None, native: str | None = None,
             id_mal: int | None = None, fmt: str = "MANGA", status: str = "RELEASING", chapters: int | None = None,
             country: str = "JP", year: int | None = 2020, synonyms: list[str] | None = None,
             staff: list[str] | None = None, type_: str = "MANGA", genres: list[str] | None = None) -> dict:
    """An AniList Media object as the API returns it."""
    return {
        "id": media_id, "idMal": id_mal, "type": type_, "format": fmt, "status": status, "chapters": chapters,
        "countryOfOrigin": country, "startDate": {"year": year},
        "title": {"romaji": romaji, "english": english, "native": native},
        "synonyms": synonyms or [], "coverImage": {"medium": f"https://img.example/{media_id}.jpg"},
        "genres": genres or [],
        "siteUrl": f"https://anilist.co/manga/{media_id}",
        "_staff": staff or [],
    }


@dataclass
class FakeAniList:
    """Answers the read queries in fetch/anilist_list.py from an in-memory catalogue."""
    catalogue: dict[int, dict] = field(default_factory=dict)        # media_id -> media
    lists: list[dict] = field(default_factory=list)                 # MediaListCollection.lists
    viewer: dict = field(default_factory=lambda: {"id": 1, "name": "Reader"})
    requests: list[dict] = field(default_factory=list)              # parsed request bodies
    route: respx.Route | None = None
    # mutation behaviour (story 16)
    mutations: list[dict] = field(default_factory=list)             # bodies of executed mutation documents
    applied: list[tuple[int, dict]] = field(default_factory=list)   # (entry_id, args) per executed alias
    complexity_limit: int | None = None                            # more aliases than this → complexity error
    alias_errors: dict[int, str] = field(default_factory=dict)      # entry_id -> per-alias error message
    status_429: int = 0                                            # answer the next N mutations with 429
    server_status_after_write: dict[int, str] = field(default_factory=dict)  # entry_id -> status AniList sets itself

    def entry(self, entry_id: int) -> dict | None:
        for group in self.lists:
            for e in group["entries"]:
                if e["id"] == entry_id:
                    return e
        return None

    def _mutate(self, query: str, variables: dict) -> httpx.Response:
        if self.status_429:
            self.status_429 -= 1
            return httpx.Response(429, headers={"Retry-After": "0"}, text="<html>Too Many Requests</html>")
        created = re.search(r"SaveMediaListEntry\(mediaId: \$m, status: (\w+), progress: \$p\)", query)
        if created:
            self.mutations.append({"query": query, "variables": variables})
            media_id, status, progress = variables["m"], created.group(1), variables["p"]
            if media_id not in self.catalogue:
                return httpx.Response(404, json={"data": {"SaveMediaListEntry": None}, "errors": [{"message": "Not Found.", "path": ["SaveMediaListEntry"]}]})
            entry_id = 9000 + len(self.mutations)
            if not any(g["name"] == "Added" for g in self.lists):
                self.lists.append({"name": "Added", "isCustomList": False, "entries": []})
            group = next(g for g in self.lists if g["name"] == "Added")
            group["entries"].append({"id": entry_id, "status": status, "progress": progress, "media": self._public(self.catalogue[media_id])})
            self.applied.append((entry_id, {"mediaId": media_id, "status": status, "progress": progress}))
            return httpx.Response(200, json={"data": {"SaveMediaListEntry": {"id": entry_id, "mediaId": media_id, "progress": progress, "status": status}}})
        aliases = re.findall(r"(m\d+): SaveMediaListEntry\(([^)]*)\)", query)
        if self.complexity_limit is not None and len(aliases) > self.complexity_limit:
            return httpx.Response(400, json={"data": None, "errors": [{"message": "Max query complexity exceeded"}]})
        self.mutations.append({"query": query, "variables": variables})
        data, errors = {}, []
        for alias, arg_text in aliases:
            args = {}
            for name, value in re.findall(r"(\w+): (\$\w+|\w+)", arg_text):
                args[name] = variables.get(value[1:]) if value.startswith("$") else value
            if "mediaId" in args:
                media_id = args["mediaId"]
                if media_id not in self.catalogue:
                    data[alias] = None
                    errors.append({"message": "Not Found.", "path": [alias]})
                    continue
                new_id = 9000 + len(self.applied) + 1
                if not any(g["name"] == "Added" for g in self.lists):
                    self.lists.append({"name": "Added", "isCustomList": False, "entries": []})
                group = next(g for g in self.lists if g["name"] == "Added")
                group["entries"].append({"id": new_id, "status": args["status"], "progress": args["progress"],
                                         "media": self._public(self.catalogue[media_id])})
                self.applied.append((new_id, args))
                data[alias] = {"id": new_id, "mediaId": media_id, "progress": args["progress"], "status": args["status"]}
                continue
            entry_id = args["id"]
            if entry_id in self.alias_errors:
                data[alias] = None
                errors.append({"message": self.alias_errors[entry_id], "path": [alias]})
                continue
            e = self.entry(entry_id)
            if e is None:
                data[alias] = None
                errors.append({"message": "Not Found.", "path": [alias]})
                continue
            if "progress" in args:
                e["progress"] = args["progress"]
            if "status" in args:
                e["status"] = args["status"]
            if entry_id in self.server_status_after_write:
                e["status"] = self.server_status_after_write[entry_id]
            self.applied.append((entry_id, args))
            data[alias] = {"id": e["id"], "mediaId": e["media"]["id"], "progress": e["progress"], "status": e["status"]}
        body = {"data": data}
        if errors:
            body["errors"] = errors
        return httpx.Response(200 if not errors else 400, json=body)

    def add_media(self, *media: dict) -> None:
        for m in media:
            self.catalogue[m["id"]] = m

    def add_list(self, name: str, entries: list[tuple[int, int, str, int]], custom: bool = False) -> None:
        """entries: (entry_id, media_id, status, progress)."""
        self.lists.append({
            "name": name, "isCustomList": custom,
            "entries": [{"id": e, "status": s, "progress": p, "media": self._public(self.catalogue[m])}
                        for e, m, s, p in entries],
        })

    @staticmethod
    def _public(m: dict, with_staff: bool = False) -> dict:
        out = {k: v for k, v in m.items() if not k.startswith("_")}
        if with_staff:
            out["staff"] = {"nodes": [{"name": {"full": n, "native": None}} for n in m["_staff"]]}
        return out

    def _search(self, text: str) -> list[dict]:
        needle = text.lower()
        hits = [m for m in self.catalogue.values() if m["type"] == "MANGA" and any(
            needle in (t or "").lower() or (t or "").lower() in needle
            for t in [*m["title"].values(), *m["synonyms"]] if t)]
        return [self._public(m, with_staff=True) for m in hits[:5]]

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        query, variables = body["query"], body.get("variables") or {}
        if query.lstrip().startswith("mutation"):
            return self._mutate(query, variables)
        if "Viewer" in query:
            data = {"Viewer": self.viewer}
        elif "MediaListCollection" in query:
            data = {"MediaListCollection": {"lists": self.lists}}
        elif "id_in" in query or "idMal_in" in query:
            key = "id" if "id_in" in query else "idMal"
            ids = set(variables["ids"])
            media = [self._public(m) for m in self.catalogue.values() if m[key] in ids and m["type"] == "MANGA"]
            data = {"Page": {"pageInfo": {"hasNextPage": False}, "media": media[: variables.get("perPage", 50)]}}
        elif re.search(r"\bm0: Media\(", query):
            ids = [int(x) for x in re.findall(r"Media\(id: (\d+)\)", query)]
            data = {f"m{i}": self._public(self.catalogue[mid]) if mid in self.catalogue else None for i, mid in enumerate(ids)}
        elif re.search(r"\bs0: Page", query):
            data = {f"s{i}": {"media": self._search(variables[f"q{i}"])} for i in range(len(variables))}
        else:
            return httpx.Response(400, json={"errors": [{"message": "unexpected query in fake"}]})
        return httpx.Response(200, json={"data": data})

    def install(self, router: respx.MockRouter) -> "FakeAniList":
        self.route = router.post(ANILIST_URL).mock(side_effect=self.handle)
        return self

    @property
    def call_count(self) -> int:
        return self.route.call_count if self.route else 0


# ---- generated library (architecture §11 assumptions; story 13) ------------

def generate_library(n: int = 500, reads_per_series: int = 60) -> tuple[FakeMangaDex, FakeAniList]:
    """85% links.al, 5% only links.mal, 10% neither; 450 of the matched series on the AniList list.

    Every series has `reads_per_series` read chapters numbered 1..N. List entries are at N, except
    every 8th, which is 10 behind (≈ 60 updates for 500 series).
    """
    md, al = FakeMangaDex(), FakeAniList()
    n_al, n_mal = n * 85 // 100, n * 5 // 100
    on_list = n * 90 // 100
    entries: list[tuple[int, int, str, int]] = []
    for i in range(n):
        title = f"Generated Series {i:03d}"
        media_id, mal_id = 10000 + i, 20000 + i
        if i < n_al:
            links = {"al": str(media_id)}
        elif i < n_al + n_mal:
            links = {"mal": str(mal_id)}
        else:
            links = {}
        al.add_media(al_media(media_id, title, id_mal=mal_id, year=2020, staff=["Author A"]))
        chapter_ids = [f"ch-{i:03d}-{c:03d}" for c in range(1, reads_per_series + 1)]
        md.add(FakeSeries(f"md-{i:03d}", title, links=links, reads=chapter_ids),
               {ch: str(c) for c, ch in enumerate(chapter_ids, start=1)})
        # The first 25 links.al series are not on the list (they need one id_in request).
        listed = (i >= 25 and i < n_al + n_mal) or (n_al + n_mal <= i < n_al + n_mal + (on_list - (n_al - 25) - n_mal))
        if listed:
            entries.append((50000 + i, media_id, "CURRENT", reads_per_series - (10 if i % 8 == 0 else 0)))
    al.add_list("Reading", entries)
    return md, al


# ---- seeded runs for web tests (no API) ------------------------------------

def md_manga_row(md_id: str, title: str, **kw) -> dict:
    from mdal.fetch.mangadex_library import links_hash

    links = kw.pop("links", {})
    row = {
        "md_id": md_id, "in_library": 1, "reading_status": "reading", "title": title,
        "alt_titles": json.dumps(kw.pop("alt_titles", [])), "original_language": "ja", "year": 2020,
        "pub_status": "ongoing", "last_chapter": None, "links": json.dumps(links, sort_keys=True),
        "links_hash": links_hash(links), "authors": json.dumps(["Author A"]), "cover_file": "c.jpg",
        "chapter_numbers_reset": 0, "fetched_at": "2026-10-03T00:00:00+00:00",
    }
    row.update(kw)
    return row


def al_media_row(media_id: int, romaji: str, **kw) -> dict:
    row = {
        "media_id": media_id, "id_mal": None, "type": "MANGA", "format": "MANGA", "status": "RELEASING",
        "chapters": None, "country": "JP", "start_year": 2020, "romaji": romaji, "english": None, "native": None,
        "synonyms": "[]", "staff": "[]", "cover_url": f"https://img.example/{media_id}.jpg",
        "site_url": f"https://anilist.co/manga/{media_id}", "fetched_at": "2026-10-03T00:00:00+00:00",
    }
    row.update(kw)
    return row


def seed_diffed_run(repo, *, evil_title: str = "<script>alert(1)</script> Evil") -> int:
    """A diffed run with one row of each kind: write, completion, status-only, implausible, exceeds_total, skip."""
    repo.replace_md_snapshot([
        md_manga_row("w", "Writer Series"),
        md_manga_row("c", "Completing Series"),
        md_manga_row("s", "Status Only Series"),
        md_manga_row("i", "Implausible Series"),
        md_manga_row("x", "Exceeds Series"),
        md_manga_row("k", evil_title),
    ], {})
    repo.upsert_media([
        al_media_row(1, "AL Writer"),
        al_media_row(2, "AL Completing", status="FINISHED", chapters=120),
        al_media_row(3, "AL Status Only", status="FINISHED", chapters=50),
        al_media_row(4, "AL Implausible"),
        al_media_row(5, "AL Exceeds", status="FINISHED", chapters=10),
    ])
    repo.replace_al_entries([
        {"entry_id": 100 + i, "media_id": i, "status": "CURRENT", "progress": p, "fetched_at": "x"}
        for i, p in [(1, 5), (2, 110), (3, 50), (4, 10), (5, 9)]
    ])
    run_id = repo.create_run("diffed")
    base = {"run_id": run_id}
    repo.replace_items(run_id, [
        {**base, "md_id": "w", "al_media_id": 1, "al_entry_id": 101, "al_progress": 5, "md_progress": 12,
         "action": "write", "reason": "MangaDex 12 > AniList 5", "unresolved_reads": 2},
        {**base, "md_id": "c", "al_media_id": 2, "al_entry_id": 102, "al_progress": 110, "md_progress": 120,
         "action": "write", "reason": "MangaDex 120 > AniList 110", "set_status": "COMPLETED",
         "status_source": "AniList", "status_approved": 1},
        {**base, "md_id": "s", "al_media_id": 3, "al_entry_id": 103, "al_progress": 50, "md_progress": 50,
         "action": "write", "reason": "at final chapter; mark completed", "set_status": "COMPLETED",
         "status_source": "AniList", "status_approved": 1},
        {**base, "md_id": "i", "al_media_id": 4, "al_entry_id": 104, "al_progress": 10, "md_progress": 400,
         "action": "flag", "flag_kind": "implausible", "reason": "jump of 390 chapters exceeds the limit of 200"},
        {**base, "md_id": "x", "al_media_id": 5, "al_entry_id": 105, "al_progress": 9, "md_progress": 11,
         "action": "flag", "flag_kind": "exceeds_total", "reason": "MangaDex 11 exceeds AniList's total of 10 chapters"},
        {**base, "md_id": "k", "action": "skip", "reason": "no match"},
    ])
    repo.update_run(run_id, phase_detail="6 series", est_requests=3, est_seconds=9)
    return run_id
