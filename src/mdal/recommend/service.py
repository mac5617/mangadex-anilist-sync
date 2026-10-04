"""Refreshing recommendations (AniList reads) and asking the local model for picks, in the background.

A refresh: makes sure your list has tags (re-reading it once if not), fetches creators for your
strongest series, gathers candidates, then asks the model. Pages only read what was stored.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from mdal.clients.anilist import AniListError
from mdal.clients.ollama import OllamaClient, OllamaError, OllamaUnavailable
from mdal.db.repo import now_iso
from mdal.fetch.anilist_list import fetch_list, fetch_staff_for
from mdal.fetch.anilist_recs import fetch_candidates
from mdal.recommend.llm import CANDIDATES, SCHEMA, SYSTEM, build_prompt, clean_answer
from mdal.recommend.profile import Profile, build_profile, entry_weight
from mdal.recommend.score import Rec, ranked, score_recs
from mdal.stats import entry_rows

if TYPE_CHECKING:
    from mdal.services import Services

log = logging.getLogger(__name__)

STAFF_FOR_TOP = 150        # creators are fetched for this many of your highest-weight series
STAFF_REQUESTS = 6         # 25 series each


class RecsBusy(Exception):
    pass


class Recommender:
    def __init__(self, services: Services) -> None:
        self.services = services
        self.task: asyncio.Task[None] | None = None
        self._ollama: OllamaClient | None = None

    @property
    def repo(self):
        return self.services.repo

    @property
    def busy(self) -> bool:
        return bool(self.task and not self.task.done())

    def ollama(self) -> OllamaClient:
        if self._ollama is None:
            self._ollama = OllamaClient(self.services.settings.ollama_url)
        return self._ollama

    async def aclose(self) -> None:
        if self._ollama is not None:
            await self._ollama.aclose()

    # ---- reading what is stored -------------------------------------------
    def profile(self) -> tuple[Profile, dict[int, float]]:
        entries = entry_rows(self.repo)
        weights = {e["media_id"]: w for e in entries
                   if (w := entry_weight(e["status"], e["score"], e["progress"])) is not None}
        return build_profile(entries), weights

    def recs(self, include_adult: bool = False) -> tuple[Profile, list[Rec]]:
        profile, weights = self.profile()
        listed = set(self.repo.al_entries())
        in_library = {r[0] for r in self.repo.conn.execute(
            "SELECT al_media_id FROM mapping WHERE al_media_id IS NOT NULL AND state IN ('auto','confirmed')")}
        hidden = self.repo.hidden_recs()
        rows = [dict(r) for r in self.repo.rec_candidates()
                if r["media_id"] not in listed | in_library | hidden
                and (r["format"] or "MANGA") in profile.formats
                and (include_adult or not r["is_adult"])]
        return profile, score_recs(profile, rows, weights)

    def status(self) -> dict[str, Any]:
        return self.repo.get_setting("rec_status") or {}

    def _status(self, **fields: Any) -> None:
        self.repo.set_setting("rec_status", {**self.status(), **fields})

    # ---- refresh ------------------------------------------------------------
    def start_refresh(self) -> None:
        if self.busy:
            raise RecsBusy("Recommendations are already being refreshed.")
        self.repo.set_setting("rec_status", {"state": "running", "detail": "starting", "started_at": now_iso()})
        self.task = asyncio.create_task(self._refresh())

    def start_llm(self) -> None:
        if self.busy:
            raise RecsBusy("Recommendations are already being refreshed.")
        self.repo.set_setting("rec_status", {**self.status(), "state": "running", "detail": "asking the model",
                                             "started_at": now_iso(), "error": None})
        self.task = asyncio.create_task(self._llm_only())

    async def _refresh(self) -> None:
        s, repo = self.services, self.repo
        say = lambda msg: self._status(detail=msg)  # noqa: E731
        try:
            entries = entry_rows(repo)
            sent = 0
            if not entries or sum(1 for e in entries if e["tags_known"]) < len(entries) / 2:
                say("reading your AniList list (for tags)")
                await fetch_list(s.anilist, repo, await s.orchestrator.user_id())
                sent += 1
                entries = entry_rows(repo)
            weights = sorted(((w, e["media_id"]) for e in entries
                              if (w := entry_weight(e["status"], e["score"], e["progress"])) is not None), reverse=True)
            known = {e["media_id"] for e in entries if e["staff_known"]}
            wanted = [m for _, m in weights[:STAFF_FOR_TOP] if m not in known]
            if wanted:
                before = len(wanted)
                left = await fetch_staff_for(s.anilist, repo, wanted, STAFF_REQUESTS, say)
                sent += -(-(before - left) // 25)
            profile, _ = self.profile()
            collected, n = await fetch_candidates(s.anilist, repo, profile, say)
            sent += n
            repo.replace_rec_candidates([{"media_id": i, "sources": json.dumps(src, ensure_ascii=False),
                                          "fetched_at": collected.fetched_at} for i, src in collected.sources.items()])
            _, recs = self.recs()
            self._status(state="running", detail=f"{len(recs)} candidates; asking the model",
                         requests=sent, candidates=len(recs))
            note = await self._ask_model()
            self._status(state="done", detail=f"{len(recs)} candidates from {sent} AniList requests" + (f"; {note}" if note else ""),
                         finished_at=now_iso(), error=None)
        except AniListError as exc:
            log.warning("recommendation refresh failed: %s", exc)
            self._status(state="failed", error=f"AniList: {exc}", finished_at=now_iso())
        except Exception as exc:
            log.exception("recommendation refresh failed")
            self._status(state="failed", error=f"{exc.__class__.__name__}: {exc}", finished_at=now_iso())

    async def _llm_only(self) -> None:
        try:
            note = await self._ask_model()
            self._status(state="done", detail=note or "picks updated", finished_at=now_iso())
        except Exception as exc:
            log.exception("asking the model failed")
            self._status(state="failed", error=f"{exc.__class__.__name__}: {exc}", finished_at=now_iso())

    # ---- the model ----------------------------------------------------------
    async def models(self) -> list[dict[str, Any]]:
        try:
            return await self.ollama().models()
        except OllamaError:
            return []

    async def _ask_model(self) -> str | None:
        """Store the model's picks. Returns a note when it could not be asked (the rest still works)."""
        model = self.repo.get_setting("ollama_model")
        previous = self.repo.get_setting("rec_llm") or {}
        try:
            available = {m["name"]: m for m in await self.ollama().models()}
            if model not in available:
                raise OllamaUnavailable(f"model {model!r} is not installed in Ollama")
            profile, recs = self.recs()
            top = ranked(recs, "overall", CANDIDATES)
            if not top:
                raise OllamaError("no candidates to choose from yet")
            self._status(detail=f"asking {model} to pick from {len(top)} candidates")
            answer = await self.ollama().chat_json(model, SYSTEM, build_prompt(profile, top), SCHEMA,
                                                   available[model].get("capabilities"))
            summary, picks = clean_answer(answer, {r.media_id for r in top})
            if len(picks) < 3:
                raise OllamaError(f"the model returned only {len(picks)} usable picks")
        except OllamaError as exc:
            self.repo.set_setting("rec_llm", {**previous, "error": str(exc), "error_at": now_iso()})
            return f"model not used: {exc}"
        self.repo.set_setting("rec_llm", {"model": model, "summary": summary, "picks": picks,
                                          "created_at": now_iso(), "error": None})
        return None
