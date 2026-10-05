""""Add to AniList" for matched series that are not on the user's list (story 17, FR-30).

The only code path that creates list entries, and only on an explicit per-item click.
One SaveMediaListEntry(mediaId, status, progress) per add; recorded as a one-item run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from collections.abc import Awaitable, Callable

from mdal import titles
from mdal.clients.anilist import AniListClient, AniListGraphQLError
from mdal.db.repo import Repo, now_iso
from mdal.fetch.anilist_list import fetch_list
from mdal.sync.rules import STATUS_LABELS, AlMediaInfo, MdInfo, completion_default, evaluate

# Offered statuses, in dropdown order. The value is inserted as an enum literal only after this whitelist check.
ADD_STATUSES = ("CURRENT", "PLANNING", "PAUSED", "COMPLETED", "DROPPED")


class AddEntryError(Exception):
    """Shown to the user as-is."""


@dataclass(frozen=True)
class NotListedRow:
    md_id: str
    md_title: str
    md_url: str
    md_cover_file: str | None
    media_id: int
    al_title: str
    al_url: str
    cover: str | None
    chapters: int | None
    proposed: int | None          # floor(max read chapter), None if nothing read
    progress: int                 # pre-filled value (capped at the AniList total)
    over_total: bool
    default_status: str
    unresolved: int


def not_listed_rows(repo: Repo) -> list[NotListedRow]:
    """Matched series whose media is not on the list, with a proposed progress from the latest read data."""
    rows: list[NotListedRow] = []
    mapped = repo.not_on_list()
    media = repo.media([m["al_media_id"] for m in mapped])
    md_rows = {r["md_id"]: r for r in repo.md_manga()}
    for m in mapped:
        md, al = md_rows[m["md_id"]], media.get(m["al_media_id"])
        reads = repo.read_chapters(m["md_id"])
        chapters = [r["chapter"] for r in reads if r["missing"] in (0, None) and r["chapter"] is not None]
        al_info = AlMediaInfo(al["chapters"] if al else None, al["status"] if al else None)
        md_info = MdInfo(md["pub_status"], md["last_chapter"], bool(md["chapter_numbers_reset"]))
        d = evaluate(chapters, len(reads) - len(chapters), None, al_info, md_info, repo.get_setting("jump_limit"))
        proposed = d.md_progress
        total = al_info.chapters
        over = proposed is not None and total is not None and proposed > total
        progress = (total if over else proposed) or 0
        title = titles.pick(al["romaji"], al["english"], al["native"]) if al else None
        rows.append(NotListedRow(
            md_id=m["md_id"],
            md_title=md["title"],
            md_url=f"https://mangadex.org/title/{m['md_id']}",
            md_cover_file=md["cover_file"],
            media_id=m["al_media_id"],
            al_title=title or f"AniList {m['al_media_id']}",
            al_url=(al["site_url"] if al else None) or f"https://anilist.co/manga/{m['al_media_id']}",
            cover=al["cover_url"] if al else None,
            chapters=total,
            proposed=proposed,
            progress=progress,
            over_total=over,
            default_status="COMPLETED" if completion_default(al_info, md_info, progress) else "CURRENT",
            unresolved=d.unresolved,
        ))
    return rows


def add_document(status: str) -> str:
    if status not in ADD_STATUSES:
        raise AddEntryError(f"Unsupported status {status!r}.")
    return (
        f"mutation ($m: Int, $p: Int) {{ SaveMediaListEntry(mediaId: $m, status: {status}, progress: $p) "
        "{ id mediaId progress status } }"
    )


async def add(
    repo: Repo, client: AniListClient, md_id: str, status: str, progress: int, user_id: Callable[[], Awaitable[int]]
) -> dict[str, Any]:
    """Create the entry. Raises AddEntryError for refusals.

    The list is re-read first (1 request): saving by mediaId when the series is already on the list would
    *update* that entry, and could turn Completed into Reading or lower its progress.
    """
    mapping = repo.get_mapping(md_id)
    if mapping is None or mapping["state"] not in ("auto", "confirmed") or mapping["al_media_id"] is None:
        raise AddEntryError("This series has no confirmed AniList match.")
    media_id = mapping["al_media_id"]
    if media_id in repo.al_entries():
        raise AddEntryError("Already on your list.")
    if progress < 0:
        raise AddEntryError("Progress cannot be negative.")
    media = repo.media([media_id]).get(media_id)
    if media is not None and media["chapters"] is not None and progress > media["chapters"]:
        raise AddEntryError(f"Progress {progress} exceeds AniList's total of {media['chapters']} chapters.")
    query = add_document(status)

    run_id = repo.create_run("writing")
    repo.update_run(run_id, approved_at=now_iso(), phase_detail="adding to AniList")
    previous = client.request_counter
    client.request_counter = lambda: repo.add_request(run_id, "anilist")
    try:
        await fetch_list(client, repo, await user_id())
        existing = repo.al_entries().get(media_id)
        if existing is not None:
            message = f"Already on your list now ({existing['status']}, progress {existing['progress']}); nothing was changed."
            repo.update_run(run_id, state="cancelled", error=message, finished_at=now_iso())
            raise AddEntryError(message)
        data = await client.graphql(query, {"m": media_id, "p": progress})
    except AniListGraphQLError as exc:
        repo.update_run(run_id, state="failed", error=f"AniList refused the add: {exc}", finished_at=now_iso())
        raise AddEntryError(f"AniList refused the add: {exc}") from exc
    except AddEntryError:
        raise
    except Exception as exc:
        repo.update_run(run_id, state="failed", error=f"{exc.__class__.__name__}: {exc}", finished_at=now_iso())
        raise
    finally:
        client.request_counter = previous

    entry = data["SaveMediaListEntry"]
    written_at = now_iso()
    repo.add_al_entry({"entry_id": entry["id"], "media_id": entry["mediaId"], "status": entry["status"],
                       "progress": entry["progress"] or 0, "fetched_at": written_at})
    repo.replace_items(run_id, [{
        "run_id": run_id, "md_id": md_id, "al_media_id": media_id, "al_entry_id": entry["id"],
        "al_progress": None, "md_progress": progress, "action": "write",
        "set_status": "COMPLETED" if status == "COMPLETED" else None, "status_approved": int(status == "COMPLETED"),
        "reason": f"added to AniList as {STATUS_LABELS[status]}", "approved": 1,
        "write_state": "done", "written_at": written_at,
        "verify_note": None if entry["status"] == status and entry["progress"] == progress
        else f"AniList stored {entry['status']} / {entry['progress']}",
    }])
    repo.update_run(run_id, state="done", finished_at=written_at,
                    phase_detail=f"added 1 entry: {STATUS_LABELS[status]}, progress {progress}")
    if not repo.get_setting("first_write_done"):
        repo.set_setting("first_write_done", True)
    return entry
