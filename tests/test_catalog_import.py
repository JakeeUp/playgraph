"""Missing-game search and imports use authoritative IGDB metadata, not browser fields."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.database import Base, get_db
from app.models import Game, GameExternalId, GamePlatform, User
from app.routers import catalog, reviews
from app.services import igdb
from tests.security_helpers import MemoryRedis, auth_headers
from tests.test_igdb import RAW_GAME
from tests.test_social import cookie_session


class Provider:
    calls = []
    failure = None

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def search(self, text, **kwargs):
        self.calls.append((text, kwargs))
        if self.failure:
            raise self.failure
        return [igdb.normalize_game(RAW_GAME)]

    async def import_game(self, game_id):
        self.calls.append(game_id)
        if self.failure:
            raise self.failure
        return igdb.normalize_game(RAW_GAME) if game_id == RAW_GAME["id"] else None


def application(db_dependency, store):
    app = FastAPI()
    app.include_router(catalog.router)
    app.include_router(reviews.router)
    app.dependency_overrides[get_db] = db_dependency
    app.state.arq_pool = store
    return app


@pytest.fixture
def api(db, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(catalog, "IGDBClient", Provider)
    Provider.calls = []
    Provider.failure = None
    db.add_all([User(id=1, display_name="Alice"), User(id=2, display_name="Bob"),
                Game(id=1, name="The Witcher local", steam_appid=20)])
    db.flush()
    db.add(GamePlatform(game_id=1, slug="win", name="PC"))
    db.commit()
    store = MemoryRedis()
    with TestClient(application(lambda: db, store), base_url="http://localhost:8000") as client:
        client.store = store
        yield client


def add(api, **extra):
    return api.post("/games/import", json={"igdb_id": 1942, **extra}, headers=auth_headers(api.store))


def test_guest_only_searches_local_catalog(api):
    result = api.get("/games/search?q=witcher").json()
    assert [row["id"] for row in result["local"]] == [1]
    assert result["external"] == [] and not Provider.calls
    assert api.post("/games/import", json={"igdb_id": 1942}).status_code == 401


def test_search_cache_normalizes_text_filters_platform_and_annotates_imports(api):
    headers = auth_headers(api.store)
    first = api.get('/games/search?q=The%20Witcher&platform=win', headers=headers)
    assert first.status_code == 200
    assert first.json()["external"][0]["game_id"] is None
    assert len(first.json()["local"]) == 1
    assert api.get('/games/search?q=THE%20%20Witcher&platform=win', headers=headers).json() == first.json()
    assert len(Provider.calls) == 1
    assert api.get('/games/search?q=the%20witcher&platform=win').json()["external"] == []
    assert len(Provider.calls) == 1
    new = add(api).json()
    again = api.get('/games/search?q=the%20witcher&platform=win', headers=headers).json()
    assert again["external"][0]["game_id"] == new["id"]
    assert len(Provider.calls) == 2
    other = api.get('/games/search?q=the%20witcher&platform=ps5', headers=headers)
    assert other.json()["local"][0]["id"] == new["id"]
    assert len(Provider.calls) == 3
    api.store.now += 86401
    assert api.get('/games/search?q=the%20witcher&platform=win', headers=auth_headers(api.store)).status_code == 200
    assert len(Provider.calls) == 4


def test_import_is_idempotent_ignores_forged_fields_and_never_exposes_submitter(api, db):
    response = add(api, name="Forged", steam_appid=999, cover_url="https://evil.test/x", added_by_user_id=2)
    assert response.status_code == 200
    row = response.json()
    assert row["name"] == RAW_GAME["name"] and row["steam_appid"] is None
    assert row["catalog_source"] == "igdb" and row["cover_url"].endswith('/co1wyy.jpg')
    assert {p["slug"] for p in row["platforms"]} == {"win", "ps5"}
    assert "added_by_user_id" not in row and "added_at" not in row
    saved = db.get(Game, row["id"])
    assert saved.added_by_user_id == 1 and saved.added_at is not None
    assert add(api).json() == row and Provider.calls == [1942]
    assert db.query(GameExternalId).filter_by(provider="igdb", external_id="1942").count() == 1
    assert api.get(f'/games/{row["id"]}').json() == row
    assert api.get('/games?platform=ps5').json()["total"] == 1
    review = api.post(f'/games/{row["id"]}/reviews', json={"rating": 4, "body": "Synthetic review"}, headers=auth_headers(api.store))
    assert review.status_code == 201
    assert review.json()["verified_playtime_minutes"] is None and review.json()["verified_source"] is None


def test_search_and_add_budgets(api):
    headers = auth_headers(api.store)
    for _ in range(20):
        assert api.get('/games/search?q=witcher', headers=headers).status_code == 200
    assert api.get('/games/search?q=witcher', headers=headers).status_code == 429
    assert len(Provider.calls) == 1
    for _ in range(30):
        assert api.post('/games/import', json={"igdb_id": 2000}, headers=headers).status_code == 404
    assert api.post('/games/import', json={"igdb_id": 2000}, headers=headers).status_code == 429
    api.store.now += 2 * 86400 + 1
    assert api.post('/games/import', json={"igdb_id": 2000}, headers=auth_headers(api.store)).status_code == 404


def test_provider_failures_are_reported_without_a_spent_budget(api):
    Provider.failure = igdb.IGDBRateLimitedError("busy")
    assert add(api).status_code == 429
    Provider.failure = igdb.IGDBAuthError("sanitized")
    assert add(api).status_code == 503


@pytest.mark.parametrize("value", [True, 0, -1, "1942", 2147483648])
def test_import_id_is_strict_and_bounded(api, value):
    assert api.post('/games/import', json={"igdb_id": value}, headers=auth_headers(api.store)).status_code == 422
    assert not Provider.calls


def test_cookie_write_requires_exact_origin_and_csrf(api):
    csrf = cookie_session(api, 1)
    for headers in ({}, {"Origin": "http://evil.test", "X-CSRF-Token": csrf},
                    {"Origin": "http://localhost:8000"}):
        assert api.post('/games/import', json={"igdb_id": 1942}, headers=headers).status_code == 403
    assert not Provider.calls
    assert api.post('/games/import', json={"igdb_id": 1942}, headers={"Origin": "http://localhost:8000", "X-CSRF-Token": csrf}).status_code == 200


def test_demo_refuses_provider_access(api, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    assert api.get('/games/search?q=witcher').status_code == 404
    assert add(api).status_code == 404
    assert api.post('/games/import', json={"igdb_id": 1942}).status_code == 404
    assert not Provider.calls


def test_parallel_imports_return_one_game(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + (tmp_path / 'catalog-test.db').as_posix(), connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        db.add(User(id=1, display_name="Alice"))
        db.commit()
    def dependency():
        with sessions() as db:
            yield db
    barrier = Barrier(2)
    class ConcurrentProvider(Provider):
        async def import_game(self, game_id):
            await asyncio.to_thread(barrier.wait, 10)
            return igdb.normalize_game(RAW_GAME)
    monkeypatch.setattr(settings, 'demo_mode', False)
    monkeypatch.setattr(catalog, 'IGDBClient', ConcurrentProvider)
    store = MemoryRedis()
    with TestClient(application(dependency, store)) as client:
        def post():
            return client.post('/games/import', json={"igdb_id": 1942}, headers=auth_headers(store))
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: post(), range(2)))
        assert [r.status_code for r in responses] == [200, 200]
        assert responses[0].json()["id"] == responses[1].json()["id"]
    with sessions() as db:
        assert db.query(Game).count() == db.query(GameExternalId).count() == 1
    engine.dispose()
