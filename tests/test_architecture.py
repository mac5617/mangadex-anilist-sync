"""Structural rules that keep every API call behind the rate-limited clients."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "mdal"
SCRIPTS = ROOT / "scripts"
HTTPX_IMPORT = re.compile(r"^\s*(import httpx|from httpx\b)", re.MULTILINE)


def python_files():
    files = list(SRC.rglob("*.py"))
    if SCRIPTS.exists():
        files += list(SCRIPTS.rglob("*.py"))
    return files


def test_only_clients_import_httpx():
    clients_dir = SRC / "clients"
    offenders = [
        str(p.relative_to(ROOT))
        for p in python_files()
        if clients_dir not in p.parents and HTTPX_IMPORT.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == []


MUTATION_MODULES = {SRC / "sync" / "writer.py", SRC / "sync" / "add_entry.py", SRC / "sync" / "list_edit.py"}


def test_mutations_only_in_writer_and_add_entry():
    offenders = [
        str(p.relative_to(ROOT))
        for p in python_files()
        if p not in MUTATION_MODULES and re.search(r"SaveMediaListEntry|bmutationb", p.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_writer_status_literals():
    text = (SRC / "sync" / "writer.py").read_text(encoding="utf-8")
    # COMPLETED for completions; CURRENT only for brand-new entries (see test_writer.py for the documents).
    assert set(re.findall(r"\b(COMPLETED|CURRENT|PLANNING|PAUSED|DROPPED|REPEATING)\b", text)) == {"COMPLETED", "CURRENT"}


PURE_MODULES = ["matching/normalize.py", "matching/score.py", "sync/rules.py", "sync/estimate.py",
                "recommend/profile.py", "recommend/score.py", "recommend/llm.py", "recommend/graph.py",
                "recommend/fresh.py", "recommend/chat.py", "recommend/feedback.py", "recommend/ranking.py",
                "listtools.py", "search.py", "year.py", "compare.py"]
IMPURE_IMPORT = re.compile(
    r"^\s*(import|from)\s+(httpx|sqlite3|asyncio|socket|urllib|pathlib|os|mdal\.(db|clients|fetch|web|services|config))\b",
    re.MULTILINE,
)


def test_pure_modules_do_no_io():
    present = [SRC / m for m in PURE_MODULES if (SRC / m).exists()]
    assert present, "at least matching/normalize.py and matching/score.py exist from story 10"
    offenders = [str(p.relative_to(ROOT)) for p in present if IMPURE_IMPORT.search(p.read_text(encoding="utf-8"))]
    assert offenders == []


def test_mal_writes_only_from_mal_writer():
    """`update_list_status` is the client's one write; only sync/mal_writer.py may call it."""
    allowed = {SRC / "sync" / "mal_writer.py", SRC / "clients" / "myanimelist.py"}
    offenders = [
        str(p.relative_to(ROOT))
        for p in python_files()
        if p not in allowed and re.search(r"update_list_status|my_list_status|\"PATCH\"", p.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_mal_writer_status_literals():
    text = (SRC / "sync" / "mal_writer.py").read_text(encoding="utf-8")
    # "completed" for approved completions; "reading" only for brand-new entries.
    sent = set(re.findall(r'status\s*=\s*"(\w+)"', text))
    assert sent == {"reading", "completed"}


def test_list_edit_status_literals():
    text = (SRC / "sync" / "list_edit.py").read_text(encoding="utf-8")
    # Outside syncs, the only statuses Shiori sets are Paused and Dropped (List → Stalled).
    assert set(re.findall(r"\b(COMPLETED|CURRENT|PLANNING|PAUSED|DROPPED|REPEATING)\b", text)) == {"PAUSED", "DROPPED"}
    assert "mediaId:" not in text   # entries are addressed by id only, so nothing can be created


def test_mal_comments_only_from_mal_writer():
    """MyAnimeList's save creates an entry that doesn't exist, so only mal_writer (which checks first) may call it."""
    allowed = {SRC / "sync" / "mal_writer.py", SRC / "clients" / "myanimelist.py"}
    offenders = [str(p.relative_to(ROOT)) for p in python_files()
                 if p not in allowed and "update_comments" in p.read_text(encoding="utf-8")]
    assert offenders == []
