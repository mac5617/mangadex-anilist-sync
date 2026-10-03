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


MUTATION_MODULES = {SRC / "sync" / "writer.py", SRC / "sync" / "add_entry.py"}


def test_mutations_only_in_writer_and_add_entry():
    offenders = [
        str(p.relative_to(ROOT))
        for p in python_files()
        if p not in MUTATION_MODULES and re.search(r"SaveMediaListEntry|mutation", p.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_writer_never_sends_other_statuses():
    text = (SRC / "sync" / "writer.py").read_text(encoding="utf-8")
    assert set(re.findall(r"status: ([A-Z_]+)", text)) == {"COMPLETED"}
    assert "mediaId:" not in text  # never create entries by media id from a sync


PURE_MODULES = ["matching/normalize.py", "matching/score.py", "sync/rules.py", "sync/estimate.py"]
IMPURE_IMPORT = re.compile(
    r"^\s*(import|from)\s+(httpx|sqlite3|asyncio|socket|urllib|pathlib|os|mdal\.(db|clients|fetch|web|services|config))\b",
    re.MULTILINE,
)


def test_pure_modules_do_no_io():
    present = [SRC / m for m in PURE_MODULES if (SRC / m).exists()]
    assert present, "at least matching/normalize.py and matching/score.py exist from story 10"
    offenders = [str(p.relative_to(ROOT)) for p in present if IMPURE_IMPORT.search(p.read_text(encoding="utf-8"))]
    assert offenders == []
