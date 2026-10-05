"""The prompt for the local model, and checking its answer. Pure: no I/O.

The model only chooses among real AniList candidates (by id) and explains its choice; any id it
invents is dropped, so a made-up title can never reach the page.
"""

from __future__ import annotations

from typing import Any

from mdal.recommend.profile import Profile
from mdal.recommend.score import Rec

PICKS = 12
NOTE_CHARS = 160      # of your own notes on a favourite, quoted to the model
CANDIDATES = 45
SYSTEM = (
    "You are a well-read manga recommender. You only recommend series from the numbered candidate list you are "
    "given, by id. Reasons are specific: name series from the reader's list that the pick resembles, and say "
    "what it shares with them. No spoilers. Plain English, no hype."
)
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "two or three sentences describing the reader's taste"},
        "picks": {"type": "array", "items": {
            "type": "object",
            "properties": {"id": {"type": "integer"}, "reason": {"type": "string"}},
            "required": ["id", "reason"]}},
    },
    "required": ["summary", "picks"],
}
STATUS_WORDS = {"CURRENT": "reading", "COMPLETED": "completed", "DROPPED": "dropped", "PAUSED": "paused",
                "REPEATING": "rereading"}


def _feature_line(features: list[Any]) -> str:
    return "; ".join(f"{f.label} ({f.count}" + (f", avg {f.mean_score:.0f}" if f.mean_score else "") + ")"
                     for f in features)


VERDICT_WORDS = {"loved": "they said they loved it", "liked": "they said they liked it",
                 "disliked": "they said they didn't like it", "not_interested": "they marked it not interested"}


def _entry(e: dict[str, Any]) -> str:
    if e.get("verdict"):
        return f"{e['title']} ({VERDICT_WORDS.get(e['verdict'], e['verdict'])})"
    bits = [STATUS_WORDS.get(e.get("status") or "", "")]
    if e.get("score"):
        bits.append(f"scored {e['score']:.0f}/100")
    elif e.get("progress"):
        bits.append(f"{e['progress']} ch read")
    line = f"{e['title']} ({', '.join(b for b in bits if b)})"
    if e.get("notes"):
        note = " ".join(e["notes"].split())
        line += f' - their note: "{note[:NOTE_CHARS]}{"…" if len(note) > NOTE_CHARS else ""}"'
    return line


def _candidate(r: Rec) -> str:
    tags = ", ".join(t["name"] for t in sorted(r.tags, key=lambda t: -(t.get("rank") or 0))[:6])
    staff = ", ".join(p["name"] for p in r.staff[:2])
    fans = ", ".join(s.get("label") for s in r.sources if s.get("kind") == "community")[:120]
    parts = [f"{r.media_id}: {r.title}", f"{r.year or '?'}, {r.country or '?'}", "/".join(r.genres) or "-"]
    if tags:
        parts.append(f"tags: {tags}")
    if staff:
        parts.append(f"by {staff}")
    if r.mean_score:
        parts.append(f"AniList avg {r.mean_score}")
    if fans:
        parts.append(f"recommended by readers of {fans}")
    return " | ".join(parts)


def build_prompt(profile: Profile, candidates: list[Rec]) -> str:
    lines = [
        f"The reader has {profile.entries} manga on their list.",
        "Genres they read most and like (count, average score where scored): " + _feature_line(profile.top("genres", 10)),
        "Tags: " + _feature_line(profile.top("tags", 18)),
        "Favourite creators: " + _feature_line(profile.top("staff", 8)),
        "",
        "Series they liked most:",
        *[f"- {_entry(e)}" for e in profile.favourites[:25]],
    ]
    if profile.disliked:
        lines += ["", "Series they dropped or rated low:", *[f"- {_entry(e)}" for e in profile.disliked[:10]]]
    lines += [
        "",
        f"Candidates (none are on their list yet). Format: id: title | year, country | genres | tags | creators | ...",
        *[_candidate(r) for r in candidates[:CANDIDATES]],
        "",
        f"Choose the {PICKS} candidates this reader is most likely to love, best first, avoiding near-duplicates "
        "(e.g. several entries of one franchise). For each, one sentence of at most 30 words on why. Also write a "
        "two- or three-sentence summary of their taste.",
    ]
    return "\n".join(lines)


def is_reason(text: str, title: str | None = None) -> bool:
    """A reason is a sentence about the series; models sometimes return just its (romaji) title instead, which is
    short and has no closing punctuation ("Aku no Hana")."""
    text = text.strip()
    if title and text.rstrip(".").casefold() == title.casefold():
        return False
    return bool(text) and (len(text.split()) >= 4 or text[-1] in ".!?…")


def clean_answer(answer: dict[str, Any], candidate_ids: set[int], titles: dict[int, str] | None = None) -> tuple[str, list[dict[str, Any]]]:
    """(summary, picks) with only valid, unique candidate ids and non-empty reasons."""
    picks: list[dict[str, Any]] = []
    seen: set[int] = set()
    for p in answer.get("picks") or []:
        try:
            media_id = int(p.get("id"))
        except (TypeError, ValueError):
            continue
        reason = str(p.get("reason") or "").strip()
        if media_id in candidate_ids and media_id not in seen and is_reason(reason, (titles or {}).get(media_id)):
            seen.add(media_id)
            picks.append({"id": media_id, "reason": reason[:400]})
    return str(answer.get("summary") or "").strip()[:1200], picks[:PICKS]
