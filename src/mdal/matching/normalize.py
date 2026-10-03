"""Title/name normalisation (architecture §7). Pure: no I/O."""

from __future__ import annotations

import unicodedata


def _is_latin(c: str) -> bool:
    o = ord(c)
    return o < 0x250 or 0x1E00 <= o <= 0x1EFF  # Basic Latin … IPA, Latin Extended Additional


def _strip_diacritics(c: str) -> str:
    # Only on Latin letters: NFKD would also strip kana dakuten (ガ → カ) and merge distinct titles.
    d = unicodedata.normalize("NFKD", c)
    return "".join(x for x in d if not unicodedata.combining(x)) if _is_latin(d[0]) else c


def normalize(s: str | None) -> str:
    """NFKC → casefold → strip diacritics → punctuation/symbols to spaces → collapse whitespace."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s).casefold()
    s = "".join(_strip_diacritics(c) for c in s)
    # Letters (L*), digits/numbers (N*) and marks kept by scripts like Devanagari (M*) stay; everything else splits words.
    s = "".join(c if unicodedata.category(c)[0] in "LNM" else " " for c in s)
    return " ".join(s.split())
