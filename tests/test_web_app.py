"""HTTP contracts for the browser surface; these do not simulate a browser."""
import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models import Game, GamePlatform, User
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
    assert "https://*.futurecdn.net" in csp and "https://clan.fastly.steamstatic.com" in csp and " https: " not in csp and " https:;" not in csp
    assert "font-src 'self'" in csp
    for name in ["styles.css", "details.css", "social.css", "app.js", "game-detail.js", "rating.js", "feed.js", "library.js", "dom.js", "chart.js",
                 "mark.svg", "favicon-32.png", "apple-touch-icon.png", "fonts/plex-sans-400.woff2", "fonts/plex-mono-400.woff2"]:
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
    assert set(result["games"][0]) == {"id", "steam_appid", "name", "genres", "header_image_url", "content_kind",
                                       "first_release_date", "cover_url", "hero_url"}
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
    game = Game(id=23, name="Linkable game", steam_appid=100, summary="A short summary.",
                cover_image_id="co1abc", hero_image_id="ar1abc")
    game.platforms.append(GamePlatform(slug="win", name="PC (Microsoft Windows)", abbreviation="PC"))
    db.add(game)
    db.commit()
    response = web.get("/games/23")
    assert response.status_code == 200
    assert response.json() == {
        "id": 23, "name": "Linkable game", "steam_appid": 100, "genres": None, "header_image_url": None,
        "content_kind": "game", "first_release_date": None, "summary": "A short summary.",
        "cover_url": "https://images.igdb.com/igdb/image/upload/t_cover_big/co1abc.jpg",
        "hero_url": "https://images.igdb.com/igdb/image/upload/t_1080p/ar1abc.jpg",
        "platforms": [{"slug": "win", "name": "PC (Microsoft Windows)", "abbreviation": "PC"}]}
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


def test_a_bad_link_gets_a_page_while_api_misses_stay_json(web):
    page = web.get("/no-such-page", headers={"Accept": "text/html"})
    assert page.status_code == 404 and page.headers["content-type"].startswith("text/html")
    assert "Page not found" in page.text
    csp = page.headers["content-security-policy"]
    assert "style-src 'self'" in csp and "script-src" not in csp and "frame-ancestors 'none'" in csp
    assert web.get("/no-such-page").json() == {"detail": "Not Found"}
    game = web.get("/games/9999", headers={"Accept": "text/html"})
    assert game.status_code == 404 and game.json() == {"detail": "Game not found"}


def test_text_assets_are_gzipped_without_losing_security_or_cache_headers(web):
    script = web.get("/assets/app.js", headers={"Accept-Encoding": "gzip"})
    assert script.headers["content-encoding"] == "gzip"
    assert "accept-encoding" in script.headers["vary"].lower()
    assert int(script.headers["content-length"]) < len(script.content)
    assert script.headers["cache-control"] == "no-cache" and script.headers["x-content-type-options"] == "nosniff"
    assert web.get("/assets/app.js", headers={"Accept-Encoding": "gzip",
                                              "If-None-Match": script.headers["etag"]}).status_code == 304
    page = web.get("/app", headers={"Accept-Encoding": "gzip"})
    assert page.headers["content-encoding"] == "gzip" and page.headers["cache-control"] == "no-store"
    assert "script-src 'self'" in page.headers["content-security-policy"]
    # Fonts are already compressed; clients that do not ask for gzip get identity.
    assert "content-encoding" not in web.get("/assets/fonts/archivo-var.woff2", headers={"Accept-Encoding": "gzip"}).headers
    assert "content-encoding" not in web.get("/assets/app.js", headers={"Accept-Encoding": "identity"}).headers


def test_auth_responses_and_small_bodies_are_never_compressed():
    import asyncio

    from starlette.responses import PlainTextResponse

    from app.main import CompressionMiddleware

    async def endpoint(scope, receive, send):
        await PlainTextResponse("token " * 1000)(scope, receive, send)

    async def run(path, size_app=endpoint):
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        scope = {"type": "http", "method": "GET", "path": path, "headers": [(b"accept-encoding", b"gzip")]}
        await CompressionMiddleware(size_app)(scope, receive, send)
        return {k.decode().lower(): v.decode() for k, v in sent[0]["headers"]}

    async def tiny(scope, receive, send):
        await PlainTextResponse("ok")(scope, receive, send)

    assert asyncio.run(run("/feed"))["content-encoding"] == "gzip"
    assert "content-encoding" not in asyncio.run(run("/auth/session"))
    assert "content-encoding" not in asyncio.run(run("/feed", tiny))


def _module_graph(entry):
    import re
    from pathlib import Path

    static = Path(__file__).resolve().parents[1] / "app" / "static"
    seen, pending = set(), [entry]
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        source = (static / name).read_text(encoding="utf-8")
        pending += re.findall(r"""^import\s[^'"]*['"]\./([\w-]+\.js)['"]""", source, re.M)
    return seen - {entry}


def test_pages_preload_their_whole_module_graph_and_existing_fonts():
    import re
    from pathlib import Path

    static = Path(__file__).resolve().parents[1] / "app" / "static"
    faces = (static / "styles.css").read_text(encoding="utf-8")
    for page, entry in (("index.html", "app.js"), ("account.html", "account.js")):
        html = (static / page).read_text(encoding="utf-8")
        preloaded = set(re.findall(r'<link rel="modulepreload" href="/assets/([\w-]+\.js)">', html))
        assert preloaded == _module_graph(entry), page
        fonts = re.findall(r'<link rel="preload" href="(/assets/fonts/[\w-]+\.woff2)" as="font" type="font/woff2" crossorigin>', html)
        assert fonts and all(f'url("{font}")' in faces for font in fonts), page
