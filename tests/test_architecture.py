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


def test_no_mutation_before_story_16():
    # Removed by story 16, which replaces it with "only writer.py and add_entry.py".
    offenders = [str(p.relative_to(ROOT)) for p in python_files() if "SaveMediaListEntry" in p.read_text(encoding="utf-8")]
    assert offenders == []
