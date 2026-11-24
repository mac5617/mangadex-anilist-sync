"""The Ratings tab: where each rating came from, changing and removing one, and the empty state."""

from fastapi.testclient import TestClient

from mdal.web.app import create_app

MD = "11111111-1111-4111-8111-111111111111"
KEY = f"md:{MD}"
ROW = {"HX-Target": f"rating-md-{MD}"}


def client_for(services):
    return TestClient(create_app(services), follow_redirects=False)


def seed(services, source="ask", verdict="loved"):
    services.repo.conn.execute(
        "INSERT INTO md_new(md_id, title, alt_titles, tags, authors, source, fetched_at) "
        "VALUES (?, 'Love Letters', '[]', '[]', '[]', 'updated', '2026-01-01T00:00:00+00:00')", (MD,))
    services.repo.conn.commit()
    services.repo.set_feedback(KEY, verdict, "Love Letters", source)


def test_empty_state_says_where_ratings_come_from(services):
    with client_for(services) as c:
        html = c.get("/discover/ratings").text
    assert "No ratings yet" in html
    assert 'href="/discover/ask"' in html and 'href="/discover"' in html


def test_each_rating_says_where_it_came_from(services):
    seed(services, source="hide", verdict="not_interested")
    with client_for(services) as c:
        assert "Marked Not interested" in c.get("/discover/ratings").text
    services.repo.set_feedback(KEY, "loved", "Love Letters", "ask")
    with client_for(services) as c:
        assert ">Ask<" in c.get("/discover/ratings").text


def test_changing_a_rating_keeps_where_it_came_from(services):
    seed(services, source="ask")
    with client_for(services) as c:
        html = c.post(f"/series/md/{MD}/rate", data={"verdict": "disliked"}, headers=ROW).text
    row = services.repo.feedback()[KEY]
    assert row["verdict"] == "disliked" and row["source"] == "ask"
    assert ">Ask<" in html


def test_removing_a_rating_can_be_undone(services):
    seed(services, source="ask", verdict="liked")
    with client_for(services) as c:
        html = c.post(f"/series/md/{MD}/rate", data={"verdict": "clear"}, headers=ROW).text
        assert "Removed." in html and "Undo" in html and KEY not in services.repo.feedback()
        c.post(f"/series/md/{MD}/rate", data={"verdict": "liked", "source": "ask"}, headers=ROW)
    row = services.repo.feedback()[KEY]
    assert row["verdict"] == "liked" and row["source"] == "ask"


def test_an_unknown_source_is_saved_as_the_series_page(services):
    seed(services, source="ask")
    services.repo.clear_feedback(KEY)
    with client_for(services) as c:
        c.post(f"/series/md/{MD}/rate", data={"verdict": "liked", "source": "bogus"})
    assert services.repo.feedback()[KEY]["source"] == "page"


def test_the_lede_says_what_each_rating_does(services):
    with client_for(services) as c:
        html = c.get("/discover/ratings").text
    assert "Read it" in html and "Not interested" in html



def test_undo_puts_the_rating_back_with_its_original_date(services):
    """Undo posts back what the Remove answer's button carries, so the row returns as it was, Saved date and all."""
    import html as html_lib
    import json
    import re

    seed(services, source="ask", verdict="liked")
    services.repo.conn.execute("UPDATE rec_feedback SET created_at='2026-10-06T11:47:00+00:00' WHERE key=?", (KEY,))
    services.repo.conn.commit()
    with client_for(services) as c:
        removed = c.post(f"/series/md/{MD}/rate", data={"verdict": "clear"}, headers=ROW).text
        undo = json.loads(html_lib.unescape(re.search(r"hx-vals='([^']*)'[^>]*>Undo<", removed).group(1)))
        c.post(f"/series/md/{MD}/rate", data=undo, headers=ROW)
    row = services.repo.feedback()[KEY]
    assert (row["verdict"], row["source"], row["created_at"]) == ("liked", "ask", "2026-10-06T11:47:00+00:00")


def test_a_saved_date_is_only_taken_from_a_ratings_row_undo(services):
    seed(services, source="ask")
    with client_for(services) as c:
        c.post(f"/series/md/{MD}/rate", data={"verdict": "liked", "saved_at": "2001-01-01T00:00:00+00:00"})
        assert not services.repo.feedback()[KEY]["created_at"].startswith("2001")       # series page: now
        c.post(f"/series/md/{MD}/rate", data={"verdict": "liked", "saved_at": "not a date"}, headers=ROW)
        assert not services.repo.feedback()[KEY]["created_at"].startswith("not")
