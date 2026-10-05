"""Series pages: everything known about one series, and the lookups that fill in the rest.

A series is 'al:<AniList id>' or 'md:<MangaDex uuid>'. The page reads what is stored (AniList and
MangaDex data, your list, your verdict, why it was recommended); one lookup per week adds what
AniList and MyAnimeList readers recommend for it and its MangaUpdates record (English releases,
licensing, categories). A lookup costs 1-3 AniList, 1 MyAnimeList and 1-2 MangaUpdates requests.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from mdal.clients.anilist import AniListError
from mdal.clients.mangadex import ALL_CONTENT_RATINGS, MangaDexError
from mdal.clients.mangaupdates import MangaUpdatesError
from mdal.clients.myanimelist import MalError
from mdal.fetch.anilist_list import plain_text
from mdal.fetch.anilist_recs import fetch_neighbours
from mdal.fetch.mal_recs import anilist_by_mal, mal_recommendations
from mdal.fetch.mangaupdates import fresh_enough, lookup_mu
from mdal.matching.normalize import normalize
from mdal.recommend import ranking
from mdal.recommend.feedback import VERDICTS
from mdal.recommend.fresh import MD_COVER, english_meta
from mdal.recommend.ratings import title_of
from mdal.stats import COUNTRY_LABELS, FORMAT_LABELS, MD_STATUS_LABELS, PUB_LABELS

if TYPE_CHECKING:
    from mdal.services import Services

log = logging.getLogger(__name__)

LIST_STATUS = {"CURRENT": "Reading", "COMPLETED": "Completed", "PAUSED": "Paused", "DROPPED": "Dropped",
               "PLANNING": "Planning", "REPEATING": "Rereading"}
MD_STATUS = {"ongoing": "Releasing", "completed": "Finished", "hiatus": "On hiatus", "cancelled": "Cancelled"}
MD_COUNTRY = {"ja": "Japan", "ko": "South Korea", "zh": "China", "zh-hk": "Hong Kong"}


def series_url(key: str) -> str:
    kind, ident = key.split(":", 1)
    return f"/series/{kind}/{ident}"


def big_cover(url: str | None) -> str | None:
    """The biggest cover there is: MangaDex's 512px thumbnail, or AniList's large one. AniList only has "large"
    for covers uploaded since its newer format (file names starting "bx"); older ones ("b123-...") stop at medium."""
    if not url:
        return None
    if "anilist" in url:
        size = "large" if url.rsplit("/", 1)[-1].startswith("bx") else "medium"
        return url.replace("/cover/small/", f"/cover/{size}/").replace("/cover/medium/", f"/cover/{size}/")
    return url.replace(".256.jpg", ".512.jpg")


class SeriesPages:
    def __init__(self, services: Services) -> None:
        self.services = services

    @property
    def repo(self):
        return self.services.repo

    # ---- what is stored --------------------------------------------------------------
    def _rows(self, key: str) -> tuple[Any, Any, Any]:
        """(AniList media, MangaDex new-release row, MangaDex library row) for a series, any of them None."""
        repo = self.repo
        if key.startswith("md:"):
            md_id = key[3:]
            new = repo.conn.execute("SELECT * FROM md_new WHERE md_id=?", (md_id,)).fetchone()
            lib = repo.conn.execute("SELECT * FROM md_manga WHERE md_id=?", (md_id,)).fetchone()
            al_id = new["al_id"] if new else None
            if al_id is None and lib is not None:
                m = repo.get_mapping(md_id)
                al_id = m["al_media_id"] if m and m["state"] in ("auto", "confirmed") else None
            if al_id is None:
                cached = self._cache(key, "anilist")
                al_id = (cached or {}).get("anchor")
            media = repo.media([al_id]).get(al_id) if al_id else None
            return media, new, lib
        media_id = int(key[3:])
        media = repo.media([media_id]).get(media_id)
        new = repo.conn.execute("SELECT * FROM md_new WHERE al_id=?", (media_id,)).fetchone()
        lib = repo.conn.execute("SELECT d.* FROM mapping p JOIN md_manga d USING (md_id) WHERE p.al_media_id=? "
                                "AND p.state IN ('auto','confirmed') ORDER BY d.md_id", (media_id,)).fetchone()
        return media, new, lib

    def exists(self, key: str) -> bool:
        return any(self._rows(key))

    def _cache(self, key: str, kind: str) -> Any:
        row = self.repo.cached(key, kind)
        return json.loads(row["data"]) if row and row["data"] else None

    def looked_up(self, key: str) -> bool:
        """Whether a lookup ran this week (it leaves a marker even when a site had nothing or failed).
        A MangaUpdates record saved before covers were kept counts as not looked up, so it's fetched again."""
        mu = self._cache(key, "mu") or {}
        if any("cover" not in r for r in mu.get("recommendations") or []):
            return False
        return fresh_enough(self.repo.cached(key, "checked"))

    def view(self, key: str) -> dict[str, Any] | None:
        media, new, lib = self._rows(key)
        if not (media or new or lib):
            return None
        repo = self.repo
        md = new or lib
        md_id = md["md_id"] if md else (self._cache(key, "md") or {}).get("md_id")
        title = (new["title"] if new else None) or (title_of(media) if media else None) or (lib["title"] if lib else "")
        alt = []
        if media:
            alt += [media["romaji"], media["english"], media["native"], *json.loads(media["synonyms"] or "[]")]
        if md:
            alt += [md["title"], *json.loads(md["alt_titles"] or "[]")]
        seen = {normalize(title)}
        alt_titles = [t for t in alt if t and normalize(t) not in seen and not seen.add(normalize(t))][:12]

        cover = None
        fallback = media["cover_url"] if media else None    # the small cover, if the big one fails to load
        if md and md["cover_file"]:
            cover = MD_COVER.format(md_id=md_id, file=md["cover_file"]).replace(".256.jpg", ".512.jpg")
        elif media and media["cover_url"]:
            cover = big_cover(media["cover_url"])
        description = (new["description"] if new else None) or (plain_text(media["description"]) if media and media["description"] else None)

        genres = json.loads(media["genres"] or "[]") if media else []
        tags = [{"name": t["name"], "rank": t.get("rank")} for t in json.loads(media["tags"] or "[]")] if media else []
        if not media and new:
            names = [t["name"] for t in json.loads(new["tags"] or "[]")]
            tags = [{"name": n, "rank": None} for n in names]
        staff = json.loads(media["staff_roles"] or "[]") if media and media["staff_roles"] else []
        authors = json.loads(md["authors"] or "[]") if md else []

        facts = []
        if media:
            facts += [("Format", FORMAT_LABELS.get(media["format"] or "", media["format"])),
                      ("Origin", COUNTRY_LABELS.get(media["country"] or "", media["country"])),
                      ("Started", media["start_year"]), ("Status", PUB_LABELS.get(media["status"] or "", media["status"])),
                      ("Chapters", media["chapters"]),
                      ("AniList score", f"{media['mean_score']}/100" if media["mean_score"] else None),
                      ("On AniList lists", f"{media['popularity']:,}" if media["popularity"] else None)]
        elif md:
            facts += [("Origin", MD_COUNTRY.get(md["original_language"] or "", md["original_language"])),
                      ("Started", md["year"]), ("Status", MD_STATUS.get(md["pub_status"] or "", md["pub_status"])),
                      ("Last chapter", md["last_chapter"])]
            if new:
                facts += [("Demographic", (new["demographic"] or "").capitalize() or None),
                          ("Rating", (new["content_rating"] or "").capitalize() or None)]
        mu = self._cache(key, "mu")
        facts = [(k, v) for k, v in facts if v not in (None, "")]

        al_id = media["media_id"] if media else None
        entry = repo.al_entries().get(al_id) if al_id else None
        verdict = repo.feedback().get(key) or (repo.feedback().get(f"al:{al_id}") if al_id else None)
        links = [(label, url) for label, url in (
            ("AniList", media["site_url"] if media else None),
            ("MangaDex", f"https://mangadex.org/title/{md_id}" if md_id else None),
            ("Search MangaDex", None if md_id else f"https://mangadex.org/search?q={quote(title)}"),
            ("MyAnimeList", f"https://myanimelist.net/manga/{media['id_mal']}" if media and media["id_mal"]
             else (f"https://myanimelist.net/manga/{new['mal_id']}" if new and new["mal_id"] else None))) if url]
        return {
            "key": key, "title": title, "alt_titles": alt_titles, "cover": cover, "cover_fallback": fallback,
            "description": description,
            "genres": genres, "tags": tags, "staff": staff, "authors": authors, "facts": facts, "links": links,
            "adult": bool(media["is_adult"]) if media and media["is_adult"] is not None else
                     bool(new and new["content_rating"] in ("erotica", "pornographic")),
            "entry": {"status": LIST_STATUS.get(entry["status"], entry["status"]), "progress": entry["progress"],
                      "score": entry["score"]} if entry else None,
            "md_status": MD_STATUS_LABELS.get(lib["reading_status"] or "none") if lib else None,
            "verdict": {"verdict": verdict["verdict"], "label": VERDICTS[verdict["verdict"]], "source": verdict["source"],
                        "at": verdict["created_at"]} if verdict else None,
            "verdict_key": key if not verdict else verdict["key"],
            "reasons": self._reasons(key, al_id),
            "ranked": self._ranked(al_id) if entry else None, "al_id": al_id,
            "notes": entry["notes"] if entry else None,
            "mal_mirror": bool(entry and media["id_mal"] and self.services.mal.connected
                               and media["id_mal"] in self.repo.mal_entries()),
            "mu": mu, "english": english_meta(mu), "looked_up": self.looked_up(key), "problems": self.problems(key),
            "readers": self._readers(key),
        }

    def _ranked(self, al_id: int) -> dict[str, Any] | None:
        """Where it sits in your ranking (List → Rank), if it's ranked."""
        rows = self.repo.ranking()
        row = next((r for r in rows if r["media_id"] == al_id), None)
        if row is None:
            return None
        tier = [r["media_id"] for r in rows if r["tier"] == row["tier"]]
        return {"place": tier.index(al_id) + 1, "label": ranking.TIERS[row["tier"]][2],
                "score": self.services.ranker.ranked_scores()[al_id]}

    def _reasons(self, key: str, al_id: int | None) -> list[str]:
        """Why Shiori recommends it, when it does."""
        if key.startswith("md:"):
            _, recs = self.services.releases.recs(include_adult=True)
            r = next((r for r in recs if r.md_id == key[3:]), None)
            return r.reasons if r else []
        _, recs = self.services.recommender.recs(include_adult=True)
        r = next((r for r in recs if r.media_id == al_id), None)
        if not r:
            return []
        return [x for k in ("community", "staff", "tag", "genre") for x in r.reasons.get(k, [])[:2]]

    def _readers(self, key: str) -> dict[str, list[dict[str, Any]]]:
        """Readers' recommendations from the last lookup: AniList, MyAnimeList, MangaUpdates."""
        listed = set(self.repo.al_entries())
        out: dict[str, list[dict[str, Any]]] = {"anilist": [], "mal": [], "mu": []}
        anilist = self._cache(key, "anilist") or {}
        ids = [m for m, _ in anilist.get("recs") or []]
        media = self.repo.media(ids)
        for m, rating in anilist.get("recs") or []:
            if m in media:
                row = media[m]
                out["anilist"].append({"title": title_of(row), "href": series_url(f"al:{m}"),
                                       "cover": row["cover_url"], "note": f"+{rating}" if rating else "similar tags",
                                       "listed": m in listed})
        mal = self._cache(key, "mal") or []
        media = self.repo.media([r["al_id"] for r in mal if r.get("al_id")])
        for r in mal:
            row = media.get(r.get("al_id"))
            out["mal"].append({"title": title_of(row) if row else r["title"],
                               "href": series_url(f"al:{r['al_id']}") if row else f"https://myanimelist.net/manga/{r['mal_id']}",
                               "cover": row["cover_url"] if row else None, "note": f"{r['votes']} votes",
                               "listed": bool(row) and row["media_id"] in listed, "external": not row})
        for r in (self._cache(key, "mu") or {}).get("recommendations") or []:
            out["mu"].append({"title": r["name"], "href": r["url"], "cover": r.get("cover"), "note": "", "listed": False,
                              "external": True})
        return out

    # ---- lookups ----------------------------------------------------------------------
    async def look_up(self, key: str, force: bool = False) -> list[str]:
        """Fetch readers' recommendations and the MangaUpdates record. Returns problems to show (if any)."""
        media, new, lib = self._rows(key)
        problems: list[str] = []
        titles = [t for t in ([new["title"]] if new else []) + ([title_of(media)] if media else []) + ([lib["title"]] if lib else [])]
        for row in (new, lib):
            if row:
                titles += json.loads(row["alt_titles"] or "[]")
        if media:
            titles += [t for t in (media["english"], media["native"], *json.loads(media["synonyms"] or "[]")) if t]

        al_id = media["media_id"] if media else None
        if titles and self.services.anilist_token() and (force or not fresh_enough(self.repo.cached(key, "anilist"))):
            try:
                anchor, recs = await fetch_neighbours(self.services.anilist, self.repo, media_id=al_id,
                                                      title=None if al_id else titles[0])
                if anchor and not al_id:
                    found = self.repo.media([anchor]).get(anchor)
                    names = {normalize(t) for t in titles}
                    if not found or not names & {normalize(t) for t in (found["romaji"], found["english"], found["native"]) if t}:
                        anchor, recs = None, []        # AniList's best title match was a different series
                self.repo.cache(key, "anilist", {"anchor": anchor, "recs": recs} if anchor else None)
                al_id = al_id or anchor
            except AniListError as exc:
                problems.append(f"AniList: {exc}")
        elif not al_id:
            al_id = (self._cache(key, "anilist") or {}).get("anchor")

        media = self.repo.media([al_id]).get(al_id) if al_id else media
        mal_id = (media["id_mal"] if media else None) or (new["mal_id"] if new else None)
        if mal_id and self.services.mal.connected and (force or not fresh_enough(self.repo.cached(key, "mal"))):
            try:
                recs = await mal_recommendations(self.services.mal, self.repo, mal_id)
                mapping = await anilist_by_mal(self.services.anilist, self.repo, [r["mal_id"] for r in recs]) \
                    if recs and self.services.anilist_token() else {}
                self.repo.cache(key, "mal", [{**r, "al_id": mapping.get(r["mal_id"])} for r in recs])
            except (MalError, AniListError) as exc:
                problems.append(f"MyAnimeList: {exc}")

        if media and not (new or lib) and (force or not fresh_enough(self.repo.cached(key, "md"))):
            try:
                found = await self._find_on_mangadex(media)
                self.repo.cache(key, "md", {"md_id": found} if found else None)
            except MangaDexError as exc:
                problems.append(f"MangaDex: {exc}")

        link = new["mu_id"] if new else None
        if not link and lib:
            link = json.loads(lib["links"] or "{}").get("mu")
        try:
            year = (media["start_year"] if media else None) or (new["year"] if new else None)
            await lookup_mu(self.services.mangaupdates, self.repo, key, titles, year, link, force=force)
        except MangaUpdatesError as exc:
            problems.append(f"MangaUpdates: {exc}")
        self.repo.cache(key, "checked", {"problems": problems})
        return problems

    async def _find_on_mangadex(self, media: Any) -> str | None:
        """The MangaDex id of an AniList series that isn't in your library or the scan: a title search
        (1 request, 2 at most), accepted only when MangaDex links back to this AniList or MAL id,
        since several series can share a title."""
        for title in dict.fromkeys(t for t in (media["romaji"], media["english"]) if t):
            body = await self.services.mangadex.get("/manga", {"title": title, "limit": 10,
                                                               "contentRating[]": ALL_CONTENT_RATINGS})
            for m in body.get("data") or []:
                links = (m.get("attributes") or {}).get("links")
                links = links if isinstance(links, dict) else {}
                if str(links.get("al") or "") == str(media["media_id"]) or (
                        media["id_mal"] and str(links.get("mal") or "") == str(media["id_mal"])):
                    return m["id"]
        return None

    def problems(self, key: str) -> list[str]:
        return (self._cache(key, "checked") or {}).get("problems") or []
