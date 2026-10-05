"""New releases on MangaDex, ranked against your list. Pure: no I/O.

Three signals, each scaled to 0..1 across the scan:
- tag: MangaDex genres and themes matched by name to the genres and tags of your AniList list;
- staff: an author or artist whose other series you liked;
- description: how closely the series' description (as an embedding) matches those of series you
  liked, minus how closely it matches ones you dropped. Only when an embedding model is installed.
The local model then reads the descriptions of the best of these and picks.
"""

from __future__ import annotations

import json
import math
import re
from array import array
from dataclasses import dataclass, field
from typing import Any

from mdal.matching.normalize import normalize
from mdal.recommend.llm import _entry, _feature_line
from mdal.recommend.profile import Feature, Profile

TOP_TAGS = 5
WEIGHTS = {"description": 0.5, "tag": 0.35, "staff": 0.15}
WEIGHTS_NO_DESCRIPTION = {"tag": 0.8, "staff": 0.2}
NO_DESCRIPTION_SCORE = 0.25   # a series without a description can't be compared; it ranks below average
CLOSEST = 3                   # liked series averaged for the description score
PICKS = 12
CANDIDATES = 40
DESCRIPTION_IN_PROMPT = 450

# MangaDex tag name -> AniList genre or tag name, where they differ.
ALIASES = {
    "girls' love": "yuri", "school life": "school", "magical girls": "mahou shoujo", "genderswap": "gender bending",
    "harem": "female harem", "reverse harem": "male harem", "virtual reality": "virtual world",
    "medical": "medicine", "philosophical": "philosophy", "office workers": "workplace",
    "traditional games": "board game", "sexual violence": "rape",
}
LANGUAGES = {"ja": "Japan", "ko": "Korea", "zh": "China", "zh-hk": "China", "en": "English", "id": "Indonesia",
             "vi": "Vietnam", "th": "Thailand", "es": "Spain", "fr": "France"}
STATUS = {"ongoing": "ongoing", "completed": "completed", "hiatus": "on hiatus", "cancelled": "cancelled"}
ADULT_RATINGS = ("erotica", "pornographic")
# MangaDex's smallest thumbnail. Loaded by the browser, lazily, with no referrer (api-notes risk 6).
MD_COVER = "https://uploads.mangadex.org/covers/{md_id}/{file}.256.jpg"


EMPHASIS = re.compile(r"(\*\*|__|\*|`)(?=\S)(.+?)(?<=\S)\1")


def plain(text: str) -> str:
    """Model text without Markdown emphasis or blank lines (pages show plain text)."""
    text = EMPHASIS.sub(r"\2", text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def term_key(name: str) -> str:
    return name.strip().casefold()


def taste_terms(profile: Profile) -> dict[str, Feature]:
    """Your genres and tags by lower-case name (a genre wins over a tag of the same name)."""
    terms = {term_key(f.label): f for f in profile.tags.values()}
    terms.update({term_key(f.label): f for f in profile.genres.values()})
    return terms


def match_term(name: str, terms: dict[str, Feature]) -> Feature | None:
    key = term_key(name)
    key = ALIASES.get(key, key)
    if key in terms:
        return terms[key]
    if key.endswith("s") and key[:-1] in terms:      # "Vampires" -> "Vampire"
        return terms[key[:-1]]
    return None


def person_key(name: str) -> str:
    """Order- and romanisation-tolerant: "Murata Yuusuke" and "Yusuke Murata" match."""
    words = normalize(name).replace("ou", "o").replace("uu", "u").split()
    return " ".join(sorted(words))


# ---- vectors ----------------------------------------------------------------------------


def unit(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]


def pack(vector: list[float]) -> bytes:
    return array("f", unit(vector)).tobytes()


def unpack(blob: bytes) -> list[float]:
    values = array("f")
    values.frombytes(blob)
    return values.tolist()


def dot(a: list[float], b: list[float]) -> float:
    return math.fsum(x * y for x, y in zip(a, b))


def doc_text(title: str, terms: list[str], description: str | None) -> str:
    """The same shape for your series and new ones, so their embeddings compare like with like."""
    parts = [title]
    if terms:
        parts.append("Tags: " + ", ".join(terms))
    if description:
        parts.append(description[:1500])
    return "\n".join(parts)


def query_text(question: str) -> str:
    """Instruction-style query (Qwen3-Embedding and similar models retrieve better with one)."""
    return f"Instruct: Given a reader's request, find manga whose description matches it\nQuery: {question}"


@dataclass
class Shelf:
    """Embedded series from your list: (weight, title, unit vector)."""
    liked: list[tuple[float, str, list[float]]]
    disliked: list[tuple[float, str, list[float]]]


def similarity(vector: list[float], shelf: Shelf) -> tuple[float, list[str]]:
    """(raw score, titles of the closest liked series). Higher-weight favourites count a little more."""
    sims = sorted(((dot(vector, v), w, t) for w, t, v in shelf.liked), reverse=True)[:CLOSEST]
    if not sims:
        return 0.0, []
    liked = sum(s * (0.7 + 0.3 * min(w, 1.0)) for s, w, _ in sims) / len(sims)
    worst = max((dot(vector, v) for _, _, v in shelf.disliked), default=0.0)
    penalty = 0.5 * max(0.0, worst - sims[0][0])     # closer to something you dropped than to anything you liked
    return liked - penalty, [t for _, _, t in sims]


# ---- ranking -------------------------------------------------------------------------------


@dataclass
class NewRec:
    md_id: str
    title: str
    description: str | None
    tags: list[str]
    authors: list[str]
    year: int | None
    language: str | None
    pub_status: str | None
    demographic: str | None
    adult: bool
    al_id: int | None
    cover_file: str | None
    last_chapter: str | None
    source: str
    similarity: float | None
    similar_to: list[str]
    raw: dict[str, float] = field(default_factory=dict)
    scores: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    @property
    def url(self) -> str:
        return f"https://mangadex.org/title/{self.md_id}"

    @property
    def cover(self) -> str | None:
        return MD_COVER.format(md_id=self.md_id, file=self.cover_file) if self.cover_file else None

    def meta(self) -> list[str]:
        return [m for m in (str(self.year) if self.year else None, LANGUAGES.get(self.language or "", self.language),
                            STATUS.get(self.pub_status or "", self.pub_status),
                            (self.demographic or "").capitalize() or None,
                            f"latest ch. {self.last_chapter}" if self.last_chapter else None,
                            "just added to MangaDex" if self.source == "added" else None) if m]


def english_meta(mu: dict[str, Any] | None) -> list[str]:
    """What MangaUpdates says about English releases, for the details line."""
    if not mu:
        return []
    bits = []
    if mu.get("latest_chapter"):
        bits.append(f"EN ch. {mu['latest_chapter']}" + (" (fully translated)" if mu.get("completed") else ""))
    elif mu.get("completed"):
        bits.append("fully translated")
    if mu.get("licensed"):
        bits.append("licensed in English")
    return bits


def make_new_rec(row: dict[str, Any]) -> NewRec:
    return NewRec(
        md_id=row["md_id"], title=row["title"], description=row.get("description"),
        tags=[t["name"] for t in json.loads(row.get("tags") or "[]")], authors=json.loads(row.get("authors") or "[]"),
        year=row.get("year"), language=row.get("original_language"), pub_status=row.get("pub_status"),
        demographic=row.get("demographic"), adult=(row.get("content_rating") or "") in ADULT_RATINGS,
        al_id=row.get("al_id"), cover_file=row.get("cover_file"), last_chapter=row.get("last_chapter"),
        source=row.get("source") or "updated", similarity=row.get("similarity"),
        similar_to=json.loads(row.get("similar_to") or "[]"),
    )


def score_new(profile: Profile, rows: list[dict[str, Any]]) -> list[NewRec]:
    recs = [make_new_rec(r) for r in rows]
    terms = taste_terms(profile)
    people = {person_key(f.label): f for f in profile.staff.values()}
    for r in recs:
        found = sorted(((f.affinity, f.label) for name in r.tags if (f := match_term(name, terms))), reverse=True)
        positive = [(a, label) for a, label in found if a > 0][:TOP_TAGS]
        r.raw["tag"] = sum(a for a, _ in positive) / TOP_TAGS + 0.5 * sum(a for a, _ in found if a < 0)
        liked = sorted(((f.affinity, name, f) for name in r.authors if (f := people.get(person_key(name))) and f.affinity > 0),
                       key=lambda x: -x[0])
        r.raw["staff"] = (liked[0][0] + 0.25 * sum(a for a, _, _ in liked[1:])) if liked else 0.0
        if r.similar_to:
            r.reasons.append("Description close to " + ", ".join(r.similar_to[:2]))
        if liked:
            r.reasons.append(f"By {liked[0][1]}, who made {', '.join(liked[0][2].top_titles(2))}")
        if positive:
            r.reasons.append("Tags you like: " + ", ".join(label for _, label in positive[:4]))

    for kind in ("tag", "staff"):
        top = max((r.raw[kind] for r in recs), default=0.0)
        for r in recs:
            r.scores[kind] = max(-1.0, r.raw[kind] / top) if top > 0 else 0.0
    sims = [r.similarity for r in recs if r.similarity is not None]
    low, high = (min(sims), max(sims)) if sims else (0.0, 0.0)
    for r in recs:
        if not sims:
            r.scores["overall"] = sum(w * r.scores[k] for k, w in WEIGHTS_NO_DESCRIPTION.items())
            continue
        if r.similarity is None:
            r.scores["description"] = NO_DESCRIPTION_SCORE
        else:
            r.scores["description"] = (r.similarity - low) / (high - low) if high > low else 1.0
        r.scores["overall"] = sum(w * r.scores[k] for k, w in WEIGHTS.items())
    return recs


def ranked_new(recs: list[NewRec], n: int | None = None) -> list[NewRec]:
    out = sorted(recs, key=lambda r: -r.scores["overall"])
    return out[:n] if n else out


# ---- the model's picks ------------------------------------------------------------------------

SYSTEM = (
    "You are a well-read manga recommender. You only recommend series from the numbered list of new releases "
    "you are given, by number. Judge each one mainly by its description. Reasons are specific: name series from "
    "the reader's list that the pick resembles and say what it shares with them. No spoilers. Plain English, no hype."
)
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"picks": {"type": "array", "items": {
        "type": "object", "properties": {"n": {"type": "integer"}, "reason": {"type": "string"}},
        "required": ["n", "reason"]}}},
    "required": ["picks"],
}


def candidate_line(n: int, r: NewRec) -> str:
    parts = [f"{n}. {r.title}", ", ".join(r.meta()[:3]) or "-", "tags: " + (", ".join(r.tags[:10]) or "-")]
    if r.authors:
        parts.append("by " + ", ".join(r.authors[:2]))
    if r.similar_to:
        parts.append("closest on their list: " + ", ".join(r.similar_to[:2]))
    description = " ".join((r.description or "no description").split())
    if len(description) > DESCRIPTION_IN_PROMPT:
        description = description[:DESCRIPTION_IN_PROMPT].rsplit(" ", 1)[0] + "…"
    return " | ".join(parts) + f"\n   {description}"


def taste_lines(profile: Profile, favourites: int = 25) -> list[str]:
    lines = [
        f"The reader has {profile.entries} manga on their list.",
        "Genres they read most and like (count, average score where scored): " + _feature_line(profile.top("genres", 10)),
        "Tags: " + _feature_line(profile.top("tags", 18)),
        "",
        "Series they liked most:",
        *[f"- {_entry(e)}" for e in profile.favourites[:favourites]],
    ]
    if profile.disliked:
        lines += ["", "Series they dropped or rated low:", *[f"- {_entry(e)}" for e in profile.disliked[:10]]]
    return lines


def build_prompt(profile: Profile, candidates: list[NewRec]) -> str:
    return "\n".join([
        *taste_lines(profile),
        "",
        "New releases on MangaDex (none are on their list). Format: number. title | year, origin, status | tags | "
        "creators | closest series on their list | description",
        *[candidate_line(i, r) for i, r in enumerate(candidates[:CANDIDATES], 1)],
        "",
        f"Choose the {PICKS} this reader is most likely to love, best first. Skip any whose description suggests "
        "something they dropped. For each, one sentence of at most 30 words on why.",
    ])


def clean_picks(answer: dict[str, Any], candidates: list[NewRec]) -> list[dict[str, Any]]:
    """[{md_id, reason}] for valid, unique numbers with a reason."""
    picks: list[dict[str, Any]] = []
    seen: set[int] = set()
    for p in answer.get("picks") or []:
        try:
            n = int(p.get("n"))
        except (TypeError, ValueError, AttributeError):
            continue
        reason = plain(str(p.get("reason") or ""))
        if 1 <= n <= min(len(candidates), CANDIDATES) and n not in seen and reason:
            seen.add(n)
            picks.append({"md_id": candidates[n - 1].md_id, "reason": reason[:400]})
    return picks[:PICKS]
