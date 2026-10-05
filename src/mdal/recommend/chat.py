"""Ask: a conversation with the local model about series you haven't read. Pure: no I/O.

Each question first narrows the pool (new MangaDex releases plus AniList candidates) to the series
that best fit both the question and your taste; the model answers from those only, by number, so it
can't suggest a title that isn't real or that you already have.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from mdal.matching.normalize import normalize
from mdal.recommend.fresh import plain, taste_lines
from mdal.recommend.llm import is_reason

SHORTLIST = 30
ANILIST_RESERVE = 8           # AniList candidates always on the shortlist
MIN_NAMED, MAX_NAMED = 5, 2   # a named title is at least this long (normalised); at most this many count
HISTORY_TURNS = 6           # earlier messages sent with each question
RELEVANCE, TASTE = 0.65, 0.35
FOCUSED_TASTE = 0.15          # taste's share when the question names a series or a tag: the request comes first
MIN_TAG_MATCHES = 6           # with fewer series having every tag asked for, any of them will do
# MangaDex format tags say how a series is published, not what it is about.
FORMAT_TAGS = {"adaptation", "long strip", "web comic", "full color", "oneshot", "official colored", "4 koma",
               "award winning", "anthology", "doujinshi", "user created", "fan colored", "self published"}
NEGATION = re.compile(r"\b(no|not|without|except|never|zero|less|minus|avoid|skip)\s+(?:\w+\s+){0,2}$")
DESCRIPTION_IN_PROMPT = 300
STOPWORDS = set("a an and any are as at be but by can do does for from give have i if in into is it its like "
                "looking me more my of on one or please read reading recommend series show similar so some something "
                "that the them there this to want what which who with would you manga manhwa manhua".split())


@dataclass
class Item:
    key: str                       # "md:<uuid>" or "al:<id>"
    title: str
    url: str
    cover: str | None
    meta: list[str]
    tags: list[str]
    description: str | None
    taste: float                   # 0..1, how well it fits your list
    adult: bool
    source: str                    # "New on MangaDex" | "AniList"
    similar_to: list[str] = field(default_factory=list)

    def search_text(self) -> str:
        return " ".join([self.title, " ".join(self.tags), self.description or ""]).casefold()


def words(text: str) -> list[str]:
    return [w for w in re.findall(r"[\w'-]+", text.casefold()) if w not in STOPWORDS and len(w) > 2]


def keyword_relevance(question: str, items: list[Item]) -> dict[str, float]:
    """Share of the question's words found in each series' title, tags or description (no embedding model)."""
    wanted = set(words(question))
    if not wanted:
        return {i.key: 0.0 for i in items}
    out = {}
    for item in items:
        text = item.search_text()
        tags = {t.casefold() for t in item.tags}
        hits = sum(2.0 if w in tags or any(w in t for t in tags) else 1.0 for w in wanted if w in text)
        out[item.key] = hits / (2 * len(wanted))
    return out


def shortlist(items: list[Item], relevance: dict[str, float], n: int = SHORTLIST,
              reserve: int = ANILIST_RESERVE, taste: float = TASTE) -> list[Item]:
    """Best blend of relevance to the question (scaled 0..1 across the pool) and taste.

    AniList candidates have no description to match, so the best few always get a place
    (otherwise a pool of mostly new MangaDex releases crowds them out)."""
    values = [relevance.get(i.key, 0.0) for i in items]
    low, high = (min(values), max(values)) if values else (0.0, 0.0)

    def blend(item: Item) -> float:
        rel = (relevance.get(item.key, 0.0) - low) / (high - low) if high > low else 0.0
        return (1 - taste) * rel + taste * item.taste

    ordered = sorted(items, key=lambda i: -blend(i))
    chosen = ordered[:n]
    missing = reserve - sum(1 for i in chosen if i.key.startswith("al:"))
    if missing > 0:
        extra = [i for i in ordered[n:] if i.key.startswith("al:")][:missing]
        keep = [i for i in chosen if i.key.startswith("al:")]
        others = [i for i in chosen if not i.key.startswith("al:")][: n - len(keep) - len(extra)]
        chosen = sorted(keep + others + extra, key=lambda i: -blend(i))
    return chosen


def tag_key(name: str) -> str:
    key = normalize(name)
    return key[:-1] if key.endswith("s") and len(key) > 4 else key   # "vampires" and "vampire" are one tag


def asked_tags(question: str, items: list[Item]) -> tuple[set[str], set[str]]:
    """(tags asked for, tags asked to avoid), as tag keys: any tag in the pool that the question names,
    avoided when "no", "not", "without"... comes just before it."""
    vocabulary = {tag_key(t) for i in items for t in i.tags} - {tag_key(t) for t in FORMAT_TAGS}
    text = f" {normalize(question)} "
    want: set[str] = set()
    avoid: set[str] = set()
    for tag in sorted(vocabulary, key=len, reverse=True):
        for form in {tag, tag + "s"}:
            at = text.find(f" {form} ")
            if at < 0 or len(tag) < 3:
                continue
            (avoid if NEGATION.search(text[:at + 1]) else want).add(tag)
            text = text.replace(f" {form} ", " ")
            break
    return want, avoid


def filter_by_tags(items: list[Item], want: set[str], avoid: set[str]) -> list[Item]:
    """Series with every tag asked for (or, when fewer than a handful have them all, any of them), none avoided."""
    keyed = [(i, {tag_key(t) for t in i.tags}) for i in items]
    keyed = [(i, tags) for i, tags in keyed if not tags & avoid]
    if not want:
        return [i for i, _ in keyed]
    every = [i for i, tags in keyed if want <= tags]
    return every if len(every) >= MIN_TAG_MATCHES else [i for i, tags in keyed if tags & want]


def tag_overlap(reference: list[str], items: list[Item]) -> dict[str, float]:
    """Share of a named series' tags (format tags aside) that each series has too."""
    ref = {tag_key(t) for t in reference} - {tag_key(t) for t in FORMAT_TAGS}
    if not ref:
        return {}
    return {i.key: len(ref & {tag_key(t) for t in i.tags}) / len(ref) for i in items}


def find_named(question: str, index: dict[str, tuple[str, str]]) -> list[tuple[str, str]]:
    """Series the question names: [(key, title)], longest match first. `index` maps normalised titles to them."""
    text = f" {normalize(question)} "
    found: list[tuple[str, str]] = []
    for name in sorted(index, key=len, reverse=True):
        if len(name) >= MIN_NAMED and f" {name} " in text and index[name] not in found:
            found.append(index[name])
            text = text.replace(f" {name} ", " ")
    return found[:MAX_NAMED]


SYSTEM = (
    "You help one reader find manga they haven't read yet. You may only suggest series from the numbered list in "
    "the latest message, by number; never name other series as suggestions (you may mention series from their own "
    "list to compare). What they ask for in their latest message decides: mood, genre, length, status, or 'like X "
    "but Y'. Every pick must match it; their usual taste only breaks ties between series that do, and earlier "
    "questions only matter when the latest one refers back to them. If too few in the list fit, pick only those and "
    "say so plainly. Be concise and specific, no hype, no spoilers. Pick 4 to 6 series unless they ask for a "
    "different number. Each series you suggest is shown to the reader as a "
    "card with its title, details and your reason: each reason says in one or two sentences why that series fits "
    "them, and the reply is one to three sentences that answer them overall without repeating the reasons (plain "
    "text, no lists, no Markdown). Always call a series by its title; the list numbers mean nothing to the reader."
)
SCHEMA: dict[str, Any] = {   # picks first, so the reply is written after them as a short wrap-up
    "type": "object",
    "properties": {
        "picks": {"type": "array", "items": {
            "type": "object", "properties": {"n": {"type": "integer"}, "reason": {"type": "string"}},
            "required": ["n", "reason"]}},
        "reply": {"type": "string", "description": "one to three sentences that answer the reader without repeating "
                                                   "the reasons; plain text"},
    },
    "required": ["picks", "reply"],
}


def item_line(n: int, item: Item) -> str:
    parts = [f"{n}. {item.title}", ", ".join(item.meta[:4]) or "-", "tags: " + (", ".join(item.tags[:10]) or "-")]
    if item.similar_to:
        parts.append("close to their " + ", ".join(item.similar_to[:2]))
    line = " | ".join(parts)   # no taste score: shown one, the model let it outweigh what was asked for
    if item.description:
        text = " ".join(item.description.split())
        if len(text) > DESCRIPTION_IN_PROMPT:
            text = text[:DESCRIPTION_IN_PROMPT].rsplit(" ", 1)[0] + "…"
        line += f"\n   {text}"
    return line


def history_text(message: dict[str, Any], titles: dict[str, str]) -> str:
    """A stored message as the model sees it later. An earlier answer is only its picks, by title: shown its own
    earlier wording, the model tends to copy it, titles and all, into the next answer."""
    if message["role"] == "user":
        return message["content"]
    picks = [t for p in message.get("picks") or [] if (t := titles.get(p["key"]))]
    return "I suggested: " + "; ".join(picks) + "." if picks else "I found nothing that fit."


@dataclass
class Named:
    """A series the reader named: the reference for "like this", never a suggestion."""
    title: str
    description: str | None
    tags: list[str]
    on_list: bool


def build_messages(profile: Any, history: list[dict[str, Any]], titles: dict[str, str],
                   question: str, candidates: list[Item], named: list[Named] = ()) -> list[dict[str, str]]:
    context = "\n".join(taste_lines(profile, favourites=20))
    messages = [{"role": "system", "content": SYSTEM + "\n\nAbout the reader:\n" + context}]
    for m in history[-HISTORY_TURNS:]:
        messages.append({"role": m["role"], "content": history_text(m, titles)})
    listing = "\n".join(item_line(i, it) for i, it in enumerate(candidates, 1))
    notes = []
    for n in named:
        state = "on my list" if n.on_list else "I've read it"
        line = f"- {n.title} ({state}; don't suggest it). Tags: {', '.join(n.tags[:10]) or '-'}."
        if n.description:
            line += " " + " ".join(n.description.split())[:DESCRIPTION_IN_PROMPT]
        notes.append(line)
    reference = ("\n\nThe series I mentioned, as the reference for what I want:\n" + "\n".join(notes)) if notes else ""
    messages.append({"role": "user", "content": (
        f"{question}{reference}\n\n---\nSeries you may suggest (none are on my list). Format: number. title | "
        f"details | tags | ...\n{listing}")})
    return messages


SENTENCE = re.compile(r"(?<=[.!?])\s+")
BARE_NUMBERS = re.compile(r"(?<![\w.,])\d{1,2}\s*(,|and|&|or)\s*\d{1,2}(?![\w%])")   # "15 and 16", "3, 7"
LISTING_LINE = re.compile(r"^\s*(\d+[.)]|[-*•])?\s*[^|\n]+\|[^|\n]+\|")


def clean_reply(answer: dict[str, Any], candidates: list[Item],
                others: list[str] = ()) -> tuple[str, list[dict[str, Any]]]:
    """(reply, [{key, reason}]) keeping only real, unique numbers. A reply sentence naming a series from the list
    or from `others` (earlier picks) that it didn't pick this time is dropped: it was copied, not chosen."""
    picks: list[dict[str, Any]] = []
    seen: set[int] = set()
    for p in answer.get("picks") or []:
        try:
            n = int(p.get("n"))
        except (TypeError, ValueError, AttributeError):
            continue
        if 1 <= n <= len(candidates) and n not in seen:
            seen.add(n)
            reason = plain(str(p.get("reason") or ""))
            picks.append({"key": candidates[n - 1].key, "reason": reason[:400] if is_reason(reason) else ""})
    # The cards show each pick, so lines copying the listing format ("3. Title | 2012, ...") are dropped.
    lines = [line for line in str(answer.get("reply") or "").splitlines() if not LISTING_LINE.match(line)]
    # A sentence that only repeats one of the reasons is already on its card.
    reasons = " ".join(p["reason"] for p in picks).casefold()
    kept = [" ".join(s for s in SENTENCE.split(line) if s.strip().casefold() not in reasons)
            for line in plain("\n".join(lines)).splitlines()]
    picked = [c.title.casefold() for c in candidates if c.key in {p["key"] for p in picks}]
    unpicked = [t.casefold() for t in [*others, *(c.title for c in candidates)]
                if len(t) >= MIN_NAMED and not any(t.casefold() in pt for pt in picked)]
    kept = [" ".join(s for s in SENTENCE.split(line) if not any(t in s.casefold() for t in unpicked)) for line in kept]
    reply = "\n".join(line for line in kept if line.strip())[:4000]
    # Numbers mean nothing to the reader: "#3", "(3)", "number 3" or "option 3" become the title.
    for n, item in enumerate(candidates, 1):
        reply = re.sub(rf"(?<![\w.])(#{n}|\({n}\)|(?:numbers?|no\.|options?|picks?|entry)\s+{n})(?![\w])", item.title,
                       reply, flags=re.IGNORECASE)
    # A sentence still citing list numbers ("while 15 and 16 are lighter") can't be read, so it goes.
    reply = "\n".join(" ".join(s for s in SENTENCE.split(line) if not BARE_NUMBERS.search(s))
                      for line in reply.splitlines()).strip()
    return reply, picks[:12]
