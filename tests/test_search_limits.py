"""Search abuse: control characters are refused and searches have their own budget."""
from tests.test_web_app import web  # noqa: F401  (fixture)

from app.middleware import SEARCHES_PER_MINUTE


def test_control_characters_in_query_are_refused(web):  # noqa: F811
    for value in ("a%00b", "%0a", "x%7f"):
        assert web.get("/games?q=" + value).status_code == 400
    assert web.get("/games?q=caf%C3%A9").status_code == 200


def test_searches_spend_their_own_budget(web):  # noqa: F811
    for number in range(SEARCHES_PER_MINUTE):
        assert web.get("/games", params={"q": f"term{number}"}).status_code == 200
    assert web.get("/games", params={"q": "one more"}).status_code == 429
    # Browsing without a search term is still allowed.
    assert web.get("/games").status_code == 200
