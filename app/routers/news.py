"""Public gaming news for the For You page: outlet headlines and Steam posts.

Two sources, each refreshed on its own clock and shared by every visitor:

- APITube allows 100 requests a day on the free plan, so its headlines are
  refreshed at most every two hours.
- Steam announcements cost nothing but one request per game, so they are
  refreshed every half hour, for the games most played on PlayGraph.

Each source's last good result is saved in Redis so an API restart does not
spend a request, and when a source fails its saved copy is served while the
next attempt waits out a cooldown, instead of every page view retrying. The
page reads the merged list a page at a time, newest first, and anything older
than MAX_AGE is left out so the feed stays current.
"""
import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from redis.exceptions import RedisError
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from app.cache import PREFIX
from app.database import SessionLocal
from app.models import Game, PlaytimeSnapshot
from app.services import news, steam_news

router = APIRouter(tags=["news"])
logger = logging.getLogger(__name__)
REFRESH = {"apitube": 2 * 60 * 60, "steam": 30 * 60}
RETRY = 15 * 60  # after a failure, wait this long before trying that source again
KEEP = 3 * 24 * 60 * 60  # how long a saved copy stays servable when refreshes fail
MAX_AGE = 30 * 24 * 60 * 60
# Outer bound on any one refresh, so a hung source can never hold its lock.
FETCH_DEADLINE = 30.0
STEAM_GAMES = 30
PAGE = 12

# Fallback when Redis is down or absent. The locks keep concurrent visitors to
# one refresh per source; the app runs a single process.
_memory: dict = {}
_locks = {name: asyncio.Lock() for name in REFRESH}


async def _read(store, key: str) -> dict:
    if store is not None:
        try:
            raw = await store.get(key)
            if raw:
                return json.loads(raw)
        except (RedisError, OSError, ValueError):
            pass
    return _memory.get(key) or {}


async def _write(store, key: str, saved: dict) -> None:
    _memory[key] = saved
    if store is not None:
        try:
            await store.set(key, json.dumps(saved, separators=(",", ":")), ex=KEEP)
        except (RedisError, OSError):
            pass


async def _latest(store, name: str, fetch: Callable[[], Awaitable[list]]) -> list | None:
    """The source's saved items, refreshed first when due. None if it has never succeeded."""
    key = f"{PREFIX}:news:{name}"
    async with _locks[name]:
        saved, now = await _read(store, key), time.time()
        if now - saved.get("fetched_at", 0) >= REFRESH[name] and now - saved.get("failed_at", 0) >= RETRY:
            try:
                async with asyncio.timeout(FETCH_DEADLINE):
                    saved = {"items": await fetch(), "fetched_at": now}
            except (httpx.HTTPError, ValueError, SQLAlchemyError, TimeoutError) as error:
                # Type only: APITube's key travels in a header, never the URL.
                logger.warning("News source %s failed: %s", name, type(error).__name__)
                saved = {**saved, "failed_at": now}
            await _write(store, key, saved)
    return saved.get("items")


def _most_played(limit: int) -> list[tuple[int, str]]:
    """Games owned by the most PlayGraph players, then by the longest playtime."""
    with SessionLocal() as db:
        rows = (db.query(Game.steam_appid, Game.name)
                .join(PlaytimeSnapshot, PlaytimeSnapshot.game_id == Game.id)
                .filter(Game.content_kind == "game", Game.steam_appid.is_not(None))
                .group_by(Game.id, Game.steam_appid, Game.name)
                .order_by(func.count(func.distinct(PlaytimeSnapshot.user_id)).desc(),
                          func.max(PlaytimeSnapshot.playtime_minutes).desc(), Game.id)
                .limit(limit).all())
    return [(appid, name) for appid, name in rows]


async def _steam() -> list[dict]:
    return await steam_news.fetch(await run_in_threadpool(_most_played, STEAM_GAMES))


async def _apitube() -> list[dict]:
    return await run_in_threadpool(news.fetch_gaming_news)


def _timestamp(item: dict) -> float:
    try:
        return datetime.fromisoformat(item["published_at"]).timestamp()
    except (KeyError, TypeError, ValueError):
        return 0.0


@router.get("/news")
async def gaming_news(request: Request, offset: int = Query(0, ge=0, le=1000)):
    store = getattr(request.app.state, "arq_pool", None)
    sources = [_latest(store, "steam", _steam)]
    if news.configured():
        sources.append(_latest(store, "apitube", _apitube))
    results = await asyncio.gather(*sources)
    if all(items is None for items in results):
        raise HTTPException(502, "News is unavailable right now. Try again later.")
    cutoff = time.time() - MAX_AGE
    merged = sorted((item for items in results for item in items or [] if _timestamp(item) >= cutoff),
                    key=_timestamp, reverse=True)
    end = offset + PAGE
    return {"items": merged[offset:end], "next": end if end < len(merged) else None}
