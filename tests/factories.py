"""In-memory fakes of the MangaDex API for respx (reused by the request-budget test, story 13)."""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx
import respx

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
