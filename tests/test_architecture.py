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
MUTATION = re.compile(r"SaveMediaListEntry|\bmutation\b")


def test_mutation_pattern_finds_any_graphql_mutation():
    assert MUTATION.search('f"mutation ($e: Int) {{ DeleteMediaListEntry(id: $e) }}"')
    assert not MUTATION.search("mutation_document(ops)")


def test_mutations_only_in_writer_and_add_entry():
    offenders = [
        str(p.relative_to(ROOT))
        for p in python_files()
        if p not in MUTATION_MODULES and MUTATION.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_writer_status_literals():
    text = (SRC / "sync" / "writer.py").read_text(encoding="utf-8")
    # COMPLETED for completions; CURRENT only for brand-new entries (see test_writer.py for the documents).
    assert set(re.findall(r"\b(COMPLETED|CURRENT|PLANNING|PAUSED|DROPPED|REPEATING)\b", text)) == {"COMPLETED", "CURRENT"}


PURE_MODULES = ["matching/normalize.py", "matching/score.py", "sync/rules.py", "sync/estimate.py",
                "recommend/profile.py", "recommend/score.py", "recommend/llm.py", "recommend/graph.py",
                "recommend/fresh.py", "recommend/chat.py", "recommend/feedback.py", "recommend/ranking.py",
                "listtools.py", "search.py", "year.py", "compare.py", "titles.py"]
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
    """MyAnimeList writes: syncs from sync/mal_writer.py, list edits from sync/mal_list_edit.py, nowhere else."""
    allowed = {SRC / "sync" / "mal_writer.py", SRC / "sync" / "mal_list_edit.py", SRC / "clients" / "myanimelist.py"}
    offenders = [
        str(p.relative_to(ROOT))
        for p in python_files()
        if p not in allowed and re.search(r"update_list_status|add_to_list|my_list_status|\"PATCH\"",
                                          p.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_mal_writer_status_literals():
    text = (SRC / "sync" / "mal_writer.py").read_text(encoding="utf-8")
    # "completed" for approved completions; "reading" only for brand-new entries.
    sent = set(re.findall(r'status\s*=\s*"(\w+)"', text))
    assert sent == {"reading", "completed"}
    # A series-page add sends the status you picked (never rereading), and only after reading the entry.
    from mdal.sync.mal_writer import MAL_ADD
    assert set(MAL_ADD.values()) == {"reading", "completed", "on_hold", "dropped", "plan_to_read"}
    add = text[text.index("async def add_by_hand"):text.index("def is_mal_add")]
    assert add.index("my_list_status(") < add.index("add_to_list(") and "if entry is not None" in add


def test_list_edit_status_literals():
    text = (SRC / "sync" / "list_edit.py").read_text(encoding="utf-8")
    # Outside syncs, statuses are set by hand (Stalled, series pages): never Rereading.
    assert set(re.findall(r"\b(COMPLETED|CURRENT|PLANNING|PAUSED|DROPPED|REPEATING)\b", text)) == {
        "CURRENT", "COMPLETED", "PAUSED", "DROPPED", "PLANNING"}
    assert "mediaId:" not in text   # entries are addressed by id only, so nothing can be created


def test_mal_comments_only_from_mal_writer():
    """MyAnimeList's save creates an entry that doesn't exist, so only mal_writer (which checks first) may call it."""
    allowed = {SRC / "sync" / "mal_writer.py", SRC / "clients" / "myanimelist.py"}
    offenders = [str(p.relative_to(ROOT)) for p in python_files()
                 if p not in allowed and "update_comments" in p.read_text(encoding="utf-8")]
    assert offenders == []


def test_mal_list_edit_literals():
    text = (SRC / "sync" / "mal_list_edit.py").read_text(encoding="utf-8")
    # A list edit sets the status you picked, and only on entries it read first (never rereading).
    sent = set(re.findall(r'"(reading|completed|on_hold|dropped|plan_to_read)"', text))
    assert sent == {"reading", "completed", "on_hold", "dropped", "plan_to_read"} and "is_rereading" not in text
    assert "my_list_status(" in text and "update_list_status" not in text and "update_comments" not in text
