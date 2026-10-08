"""Provider display names are cleaned before storage; news refreshes are bounded in time."""
import asyncio
import inspect

import httpx

from app import worker
from app.routers import accounts, auth, psn as psn_router
from app.routers import news as news_router
from app.schemas import display_name
from app.services import steam_news


def test_display_name_strips_nul_controls_and_bidi_overrides():
    raw = " ‮A\x00B⁦C​\x07D﻿ " + "E" * 500
    name = display_name(raw, "fallback")
    assert len(name) <= 80
    for bad in ("\x00", "‮", "⁦", "​", "\x07", "﻿"):
        assert bad not in name
    assert name.startswith("ABCD")


def test_display_name_falls_back_for_blank_or_non_text():
    assert display_name("   ", "Player123456") == "Player123456"
    assert display_name("‮\x00", "Player") == "Player"
    assert display_name(None, "Player") == "Player"
    assert display_name(123, "X") == "X"
    assert display_name("a\n\tb", "X") == "a b"


def test_every_provider_name_path_uses_the_shared_sanitizer():
    # Steam sign-up, Clerk sign-up, the PSN handle and PSN game names all
    # store provider text.
    assert "display_name((summary" in inspect.getsource(auth.steam_callback)
    assert "display_name(profile.get" in inspect.getsource(accounts.exchange)
    assert "display_handle=display_name(" in inspect.getsource(psn_router.check_link)
    sync = inspect.getsource(worker._sync_psn_titles)
    assert "display_handle = display_name(" in sync and sync.count("Game(name=display_name(") == 2


def test_steam_news_refresh_returns_partial_results_at_its_deadline(monkeypatch):
    monkeypatch.setattr(steam_news, "DEADLINE", 0.2)

    def handler(request):
        if request.url.params["appid"] == "2":
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json={"appnews": {"newsitems": [
            {"url": "https://example.com/1", "title": "Patch", "date": 1700000000, "contents": ""}]}}, request=request)

    async def slow_send(self, request, **kwargs):
        if request.url.params["appid"] == "3":
            await asyncio.sleep(5)
        return handler(request)

    monkeypatch.setattr(httpx.AsyncClient, "send", slow_send)

    async def run():
        loop = asyncio.get_running_loop()
        start = loop.time()
        items = await steam_news.fetch([(1, "One"), (3, "Slow")])
        return items, loop.time() - start

    items, elapsed = asyncio.run(run())
    assert elapsed < 2
    assert [item["appid"] for item in items] == [1]


def test_steam_news_refresh_with_nothing_finished_is_a_timeout(monkeypatch):
    monkeypatch.setattr(steam_news, "DEADLINE", 0.1)

    async def hang(self, request, **kwargs):
        await asyncio.sleep(5)

    monkeypatch.setattr(httpx.AsyncClient, "send", hang)
    try:
        asyncio.run(steam_news.fetch([(1, "One")]))
    except TimeoutError:
        pass
    else:
        raise AssertionError("expected TimeoutError")


def test_latest_treats_a_hung_source_as_a_failure_and_releases_the_lock(monkeypatch):
    monkeypatch.setattr(news_router, "FETCH_DEADLINE", 0.1)
    monkeypatch.setattr(news_router, "_memory", {})
    monkeypatch.setattr(news_router, "_locks", {name: asyncio.Lock() for name in news_router.REFRESH})

    async def hang():
        await asyncio.sleep(5)
        return []

    async def run():
        items = await news_router._latest(None, "steam", hang)
        saved = news_router._memory[f"{news_router.PREFIX}:news:steam"]
        return items, saved, news_router._locks["steam"].locked()

    items, saved, locked = asyncio.run(run())
    assert items is None
    assert "failed_at" in saved  # the cooldown applies, so the next visitor does not wait again
    assert locked is False
