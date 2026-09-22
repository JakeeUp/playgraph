"""HTTP contracts for the browser surface; these do not simulate a browser."""
import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models import Game, User
from tests.security_helpers import MemoryRedis, auth_headers


@pytest.fixture
def web(db, monkeypatch):
    monkeypatch.setattr(app.state, "arq_pool", MemoryRedis(), raising=False)
    monkeypatch.setitem(app.dependency_overrides, get_db, lambda: db)
    return TestClient(app, base_url="http://localhost:8000")


def test_shell_assets_and_strict_csp(web):
    response = web.get("/")
    assert response.status_code == 200
    assert response.url.path == "/app"
    assert 'name="viewport"' in response.text
    csp = response.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp
    assert "frame-ancestors 'none'" in csp and "form-action 'self'" in csp
    assert "https://shared.fastly.steamstatic.com" in csp
    for name in ["styles.css", "details.css", "social.css", "app.js", "game-detail.js", "rating.js", "feed.js", "library.js", "dom.js", "chart.js", "mark.svg"]:
        assert web.get("/assets/" + name).status_code == 200
    assert web.get("/auth/session").status_code == 401


def test_static_assets_revalidate_while_api_responses_are_never_stored(web):
    first = web.get("/assets/app.js")
    assert first.status_code == 200
    assert first.headers["cache-control"] == "no-cache"
    assert web.get("/assets/app.js", headers={"If-None-Match": first.headers["etag"]}).status_code == 304
    for path in ("/app", "/games", "/auth/session"):
        assert web.get(path).headers["cache-control"] == "no-store"


def test_public_catalog_bounds_search_and_no_private_fields(web, db):
    db.add_all([Game(name="100% Game", steam_appid=100), Game(name="A Game", steam_appid=101),
                Game(name="B Game", steam_appid=102)])
    db.commit()
    result = web.get("/games", params={"limit": 2, "offset": 1}).json()
    assert result["total"] == 3
    assert [game["name"] for game in result["games"]] == ["A Game", "B Game"]
    assert set(result["games"][0]) == {"id", "steam_appid", "name", "genres", "header_image_url", "content_kind"}
    assert web.get("/games", params={"q": "%"}).json()["total"] == 1
    assert web.get("/games", params={"q": "a game"}).json()["total"] == 1
    assert web.get("/games", params={"offset": 100}).json()["games"] == []
    for params in [{"limit": 101}, {"limit": 0}, {"offset": -1}, {"offset": 10001}, {"q": "x" * 121}]:
        assert web.get("/games", params=params).status_code == 422


def test_browser_callback_failure_returns_to_app_without_assertion(web):
    response = web.get("/auth/steam/callback?ui=1&state=invalid&openid.sig=secret", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/app?login_error=1"
    assert "secret" not in response.text


def test_single_game_link_returns_only_public_metadata(web, db):
    db.add(Game(id=23, name="Linkable game", steam_appid=100))
    db.commit()
    response = web.get("/games/23")
    assert response.status_code == 200
    assert response.json() == {"id": 23, "name": "Linkable game", "steam_appid": 100,
                               "genres": None, "header_image_url": None, "content_kind": "game"}
    assert web.get("/games/9999").status_code == 404
    assert web.get("/games/99999999999999999").status_code == 422


def test_recommendations_require_a_session_and_report_unbuilt(web, db):
    """The route has no body yet, but its access contract is already public API.

    An anonymous caller must be refused by the session gate like every other
    /me route, and an authenticated one must get an honest "not built" rather
    than the 500 that raising NotImplementedError produced.
    """
    assert web.get("/me/recommendations").status_code == 401
    assert web.get("/me/recommendations", headers={"Authorization": "Bearer invalid"}).status_code == 401
    db.add(User(id=1, display_name="Player"))
    db.commit()
    response = web.get("/me/recommendations", headers=auth_headers(app.state.arq_pool, 1))
    assert response.status_code == 501
    assert response.json()["detail"] == "Recommendations are not available yet"
