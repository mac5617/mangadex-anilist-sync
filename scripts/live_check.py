"""Read-only live probe of both APIs (story 09). Run it yourself:

    uv run python scripts/live_check.py          # sends about 5 AniList + 5 MangaDex read requests
    uv run python scripts/live_check.py --dry    # prints the plan, sends nothing

It only reads. It never changes anything on AniList or MangaDex. It uses the
app's own rate-limited clients at their default budgets, and prints no secrets.
Paste the printed report into docs/api-notes.md under "Live check <date>".
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import dotenv_values  # noqa: E402

from mdal.clients.anilist import AniListError  # noqa: E402
from mdal.clients.mangadex import ALL_CONTENT_RATINGS, MangaDexError  # noqa: E402
from mdal.config import ENV_FILE  # noqa: E402

REQUIRED_KEYS = [
    "MANGADEX_USERNAME", "MANGADEX_PASSWORD", "MANGADEX_CLIENT_ID", "MANGADEX_CLIENT_SECRET", "ANILIST_ACCESS_TOKEN",
]

PLAN = """Planned requests (all reads):
  AniList  1  Viewer
  AniList  2  MediaListCollection (your whole manga list)
  AniList  3  Page(perPage: 50) { media(id_in: first 50 list ids) }   -> is perPage 50 accepted?
  AniList  4  5 aliased Page searches in one document                 -> are aliased roots allowed?
  AniList  5  10 aliased Media(id) roots in one document              -> complexity signal for batch size 10
  MangaDex 1  token (password grant)
  MangaDex 2  /manga/status                                           -> library size
  MangaDex 3  /manga/read?grouped=true with up to 100 ids             -> is a 100-id chunk accepted?
  MangaDex 4  /chapter with up to 100 read ids, includeUnavailable=1
  MangaDex 5  /chapter, same ids, without includeUnavailable          -> how many are 'unavailable'
"""

LIST_QUERY = (
    "query ($userId: Int) { MediaListCollection(userId: $userId, type: MANGA) { "
    "lists { name isCustomList entries { id media { id title { romaji } } } } } }"
)
PAGE_QUERY = (
    "query ($ids: [Int]) { Page(page: 1, perPage: 50) { pageInfo { hasNextPage } "
    "media(id_in: $ids, type: MANGA) { id } } }"
)


def missing_keys(env_file: Path) -> list[str]:
    values = dotenv_values(env_file) if env_file.exists() else {}
    return [k for k in REQUIRED_KEYS if not (values.get(k) or "").strip()]


def search_document(n: int) -> str:
    params = ", ".join(f"$q{i}: String" for i in range(n))
    roots = " ".join(f"s{i}: Page(page: 1, perPage: 5) {{ media(search: $q{i}, type: MANGA) {{ id }} }}" for i in range(n))
    return f"query ({params}) {{ {roots} }}"


def media_roots_document(ids: list[int]) -> str:
    roots = " ".join(f"m{i}: Media(id: {media_id}) {{ id title {{ romaji }} chapters status }}" for i, media_id in enumerate(ids))
    return f"query {{ {roots} }}"


async def run_checks(services, out) -> None:
    al, md = services.anilist, services.mangadex

    async def step(label: str, coro):
        try:
            result = await coro
            out(f"- {label}: OK {result}")
            return True
        except (AniListError, MangaDexError) as exc:
            out(f"- {label}: FAILED ({exc.__class__.__name__}: {exc})")
            return False

    out(f"## Live check {date.today().isoformat()}")
    out("")

    state: dict = {}

    async def viewer():
        v = (await al.graphql("query { Viewer { id name } }"))["Viewer"]
        state["user_id"] = v["id"]
        return f"(user id {v['id']})"

    async def status():
        statuses = (await md.get("/manga/status")).get("statuses") or {}
        state["library"] = sorted(statuses)
        return f"({len(statuses)} series in library)"

    async def the_list():
        lists = (await al.graphql(LIST_QUERY, {"userId": state["user_id"]}))["MediaListCollection"]["lists"]
        entries, status_ids = {}, set()
        for group in lists:
            for e in group["entries"]:
                entries[e["id"]] = e
                if not group["isCustomList"]:
                    status_ids.add(e["id"])
        state["media"] = [e["media"] for e in entries.values()]
        custom_only = len(set(entries) - status_ids)
        names = ", ".join(g["name"] for g in lists)
        return f"({len(entries)} entries, {custom_only} only in custom lists; lists: {names})"

    async def page50():
        ids = [m["id"] for m in state["media"][:50]]
        media = (await al.graphql(PAGE_QUERY, {"ids": ids}))["Page"]["media"]
        return f"(asked {len(ids)}, got {len(media)})"

    async def search5():
        titles = [m["title"]["romaji"] for m in state["media"][:5]] or ["Berserk"]
        data = await al.graphql(search_document(len(titles)), {f"q{i}": t for i, t in enumerate(titles)})
        return f"({len(data)} aliased roots answered)"

    async def media10():
        ids = [m["id"] for m in state["media"][:10]] or [30002]
        data = await al.graphql(media_roots_document(ids))
        return f"({sum(1 for v in data.values() if v)} of {len(ids)} roots returned)"

    async def reads100():
        ids = state["library"][:100]
        data = (await md.get("/manga/read", {"ids[]": ids, "grouped": "true"})).get("data") or {}
        shape = "grouped object" if isinstance(data, dict) else f"{type(data).__name__}"
        state["read_ids"] = [c for chs in (data.values() if isinstance(data, dict) else []) for c in chs][:100]
        return f"({len(ids)} ids sent, {shape}, {sum(len(v) for v in data.values()) if isinstance(data, dict) else 0} read markers)"

    async def chapters(include_unavailable: bool):
        ids = state.get("read_ids") or []
        if not ids:
            return "(skipped: no read markers in the first 100 series)"
        params = {"ids[]": ids, "limit": 100, "contentRating[]": ALL_CONTENT_RATINGS}
        if include_unavailable:
            params["includeUnavailable"] = "1"
        data = (await md.get("/chapter", params)).get("data") or []
        nulls = sum(1 for c in data if (c.get("attributes") or {}).get("chapter") is None)
        return f"({len(ids)} asked, {len(data)} returned, {nulls} with null chapter number)"

    if not await step("AniList Viewer", viewer()):
        out("Stopping: AniList is not reachable with this token.")
        return
    await step("MangaDex login + /manga/status", status())
    if await step("AniList MediaListCollection", the_list()):
        await step("AniList Page perPage=50 id_in", page50())
        await step("AniList 5 aliased Page searches", search5())
        await step("AniList 10 aliased Media roots", media10())
    if state.get("library"):
        await step("MangaDex /manga/read grouped, 100 ids", reads100())
        await step("MangaDex /chapter with includeUnavailable=1", chapters(True))
        await step("MangaDex /chapter without includeUnavailable", chapters(False))
    out(f"- AniList rate-limit headers on the last response: {al.last_rate_headers or 'none'}")


def main(argv: list[str] | None = None, env_file: Path = ENV_FILE, out=print) -> int:
    parser = argparse.ArgumentParser(description="Read-only live check of the AniList and MangaDex APIs.")
    parser.add_argument("--dry", action="store_true", help="print the planned requests and send nothing")
    args = parser.parse_args(argv)

    missing = missing_keys(env_file)
    if missing:
        out("Missing in .env: " + ", ".join(missing))
        out("Connect AniList in the app (Settings) first; it stores ANILIST_ACCESS_TOKEN.")
        return 2
    if args.dry:
        out(PLAN)
        return 0

    from mdal.db.connection import connect
    from mdal.db.repo import Repo
    from mdal.config import Settings
    from mdal.logsetup import configure_logging
    from mdal.services import Services

    settings = Settings(_env_file=env_file)
    services = Services(env_file, Repo(connect(settings.ensure_db_dir())), settings)
    configure_logging(services.secret_values)

    async def go():
        try:
            await run_checks(services, out)
        finally:
            await services.aclose()

    asyncio.run(go())
    return 0


if __name__ == "__main__":
    sys.exit(main())
