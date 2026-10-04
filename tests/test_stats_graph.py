"""Stats → Connections: which tags/genres go together."""

import json
import re

from fastapi.testclient import TestClient

from mdal.stats_graph import theme_graph
from mdal.web.app import create_app
from tests.factories import al_media_row


def row(i, tags, genres=("Action",), score=None, status="CURRENT"):
    return {"media_id": i, "title": f"S{i}", "status": status, "score": score, "progress": 20, "format": "MANGA",
            "tags": list(tags), "tag_ranks": [{"name": t, "rank": 80} for t in tags], "genres": list(genres), "staff": []}


def library():
    rows = [row(i, ["Isekai", "Magic", "Reincarnation"], score=60) for i in range(12)]   # one cluster
    rows += [row(100 + i, ["Gore", "Psychological", "Tragedy"], score=95) for i in range(10)]  # another
    rows += [row(200 + i, ["Isekai"]) for i in range(8)]
    rows += [row(300 + i, ["School"]) for i in range(6)]   # common, never with the others
    rows += [row(400, ["Isekai", "Gore"])]                    # a single crossover: below MIN_SHARED
    return rows


def test_clusters_are_linked_and_crossovers_are_not():
    g = theme_graph(library(), "tags")
    pairs = {frozenset((l["source"], l["target"])) for l in g["links"]}
    assert frozenset(("Isekai", "Magic")) in pairs and frozenset(("Gore", "Psychological")) in pairs
    assert frozenset(("Isekai", "Gore")) not in pairs
    nodes = {n["id"]: n for n in g["nodes"]}
    assert nodes["Isekai"]["size"] == 1.0 and nodes["Isekai"]["count"] == 21
    assert nodes["Gore"]["tone"] > nodes["Isekai"]["tone"]            # liked more: darker
    assert "mean score 95 (10 scored)" in nodes["Gore"]["tip"]
    assert "School" not in {x for p in pairs for x in p}               # no partner above chance
    assert all(len([l for l in g["links"] if n in (l["source"], l["target"])]) <= 4 + 2 for n in nodes)
    assert g["pairs"][0]["lift"] >= g["pairs"][-1]["lift"]


def test_too_little_data_gives_an_empty_graph():
    assert theme_graph([row(1, ["Isekai"])], "tags")["nodes"] == []
    assert theme_graph([], "genres")["nodes"] == []


def test_page(services):
    repo = services.repo
    repo.upsert_media([al_media_row(i, f"Series {i}", tags=json.dumps([{"name": t, "rank": 80} for t in tags]),
                                    genres='["Action"]')
                       for i, tags in [*[(i, ["Isekai", "Magic"]) for i in range(1, 9)],
                                       *[(i, ["Gore", "</script><b>x"]) for i in range(20, 28)]]])
    repo.replace_al_entries([{"entry_id": 1000 + i, "media_id": i, "status": "CURRENT", "progress": 5, "fetched_at": "x"}
                             for i in [*range(1, 9), *range(20, 28)]])
    with TestClient(create_app(services)) as c:
        html = c.get("/stats/connections").text
        assert 'aria-current="page">Connections' in html and "Strongest pairings" in html
        data = json.loads(re.search(r'<script id="theme-net" type="application/json">(.*?)</script>', html, re.S).group(1))
        assert {"Isekai", "Gore"} <= {n["id"] for n in data["nodes"]}
        assert next(n for n in data["nodes"] if n["id"] == "Isekai")["href"] == "/stats/entries?tag=Isekai"
        assert "<b>x" not in html.split("theme-net")[0]
        genres = c.get("/stats/connections?show=genres&status=CURRENT").text
        assert "Too few series match" in genres or "theme-net" in genres
        assert c.get("/stats/connections?show=bogus").status_code == 200
