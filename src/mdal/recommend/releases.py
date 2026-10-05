"""New releases (a MangaDex scan, scored and picked in the background) and the Ask chat.

A scan: makes sure your list has descriptions (one AniList request if not), reads the latest
MangaDex series and your follows, compares descriptions with the embedding model when one is
installed, then asks the chat model to pick. Pages only read what was stored.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from mdal.clients.anilist import AniListError
from mdal.clients.mangadex import MangaDexError
from mdal.clients.ollama import OllamaError, OllamaUnavailable
from mdal.db.repo import now_iso
from mdal.fetch.anilist_list import fetch_list, plain_text
from mdal.fetch.anilist_recs import fetch_neighbours
from mdal.fetch.mal_recs import anilist_by_mal, mal_recommendations
from mdal.clients.myanimelist import MalError
from mdal.fetch.mangadex_new import fetch_new_releases
from mdal.fetch.mangaupdates import lookup_mu
from mdal.clients.mangaupdates import MangaUpdatesError
from mdal.matching.normalize import normalize
from mdal.recommend import chat, fresh
from mdal.recommend.fresh import NewRec, Shelf
from mdal.recommend.feedback import detect_verdict, stronger
from mdal.recommend.profile import Profile, entry_weight
from mdal.recommend.ratings import rate
from mdal.recommend.score import ranked, score_recs
from mdal.stats import COUNTRY_LABELS, entry_rows

if TYPE_CHECKING:
    from mdal.services import Services

log = logging.getLogger(__name__)

LIKED_SHELF = 150       # your best-liked series compared with each new one
DISLIKED_SHELF = 40
EMBED_BATCH = 32
MIN_TITLE = 4           # shorter normalised titles are too generic to exclude by
SCHEDULE_START, SCHEDULE_CHECK = 120.0, 600.0   # seconds: first check after start-up, then every 10 minutes
SEEN_KEEP = 2000
FOLLOW_UP = 0.35        # weight of the previous question when picking the shortlist
ANCHOR = 0.7            # weight of a named series' description against the question's own wording
NEIGHBOUR_BOOST = 0.6   # lift for series AniList readers recommend for a named series (relevance is 0..1)
VERDICT_PAST = {"loved": "loved", "liked": "liked", "disliked": "didn't like"}
TAG_SHARE = 0.8         # weight of sharing a named series' tags, next to its description (0..1)


def scaled(values: dict[str, float]) -> dict[str, float]:
    """0..1 across the pool, so signals measured differently can be added."""
    if not values:
        return {}
    low, high = min(values.values()), max(values.values())
    return {k: (v - low) / (high - low) if high > low else 0.0 for k, v in values.items()}


class ScanBusy(Exception):
    pass


class NewReleases:
    def __init__(self, services: Services) -> None:
        self.services = services
        self.task: asyncio.Task[None] | None = None

    @property
    def repo(self):
        return self.services.repo

    @property
    def busy(self) -> bool:
        return bool(self.task and not self.task.done())

    def ollama(self):
        return self.services.recommender.ollama()

    def status(self) -> dict[str, Any]:
        return self.repo.get_setting("new_status") or {}

    def _status(self, **fields: Any) -> None:
        self.repo.set_setting("new_status", {**self.status(), **fields})

    # ---- what you already have ---------------------------------------------------
    def _owned(self) -> tuple[set[str], set[int], set[int], set[str]]:
        """(MangaDex ids, AniList ids, MAL ids, normalised titles) of everything on your lists or followed."""
        conn = self.repo.conn
        md = {r[0] for r in conn.execute("SELECT md_id FROM md_manga")} | self.repo.md_follows()
        al = set(self.repo.al_entries()) | {r[0] for r in conn.execute(
            "SELECT al_media_id FROM mapping WHERE al_media_id IS NOT NULL AND state IN ('auto','confirmed')")}
        mal = set(self.repo.mal_entries())
        titles: set[str] = set()
        for m in self.repo.media(al).values():
            if m["id_mal"]:
                mal.add(m["id_mal"])
            titles.update(normalize(t) for t in (m["romaji"], m["english"], m["native"], *json.loads(m["synonyms"] or "[]")))
        for r in conn.execute("SELECT title, alt_titles FROM md_manga"):
            titles.update(normalize(t) for t in (r["title"], *json.loads(r["alt_titles"] or "[]")))
        return md, al, mal, {t for t in titles if len(t) >= MIN_TITLE}

    def recs(self, include_adult: bool = False) -> tuple[Profile, list[NewRec]]:
        profile, _ = self.services.recommender.profile()
        md, al, mal, titles = self._owned()
        rated = set(self.repo.feedback())
        skip = md | self.repo.hidden_new() | {k[3:] for k in rated if k.startswith("md:")}
        al = al | {int(k[3:]) for k in rated if k.startswith("al:")}
        rows = []
        for r in self.repo.new_releases():
            if r["md_id"] in skip or r["al_id"] in al or r["mal_id"] in mal:
                continue
            if any(normalize(t) in titles for t in (r["title"], *json.loads(r["alt_titles"] or "[]"))):
                continue
            if not include_adult and r["content_rating"] in fresh.ADULT_RATINGS:
                continue
            rows.append(dict(r))
        return profile, fresh.score_new(profile, rows)

    # ---- the scan ------------------------------------------------------------------
    def start_scan(self, scheduled: bool = False) -> None:
        if self.busy:
            raise ScanBusy("A scan is already running.")
        self.repo.set_setting("new_status", {"state": "running", "detail": "starting", "started_at": now_iso(),
                                             "scheduled": scheduled})
        self.task = asyncio.create_task(self._scan())

    # ---- the background scan and the Home digest -------------------------------------
    def next_scan(self) -> datetime | None:
        """When the background scan is next due; None when it's off."""
        hours = self.repo.get_setting("new_scan_hours")
        if not hours:
            return None
        last = self.status().get("finished_at") or self.status().get("started_at")
        return datetime.fromisoformat(last) + timedelta(hours=hours) if last else datetime.now(UTC)

    def scan_due(self) -> bool:
        s = self.services
        due = self.next_scan()
        if due is None or datetime.now(UTC) < due or self.busy or not s.settings.mangadex_username:
            return False
        if s.orchestrator.lock.locked() or (s.orchestrator.task and not s.orchestrator.task.done()):
            return False                               # a sync is using MangaDex
        cooldown = s.mangadex_guard.cooldown()
        return not (cooldown and cooldown[0] > time.time())

    async def run_schedule(self, sleep=asyncio.sleep) -> None:
        """Runs for the app's lifetime: starts a scan whenever one is due."""
        await sleep(SCHEDULE_START)
        while True:
            try:
                if self.scan_due():
                    log.info("starting the scheduled new-release scan")
                    self.start_scan(scheduled=True)
            except Exception:
                log.exception("scheduled scan check failed")
            await sleep(SCHEDULE_CHECK)

    def _update_digest(self) -> None:
        """The scan's picks you haven't seen on New releases yet, for Home."""
        picks = [p["md_id"] for p in (self.repo.get_setting("new_llm") or {}).get("picks") or []]
        if not picks:
            _, recs = self.recs()
            picks = [r.md_id for r in fresh.ranked_new(recs, fresh.PICKS)]
        seen = set(self.repo.get_setting("new_seen") or [])
        self.repo.set_setting("new_digest", {"at": now_iso(), "md_ids": [m for m in picks if m not in seen]})

    def mark_seen(self, md_ids: list[str]) -> None:
        seen = [m for m in self.repo.get_setting("new_seen") or [] if m not in md_ids] + md_ids
        self.repo.set_setting("new_seen", seen[-SEEN_KEEP:])
        self.repo.set_setting("new_digest", None)

    def digest(self) -> list[NewRec]:
        """New picks since you last opened New releases, best first (still not read, hidden or rated)."""
        wanted = (self.repo.get_setting("new_digest") or {}).get("md_ids") or []
        if not wanted:
            return []
        _, recs = self.recs()
        by_id = {r.md_id: r for r in recs}
        return [by_id[m] for m in wanted if m in by_id]

    def start_llm(self) -> None:
        if self.busy:
            raise ScanBusy("A scan is already running.")
        self.repo.set_setting("new_status", {**self.status(), "state": "running", "detail": "asking the model",
                                             "started_at": now_iso(), "error": None})
        self.task = asyncio.create_task(self._llm_only())

    async def _scan(self) -> None:
        s, repo = self.services, self.repo
        say = lambda msg: self._status(detail=msg)  # noqa: E731
        try:
            entries = entry_rows(repo)
            described = repo.descriptions(e["media_id"] for e in entries)
            if s.anilist_token() and (not entries or len(described) < len(entries) / 2):
                say("reading your AniList list (for descriptions)")
                await fetch_list(s.anilist, repo, await s.orchestrator.user_id())
            found, requests = await fetch_new_releases(s.mangadex, repo, repo.get_setting("new_language"), say)
            notes = [note] if (note := await self._compare_descriptions()) else []
            _, recs = self.recs()
            self._status(detail=f"{len(recs)} new to you; asking the model", requests=requests, candidates=len(recs))
            if note := await self._ask_model():
                notes.append(note)
            if note := await self._english_releases():
                notes.append(note)
            self._update_digest()
            self._status(state="done", finished_at=now_iso(), error=None,
                         detail=f"{len(recs)} of {found} series are new to you ({requests} MangaDex requests)"
                         + "".join(f"; {n}" for n in notes))
        except (MangaDexError, AniListError) as exc:
            log.warning("new-release scan failed: %s", exc)
            self._status(state="failed", error=str(exc), finished_at=now_iso())
        except Exception as exc:
            log.exception("new-release scan failed")
            self._status(state="failed", error=f"{exc.__class__.__name__}: {exc}", finished_at=now_iso())

    async def _llm_only(self) -> None:
        try:
            note = await self._ask_model()
            self._status(state="done", detail=note or "picks updated", finished_at=now_iso())
        except Exception as exc:
            log.exception("asking the model failed")
            self._status(state="failed", error=f"{exc.__class__.__name__}: {exc}", finished_at=now_iso())

    async def _english_releases(self) -> str | None:
        """Look up the model's picks on MangaUpdates (English chapters, licensing) so their cards can show it."""
        picks = [p["md_id"] for p in (self.repo.get_setting("new_llm") or {}).get("picks") or []]
        if not picks:
            return None
        rows = {r["md_id"]: r for r in self.repo.new_releases() if r["md_id"] in picks}
        self._status(detail=f"checking English releases on MangaUpdates for {len(rows)} picks")
        try:
            for md_id, r in rows.items():
                titles = [r["title"], *json.loads(r["alt_titles"] or "[]")]
                await lookup_mu(self.services.mangaupdates, self.repo, f"md:{md_id}", titles, r["year"], r["mu_id"])
        except MangaUpdatesError as exc:
            return f"MangaUpdates not used: {exc}"
        return None

    # ---- embeddings ----------------------------------------------------------------
    async def embed_model(self) -> str | None:
        """The configured embedding model when Ollama has it, else None (description matching is skipped)."""
        model = self.repo.get_setting("embed_model")
        try:
            names = {m["name"] for m in await self.ollama().models()}
        except OllamaError:
            return None
        return model if model in names or f"{model}:latest" in names else None

    async def vectors(self, model: str, texts: dict[str, str]) -> dict[str, list[float]]:
        """ref -> unit vector, embedding only texts that changed since last time."""
        def digest(text: str) -> str:
            return hashlib.sha1(text.encode()).hexdigest()

        cached = self.repo.embeddings(model, texts)
        out = {ref: fresh.unpack(blob) for ref, (h, blob) in cached.items() if h == digest(texts[ref])}
        todo = [ref for ref in texts if ref not in out]
        for start in range(0, len(todo), EMBED_BATCH):
            chunk = todo[start : start + EMBED_BATCH]
            vectors = await self.ollama().embed(model, [texts[r] for r in chunk])
            rows = [(ref, digest(texts[ref]), fresh.pack(v)) for ref, v in zip(chunk, vectors)]
            self.repo.save_embeddings(model, rows)
            out.update({ref: fresh.unpack(blob) for ref, _, blob in rows})
        return out

    def _shelf_texts(self) -> tuple[dict[str, str], dict[str, tuple[float, str]]]:
        """Texts of your best-liked and most-disliked described series, and ref -> (weight, title)."""
        entries = entry_rows(self.repo)
        _, weights = self.services.recommender.profile()
        described = self.repo.descriptions(weights)
        rows = sorted((e for e in entries if e["media_id"] in described), key=lambda e: -weights[e["media_id"]])
        liked = [e for e in rows if weights[e["media_id"]] >= 0.3][:LIKED_SHELF]
        disliked = [e for e in reversed(rows) if weights[e["media_id"]] < 0][:DISLIKED_SHELF]
        texts, info = {}, {}
        for e in liked + disliked:
            ref = f"lib:{e['media_id']}"
            texts[ref] = fresh.doc_text(e["title"], e["genres"] + e["tags"], plain_text(described[e["media_id"]]))  # older rows kept the notes
            info[ref] = (weights[e["media_id"]], e["title"])
        # Series you rated here count too, with the description saved alongside the verdict.
        for e in self.services.recommender.rated_entries():
            w = entry_weight(e["status"], e["score"], e["progress"])
            if e["description"] and w is not None and (w >= 0.3 or w < 0):
                ref = f"rated:{e['key']}"
                texts[ref] = fresh.doc_text(e["title"], e["genres"] + e["tags"], e["description"])
                info[ref] = (w, e["title"])
        return texts, info

    async def _compare_descriptions(self) -> str | None:
        """Store each new series' description match. Returns a note when it was skipped."""
        model = await self.embed_model()
        if model is None:
            return f"description matching skipped (install {self.repo.get_setting('embed_model')} in Ollama)"
        texts, info = self._shelf_texts()
        if not texts:
            return "description matching skipped (no descriptions on your list yet)"
        self._status(detail=f"comparing descriptions with {model}")
        rows = [r for r in self.repo.new_releases() if r["description"]]
        new_texts = {f"md:{r['md_id']}": fresh.doc_text(r["title"], [t["name"] for t in json.loads(r["tags"])], r["description"])
                     for r in rows}
        try:
            vectors = await self.vectors(model, {**texts, **new_texts})
        except OllamaError as exc:
            return f"description matching skipped: {exc}"
        shelf = Shelf(liked=[(w, t, vectors[ref]) for ref, (w, t) in info.items() if w >= 0],
                      disliked=[(w, t, vectors[ref]) for ref, (w, t) in info.items() if w < 0])
        self.repo.set_similarity({ref[3:]: fresh.similarity(vectors[ref], shelf) for ref in new_texts})
        return None

    # ---- the model -----------------------------------------------------------------
    async def _ask_model(self) -> str | None:
        """Store the model's picks. Returns a note when it could not be asked (the ranking still works)."""
        model = self.repo.get_setting("ollama_model")
        previous = self.repo.get_setting("new_llm") or {}
        try:
            available = {m["name"]: m for m in await self.ollama().models()}
            if model not in available:
                raise OllamaUnavailable(f"model {model!r} is not installed in Ollama")
            profile, recs = self.recs()
            top = fresh.ranked_new(recs, fresh.CANDIDATES)
            if not top:
                raise OllamaError("no new series to choose from")
            self._status(detail=f"asking {model} to read {len(top)} descriptions")
            answer = await self.ollama().chat_json(model, fresh.SYSTEM, fresh.build_prompt(profile, top), fresh.SCHEMA,
                                                   available[model].get("capabilities"))
            picks = fresh.clean_picks(answer, top)
            if len(picks) < 3:
                raise OllamaError(f"the model returned only {len(picks)} usable picks")
        except OllamaError as exc:
            self.repo.set_setting("new_llm", {**previous, "error": str(exc), "error_at": now_iso()})
            return f"model not used: {exc}"
        self.repo.set_setting("new_llm", {"model": model, "picks": picks, "created_at": now_iso(), "error": None})
        return None

    # ---- Ask -----------------------------------------------------------------------
    def pool(self, include_adult: bool = False) -> tuple[Profile, list[chat.Item]]:
        """Everything you could be suggested: new MangaDex releases and AniList candidates.

        Taste is scaled 0..1 within each source: their scores are built differently, so one source's best
        would otherwise always outrank the other's."""
        profile, new = self.recs(include_adult)
        _, al_recs = self.services.recommender.recs(include_adult)
        mu = self.repo.cached_all("mu")      # English chapters and licensing, where MangaUpdates was asked
        md_items = [chat.Item(
            key=f"md:{r.md_id}", title=r.title, url=r.url, cover=r.cover, meta=r.meta() + fresh.english_meta(mu.get(f"md:{r.md_id}")), tags=r.tags,
            description=r.description, taste=max(0.0, r.scores["overall"]), adult=r.adult, source="MangaDex",
            similar_to=r.similar_to) for r in new]
        new_al = {r.al_id for r in new if r.al_id}
        descriptions = self.repo.descriptions(r.media_id for r in al_recs)
        al_items = [self._al_item(r, descriptions.get(r.media_id), english=fresh.english_meta(mu.get(f"al:{r.media_id}")))
                    for r in ranked(al_recs, "overall") if r.media_id not in new_al]
        for group in (md_items, al_items):
            best = max((i.taste for i in group), default=0.0)
            for i in group:
                i.taste = i.taste / best if best > 0 else 0.0
        return profile, md_items + al_items

    @staticmethod
    def _al_item(r: Any, description: str | None, source: str = "AniList", english: list[str] = ()) -> chat.Item:
        meta = [m for m in (str(r.year) if r.year else None, COUNTRY_LABELS.get(r.country or "", r.country),
                            (r.status or "").lower() or None, f"{r.chapters} ch" if r.chapters else None) if m]
        meta += list(english)
        return chat.Item(
            key=f"al:{r.media_id}", title=r.title, url=r.url or f"https://anilist.co/manga/{r.media_id}",
            cover=r.cover, meta=meta, tags=r.genres + [t["name"] for t in r.tags], description=description or None,
            taste=max(0.0, r.scores["overall"]), adult=r.is_adult, source=source)

    def _title_index(self, items: list[chat.Item]) -> dict[str, tuple[str, str]]:
        """Normalised title -> (key, title) for everything the reader might name: the pool and their own list."""
        index: dict[str, tuple[str, str]] = {}
        alts = {f"md:{r['md_id']}": json.loads(r["alt_titles"] or "[]") for r in self.repo.new_releases()}
        for i in items:
            for name in (i.title, *alts.get(i.key, [])):
                index.setdefault(normalize(name), (i.key, i.title))
        listed = self.repo.media(self.repo.al_entries())
        for m in listed.values():
            title = m["romaji"] or m["english"] or m["native"] or f"#{m['media_id']}"
            for name in (m["romaji"], m["english"], m["native"], *json.loads(m["synonyms"] or "[]")):
                if name:
                    index[normalize(name)] = (f"lib:{m['media_id']}", title)   # your list wins a tie
        return index

    def _named(self, keys: list[tuple[str, str]], items: list[chat.Item]) -> list[tuple[chat.Named, int | None, str]]:
        """(what the model is told, its AniList id when known, its text for description matching)."""
        by_key = {i.key: i for i in items}
        md_al = {f"md:{r['md_id']}": r["al_id"] for r in self.repo.new_releases()}
        out = []
        for key, title in keys:
            if key.startswith("lib:"):
                media_id = int(key[4:])
                m = self.repo.media([media_id]).get(media_id)
                tags = json.loads(m["genres"] or "[]") + [t["name"] for t in json.loads(m["tags"] or "[]")] if m else []
                description = plain_text(m["description"]) if m and m["description"] else None
                out.append((chat.Named(title, description, tags, on_list=True), media_id,
                            fresh.doc_text(title, tags, description)))
            elif item := by_key.get(key):
                al_id = int(key[3:]) if key.startswith("al:") else md_al.get(key)
                out.append((chat.Named(title, item.description, item.tags, on_list=False), al_id,
                            fresh.doc_text(item.title, item.tags, item.description)))
        return out

    async def _neighbours(self, named: list[tuple[chat.Named, int | None, str]], items: list[chat.Item],
                          include_adult: bool) -> tuple[list[chat.Item], dict[str, float]]:
        """What AniList (and, when connected, MyAnimeList) readers recommend for the named series: new items, and
        a 0..1 boost per key for the series they recommend. Nothing when AniList isn't connected or doesn't answer."""
        if not self.services.anilist_token():
            return [], {}
        found: dict[int, tuple[int, str]] = {}   # media id -> (best rating, the named title)
        for n, al_id, _ in named:
            try:
                anchor, recs = await fetch_neighbours(self.services.anilist, self.repo, media_id=al_id,
                                                      title=None if al_id else n.title)
                recs += await self._mal_neighbours(anchor)
            except (AniListError, MalError) as exc:
                log.warning("looking up readers' recommendations for %r failed: %s", n.title, exc)
                continue
            for media_id, rating in recs:
                if media_id != anchor and rating > found.get(media_id, (-1, ""))[0]:
                    found[media_id] = (rating, n.title)
        if not found:
            return [], {}
        _, owned_al, _, owned_titles = self._owned()
        hidden = self.repo.hidden_recs()
        in_pool = {i.key for i in items}
        profile, weights = self.services.recommender.profile()
        rows = [dict(r, sources=json.dumps([{"kind": "community", "via": 0, "label": found[r["media_id"]][1],
                                             "rating": found[r["media_id"]][0]}]))
                for r in self.repo.media(found).values()
                if r["media_id"] not in owned_al | hidden and (include_adult or not r["is_adult"])
                and normalize(r["romaji"] or r["english"] or "") not in owned_titles]
        extra = []
        for r in score_recs(profile, rows, weights):
            if f"al:{r.media_id}" not in in_pool:
                row = next(x for x in rows if x["media_id"] == r.media_id)
                extra.append(self._al_item(r, row.get("description"), source=f"AniList · readers of {found[r.media_id][1]}"))
        best = max((i.taste for i in extra), default=0.0)
        for i in extra:
            i.taste = i.taste / best if best > 0 else 0.0
        top = max(rating for rating, _ in found.values()) or 1
        boost = {f"al:{m}": 0.5 + 0.5 * max(rating, 0) / top for m, (rating, _) in found.items()}
        return extra, boost

    async def _mal_neighbours(self, anchor: int | None) -> list[tuple[int, int]]:
        """[(AniList id, MAL readers' votes)] for a series, when MyAnimeList is connected and knows it."""
        m = self.repo.media([anchor]).get(anchor) if anchor else None
        if not (m and m["id_mal"] and self.services.mal.connected):
            return []
        recs = await mal_recommendations(self.services.mal, self.repo, m["id_mal"])
        mapping = await anilist_by_mal(self.services.anilist, self.repo, [r["mal_id"] for r in recs]) if recs else {}
        return [(mapping[r["mal_id"]], r["votes"]) for r in recs if r["mal_id"] in mapping]

    async def _relevance(self, question: str, items: list[chat.Item], anchors: list[str] = ()) -> dict[str, float]:
        """How well each series fits the question: by meaning with an embedding model, else by shared words.
        Named series (`anchors`, their text) weigh most: "like this" means like their descriptions."""
        model = await self.embed_model()
        if model is None:
            words = question + " " + " ".join(anchors)
            return chat.keyword_relevance(words, items)
        texts = {i.key: fresh.doc_text(i.title, i.tags, i.description) for i in items}
        try:
            vectors = await self.vectors(model, texts)
            query = fresh.unit((await self.ollama().embed(model, [fresh.query_text(question)]))[0])
            refs = [fresh.unit(v) for v in await self.ollama().embed(model, list(anchors))] if anchors else []
        except OllamaError:
            return chat.keyword_relevance(question, items)
        out = {}
        for key, v in vectors.items():
            asked = fresh.dot(query, v)
            out[key] = (ANCHOR * max(fresh.dot(r, v) for r in refs) + (1 - ANCHOR) * asked) if refs else asked
        return out

    async def ask(self, question: str, include_adult: bool = False) -> dict[str, Any]:
        """Answer one chat message. Both messages are stored; raises OllamaError when the model can't answer."""
        model = self.repo.get_setting("ollama_model")
        available = {m["name"]: m for m in await self.ollama().models()}
        if model not in available:
            raise OllamaUnavailable(f"The model {model} is not installed in Ollama.")
        profile, items = self.pool(include_adult)
        if not items:
            raise OllamaError("There is nothing to suggest yet: scan new releases or refresh For you first.")
        history = [dict(m, picks=json.loads(m["picks"]) if m["picks"] else None) for m in self.repo.chat_messages(50)]
        titles = {i.key: i.title for i in items}

        # A series the question names is the reference, never a suggestion; AniList adds what its readers recommend.
        index = self._title_index(items)
        named_keys = chat.find_named(question, index)
        named = self._named(named_keys, items)
        items = [i for i in items if i.key not in {k for k, _ in named_keys}]
        extra, boost = await self._neighbours(named, items, include_adult) if named else ([], {})
        items += extra
        titles.update({i.key: i.title for i in extra})

        # Series already suggested in this conversation aren't offered again (unless nothing else is left),
        # and one you named earlier ("I just read X") stays read for the rest of it.
        suggested = {p["key"] for m in history for p in m.get("picks") or []}
        suggested |= {k for m in history if m["role"] == "user" for k, _ in chat.find_named(m["content"], index)}
        items = [i for i in items if i.key not in suggested] or items
        # A genre or tag the question names is a filter, not a hint: "an isekai" only offers isekai.
        want, avoid = chat.asked_tags(question, items)
        items = chat.filter_by_tags(items, want, avoid) or items
        focused = bool(named or want or avoid)

        relevance = scaled(await self._relevance(question, items, [text for _, _, text in named]))
        if named:
            # "Like this" also means sharing what it is about: its tags count as much as its description.
            overlap = chat.tag_overlap([t for n, _, _ in named for t in n.tags], items)
            relevance = {k: v + TAG_SHARE * overlap.get(k, 0.0) for k, v in relevance.items()}
        elif not focused and (earlier := next((m["content"] for m in reversed(history) if m["role"] == "user"), "")):
            # A vague follow-up ("more like that, but finished") also leans a little on the question before it.
            before = scaled(await self._relevance(earlier, items))
            relevance = {k: v + FOLLOW_UP * before.get(k, 0.0) for k, v in relevance.items()}
        relevance = {k: v + NEIGHBOUR_BOOST * boost.get(k, 0.0) for k, v in relevance.items()}
        candidates = chat.shortlist(items, relevance, taste=chat.FOCUSED_TASTE if focused else chat.TASTE)
        messages = chat.build_messages(profile, history, titles, question, candidates, [n for n, _, _ in named])
        answer = await self.ollama().chat_messages(model, messages, chat.SCHEMA, available[model].get("capabilities"))
        earlier_picks = [titles[p["key"]] for m in history for p in m.get("picks") or [] if p["key"] in titles]
        named_titles = {n.title for n, _, _ in named}
        reply, picks = chat.clean_reply(answer, candidates, [t for t in earlier_picks if t not in named_titles])
        if not reply and not picks:
            raise OllamaError("The model gave an empty answer. Try asking again.")
        notes = self._learn(question, named_keys)
        self.repo.add_chat_message("user", question)
        self.repo.add_chat_message("assistant", reply, picks, notes)
        return {"reply": reply, "picks": picks, "model": model, "named": sorted(named_titles), "notes": notes}

    def _learn(self, question: str, named_keys: list[tuple[str, str]]) -> list[str]:
        """Save what the message says about a series it names ("I just read X and loved it"). Series on your
        AniList list are left alone: your list is the record for those. Returns notes for the reader."""
        verdict = detect_verdict(question)
        if verdict is None:
            return []
        saved = self.repo.feedback()
        notes = []
        for key, title in named_keys:
            if key.startswith("lib:") or not stronger(verdict, (saved.get(key) or {"verdict": None})["verdict"]):
                continue
            if rate(self.repo, key, verdict, "ask"):
                notes.append(f"Saved: you've read {title}, so it won't be suggested." if verdict == "read"
                             else f"Saved: you {VERDICT_PAST[verdict]} {title}. It now counts towards your taste.")
        return notes

    def cards_pool(self, include_adult: bool = False, items: list[chat.Item] | None = None) -> dict[str, chat.Item]:
        """key -> series for showing the conversation: the pool (`items`, when already built), plus AniList series
        a lookup added earlier (cached in al_media) that are still not on your list or hidden."""
        if items is None:
            _, items = self.pool(include_adult)
        by_key = {i.key: i for i in items}
        wanted = {p["key"] for m in self.repo.chat_messages() for p in json.loads(m["picks"] or "[]")}
        missing = [int(k[3:]) for k in wanted if k.startswith("al:") and k not in by_key]
        if missing:
            _, owned_al, _, _ = self._owned()
            hidden = self.repo.hidden_recs()
            profile, weights = self.services.recommender.profile()
            rows = [dict(r, sources="[]") for r in self.repo.media(missing).values()
                    if r["media_id"] not in owned_al | hidden and (include_adult or not r["is_adult"])]
            for r in score_recs(profile, rows, weights):
                description = next(x["description"] for x in rows if x["media_id"] == r.media_id)
                by_key[f"al:{r.media_id}"] = self._al_item(r, description)
        return by_key
