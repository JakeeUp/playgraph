"""The public response cache: served from Redis, never stale after a write, never private."""
import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

from app.cache import CATALOG, PREFIX, cached, invalidate
from app.database import get_db
from app.models import Game, User
from app.routers import catalog, feed, reviews
from tests.security_helpers import MemoryRedis, auth_headers


@pytest.fixture
def api(db):
    app = FastAPI()
    for router in (catalog.router, feed.router, reviews.router):
        app.include_router(router)
    app.state.arq_pool = MemoryRedis()
    app.dependency_overrides[get_db] = lambda: db
    db.add_all([User(id=1, display_name="Author"), User(id=2, display_name="Reader"),
                Game(id=1, steam_appid=10, name="Game")])
    db.commit()
    with TestClient(app) as client:
        yield client


def entries(store):
    return {key: value for key, value in store.data.items()
            if key.startswith(PREFIX + ":") and ":gen:" not in key}


def test_public_reads_are_served_from_the_cache_until_invalidated(api, db):
    store = api.app.state.arq_pool
    first = api.get("/games").json()
    assert first["total"] == 1
    # Written behind the app's back, so only an invalidation can reveal it.
    db.add(Game(id=2, steam_appid=20, name="Sneaked in")); db.commit()
    assert api.get("/games").json() == first
    asyncio.run(invalidate(store, CATALOG))
    assert api.get("/games").json()["total"] == 2


def test_the_writer_reads_its_own_write_immediately(api):
    reviews_path, author = "/games/1/reviews", auth_headers(api.app.state.arq_pool, 1)
    assert api.get(reviews_path).json() == []
    assert api.get("/feed").json()["total"] == 0
    review = api.post(reviews_path, json={"rating": 4, "body": "First"}, headers=author).json()
    assert [row["id"] for row in api.get(reviews_path).json()] == [review["id"]]
    assert api.get("/feed").json()["total"] == 1

    comments_path = f"/reviews/{review['id']}/comments"
    assert api.get(comments_path).json() == []
    api.post(comments_path, json={"body": "Reply"}, headers=auth_headers(api.app.state.arq_pool, 2))
    assert [row["body"] for row in api.get(comments_path).json()] == ["Reply"]
    assert api.get(f"/reviews/{review['id']}").json()["comment_count"] == 1

    api.patch(f"/reviews/{review['id']}", json={"rating": 2, "body": "Changed"}, headers=author)
    assert api.get(reviews_path).json()[0]["body"] == "Changed"
    api.delete(f"/reviews/{review['id']}", headers=author)
    assert api.get(reviews_path).json() == []
    assert api.get(f"/reviews/{review['id']}").status_code == 404


def test_a_cache_hit_matches_the_miss_it_replaced(api):
    api.post("/games/1/reviews", json={"rating": 5}, headers=auth_headers(api.app.state.arq_pool, 1))
    miss = api.get("/feed")
    hit = api.get("/feed")
    assert miss.content == hit.content


def test_redis_trouble_costs_speed_not_answers(api):
    store = api.app.state.arq_pool

    async def broken(*_args, **_kwargs):
        raise RedisConnectionError("down")
    store.get = store.set = broken
    response = api.get("/games")
    assert response.status_code == 200 and response.json()["total"] == 1


def test_only_json_is_stored_and_private_reads_are_never_cached(api):
    store = api.app.state.arq_pool
    api.get("/games"); api.get("/games/1"); api.get("/games/1/reviews"); api.get("/feed")
    public = entries(store)
    assert len(public) == 4
    for value in public.values():
        json.loads(value)  # data, never a pickled object
    assert api.get("/me/games/1/review", headers=auth_headers(store, 1)).status_code == 200
    assert entries(store) == public


def _request():
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(arq_pool=MemoryRedis())))


def test_a_herd_of_concurrent_misses_runs_one_query():
    calls, request = [], _request()

    def compute():
        calls.append(1)
        time.sleep(0.05)  # long enough that every caller arrives mid-computation
        return {"computed": len(calls)}

    async def herd():
        return await asyncio.gather(*(cached(request, CATALOG, ("herd",), 60, compute) for _ in range(25)))
    assert asyncio.run(herd()) == [{"computed": 1}] * 25
    assert len(calls) == 1


def test_a_failed_computation_is_shared_not_repeated():
    calls, request = [], _request()

    def compute():
        calls.append(1)
        time.sleep(0.05)
        raise HTTPException(status_code=404, detail="Game not found")

    async def herd():
        return await asyncio.gather(*(cached(request, CATALOG, ("missing",), 60, compute) for _ in range(10)),
                                    return_exceptions=True)
    assert [error.status_code for error in asyncio.run(herd())] == [404] * 10
    assert len(calls) == 1


def test_a_waiter_takes_over_when_the_computing_caller_disconnects():
    calls, request = [], _request()

    def compute():
        calls.append(1)
        time.sleep(0.1)
        return {"ok": True}

    async def scenario():
        leader = asyncio.create_task(cached(request, CATALOG, ("gone",), 60, compute))
        await asyncio.sleep(0.02)
        waiter = asyncio.create_task(cached(request, CATALOG, ("gone",), 60, compute))
        await asyncio.sleep(0.02)
        leader.cancel()
        return await waiter, leader
    result, leader = asyncio.run(scenario())
    assert result == {"ok": True} and leader.cancelled()
    assert len(calls) == 2
