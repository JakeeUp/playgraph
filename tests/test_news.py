import time
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import settings
from app.routers import news as news_router
from app.services import news, steam_news


def iso(seconds_ago: float) -> str:
    return datetime.fromtimestamp(time.time() - seconds_ago, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture(autouse=True)
def fresh_news():
    """Each test starts with nothing saved."""
    news_router._memory.clear()
    yield
    news_router._memory.clear()


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(news_router.router)
    return TestClient(app)


@pytest.fixture
def upstream(monkeypatch):
    """Answer APITube requests from a handler and record what was sent."""
    sent, reply = [], {"status": 200, "json": {"results": []}}
    real_client = httpx.Client

    def handle(request):
        sent.append(request)
        return httpx.Response(reply["status"], json=reply["json"])

    monkeypatch.setattr(news.httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs))
    monkeypatch.setattr(settings, "apitube_api_key", SecretStr("apitube-test-key"))
    return sent, reply


@pytest.fixture
def steam(monkeypatch):
    """Answer Steam news requests per appid; the most played games are fixed."""
    sent, posts, status = [], {}, {"code": 200}
    real_client = httpx.AsyncClient

    def handle(request):
        sent.append(request)
        appid = int(request.url.params["appid"])
        return httpx.Response(status["code"], json={"appnews": {"appid": appid, "newsitems": posts.get(appid, [])}})

    monkeypatch.setattr(steam_news.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs))
    monkeypatch.setattr(news_router, "_most_played", lambda limit: [(730, "Counter-Strike 2"), (39210, "FINAL FANTASY XIV Online")])
    return sent, posts, status


def post(title, seconds_ago=3600, contents="", url=None):
    return {"title": title, "url": url or f"https://steamstore-a.akamaihd.net/news/externalpost/steam_community_announcements/{abs(hash(title))}",
            "date": int(time.time() - seconds_ago), "contents": contents, "feed_type": 1}


def test_without_an_apitube_key_only_steam_is_asked(client, upstream, steam, monkeypatch):
    sent, _ = upstream
    steam_sent, posts, _ = steam
    monkeypatch.setattr(settings, "apitube_api_key", SecretStr(""))
    posts[730] = [post("Counter-Strike 2 Update")]
    body = client.get("/news").json()
    assert [item["title"] for item in body["items"]] == ["Counter-Strike 2 Update"]
    assert sent == [] and len(steam_sent) == 2
    assert steam_sent[0].url.params["feeds"] == "steam_community_announcements"


def test_key_travels_only_in_the_header_and_sources_are_gaming_outlets(client, upstream, steam):
    sent, _ = upstream
    client.get("/news")
    request = sent[0]
    assert request.headers["X-API-Key"] == "apitube-test-key"
    assert "apitube-test-key" not in str(request.url)
    assert request.url.params["source.domain"] == "pcgamer.com,eurogamer.net,rockpapershotgun.com"
    assert request.url.params["sort.by"] == "published_at"
    assert request.url.params["per_page"] == "10"


def test_only_https_links_survive_and_titles_are_deduplicated(client, upstream, steam):
    _, reply = upstream
    reply["json"] = {"results": [
        {"href": "https://www.pcgamer.com/a", "title": "Big  reveal", "description": "(Image credit: Valve) " + "x" * 400,
         "published_at": iso(60), "source": {"domain": "pcgamer.com"}},
        {"href": "https://www.eurogamer.net/b", "title": "big reveal", "published_at": iso(60), "source": {"domain": "eurogamer.net"}},
        {"href": "javascript:alert(1)", "title": "Script link", "published_at": iso(60)},
        {"href": "http://pcgamer.com/c", "title": "Plain http", "published_at": iso(60)},
        {"href": "https://pcgamer.com/d", "title": "", "published_at": iso(60)},
        "not an article",
    ]}
    items = client.get("/news").json()["items"]
    assert [item["url"] for item in items] == ["https://www.pcgamer.com/a"]
    assert items[0]["title"] == "Big reveal"
    assert items[0]["source"] == "pcgamer.com"
    assert items[0]["summary"].startswith("xxx")  # the photo caption is dropped
    assert len(items[0]["summary"]) == 280 and items[0]["summary"].endswith("…")


def test_images_are_kept_only_from_the_outlets_image_hosts(client, upstream, steam):
    _, reply = upstream
    reply["json"] = {"results": [
        {"href": f"https://pcgamer.com/{name}", "title": name, "image": image, "published_at": iso(index)}
        for index, (name, image) in enumerate([
            ("A", "https://cdn.mos.cms.futurecdn.net/a.jpg"),
            ("B", "https://assetsio.gnwcdn.com/b.jpg"),
            ("C", "https://tracker.example.com/pixel.gif"),
            ("D", "http://cdn.mos.cms.futurecdn.net/d.jpg"),
            ("E", "https://evilfuturecdn.net/e.jpg"),
            ("F", None)])]}
    images = [item["image"] for item in client.get("/news").json()["items"]]
    assert images == ["https://cdn.mos.cms.futurecdn.net/a.jpg", "https://assetsio.gnwcdn.com/b.jpg",
                      None, None, None, None]


def test_steam_posts_become_plain_text_with_a_rebuilt_image(client, upstream, steam):
    _, posts, _ = steam
    posts[39210] = [post("Fall Guys event", contents=(
        "[p]\\[ MISC ] Join &amp; play[/p][img src=\"{STEAM_CLAN_IMAGE}/4459756/7a47abc.jpg\"][/img]"
        "<script>x</script>[url=https://evil.example]link[/url]"))]
    posts[730] = [post("No image", url="http://insecure.example/post"), post("Kept, no image", contents="[b]Patch[/b]")]
    items = client.get("/news").json()["items"]
    by_title = {item["title"]: item for item in items}
    assert set(by_title) == {"Fall Guys event", "Kept, no image"}
    fall = by_title["Fall Guys event"]
    assert fall["image"] == "https://clan.fastly.steamstatic.com/images/4459756/7a47abc.jpg"
    assert fall["summary"] == "[ MISC ] Join & play x link"
    assert fall["source"] == "FINAL FANTASY XIV Online" and fall["appid"] == 39210
    assert by_title["Kept, no image"]["image"] is None


def test_sources_merge_newest_first_old_news_is_dropped_and_pages_follow(client, upstream, steam, monkeypatch):
    _, reply = upstream
    _, posts, _ = steam
    monkeypatch.setattr(news_router, "PAGE", 2)
    reply["json"] = {"results": [
        {"href": "https://pcgamer.com/new", "title": "Outlet new", "published_at": iso(100)},
        {"href": "https://pcgamer.com/old", "title": "Outlet from July", "published_at": iso(news_router.MAX_AGE + 60)}]}
    posts[730] = [post("Steam newest", seconds_ago=10), post("Steam older", seconds_ago=5000)]
    first = client.get("/news").json()
    assert [item["title"] for item in first["items"]] == ["Steam newest", "Outlet new"]
    assert first["next"] == 2
    second = client.get("/news", params={"offset": 2}).json()
    assert [item["title"] for item in second["items"]] == ["Steam older"]
    assert second["next"] is None


def test_each_source_is_fetched_once_per_refresh_window(client, upstream, steam, monkeypatch):
    sent, _ = upstream
    steam_sent, _, _ = steam
    clock = [time.time()]
    monkeypatch.setattr(news_router.time, "time", lambda: clock[0])
    for _ in range(5):
        assert client.get("/news").status_code == 200
    assert len(sent) == 1 and len(steam_sent) == 2
    clock[0] += news_router.REFRESH["steam"]
    client.get("/news")
    assert len(sent) == 1 and len(steam_sent) == 4
    clock[0] += news_router.REFRESH["apitube"]
    client.get("/news")
    assert len(sent) == 2


def test_a_failed_refresh_serves_old_headlines_and_waits_before_retrying(client, upstream, steam, monkeypatch):
    sent, reply = upstream
    clock = [time.time()]
    monkeypatch.setattr(news_router.time, "time", lambda: clock[0])
    reply["json"] = {"results": [{"href": "https://pcgamer.com/a", "title": "Kept", "published_at": iso(60)}]}
    client.get("/news")

    clock[0] += news_router.REFRESH["apitube"]
    reply["status"] = 429
    for _ in range(3):
        response = client.get("/news")
        assert response.status_code == 200
        assert [item["title"] for item in response.json()["items"]] == ["Kept"]
    assert len(sent) == 2  # one failed attempt, then the cooldown holds

    clock[0] += news_router.RETRY
    client.get("/news")
    assert len(sent) == 3


def test_one_steam_game_failing_keeps_the_others(client, upstream, steam, monkeypatch):
    _, posts, _ = steam
    posts[39210] = [post("Still here")]
    real_parse = steam_news.parse

    def parse(payload, appid, name):
        if appid == 730:
            raise ValueError("bad payload")
        return real_parse(payload, appid, name)
    monkeypatch.setattr(steam_news, "parse", parse)
    assert [item["title"] for item in client.get("/news").json()["items"]] == ["Still here"]


def test_everything_failing_with_nothing_saved_is_a_plain_502_that_waits(client, upstream, steam):
    sent, reply = upstream
    steam_sent, _, status = steam
    reply["status"], status["code"] = 401, 500
    response = client.get("/news")
    assert response.status_code == 502
    assert "apitube-test-key" not in response.text
    assert client.get("/news").status_code == 502
    assert len(sent) == 1 and len(steam_sent) == 2
