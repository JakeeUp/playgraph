"""DEMO_MODE: read-only, no real sign-in, no external services, no Redis."""
import asyncio
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings, settings
from app.database import Base, get_db
from app.demo import DEMO_STEAM_ID, DemoGuard
from app.main import app as real_app
from app.memory_store import MemoryStore
from app.middleware import SecurityMiddleware
from app.models import Comment, Game, LinkedAccount, Platform, PlaytimeSnapshot, Review, User
from app.routers import accounts, auth, demo, library, news, psn, reviews
from scripts import seed_demo

ORIGIN = {"Origin": "http://localhost:8000", "X-PlayGraph-Auth": "1"}


def build(db):
    app = FastAPI()
    for router in (auth.router, accounts.router, library.router, reviews.router, news.router, psn.router, demo.router):
        app.include_router(router)
    app.add_middleware(DemoGuard)
    app.add_middleware(SecurityMiddleware)
    app.state.arq_pool = MemoryStore()
    app.dependency_overrides[get_db] = lambda: db
    return app


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    user = User(display_name="Demo Player")
    db.add(user)
    db.flush()
    db.add(LinkedAccount(user_id=user.id, platform=Platform.steam, platform_user_id=DEMO_STEAM_ID))
    db.add(Game(id=1, steam_appid=10, name="Game"))
    db.commit()
    with TestClient(build(db), base_url="http://localhost:8000") as value:
        yield value


def login(client):
    assert client.post("/demo/login", headers=ORIGIN).status_code == 204
    session = client.get("/auth/session").json()
    return {"Origin": "http://localhost:8000", "X-CSRF-Token": session["csrf_token"]}


def test_demo_login_issues_a_normal_session_and_logout_works(client):
    headers = login(client)
    assert client.get("/auth/session").json()["user"]["display_name"] == "Demo Player"
    assert client.get("/me/library").status_code == 200
    assert client.post("/auth/logout", headers=headers).status_code == 204
    assert client.get("/auth/session").status_code == 401


def test_demo_login_needs_the_browser_checks(client):
    assert client.post("/demo/login").status_code == 403
    assert client.post("/demo/login", headers={**ORIGIN, "Origin": "https://evil.example"}).status_code == 403
    assert client.post("/demo/login", headers={"Origin": ORIGIN["Origin"]}).status_code == 403


def test_demo_login_without_seed_data_is_unavailable(client, db):
    db.query(LinkedAccount).delete()
    db.commit()
    assert client.post("/demo/login", headers=ORIGIN).status_code == 503


@pytest.mark.parametrize("method,path", [
    ("POST", "/games/1/reviews"), ("PATCH", "/reviews/1"), ("DELETE", "/reviews/1"),
    ("POST", "/reviews/1/comments"), ("PUT", "/anything"), ("POST", "/demo/other"),
    ("POST", "/auth/logout/extra")])
def test_every_other_write_is_refused_as_read_only(client, method, path):
    headers = login(client)
    response = client.request(method, path, headers=headers, json={"rating": 5, "body": "x"})
    assert response.status_code == 403
    assert response.json()["detail"] == "The demo is read-only."


def test_nothing_is_written_by_a_blocked_review(client, db):
    headers = login(client)
    assert client.post("/games/1/reviews", headers=headers, json={"rating": 5, "body": "hello"}).status_code == 403
    assert db.query(Review).count() == 0 and db.query(Comment).count() == 0


@pytest.mark.parametrize("path", [
    "/auth/steam/login", "/auth/steam/callback", "/auth/steam/connect/callback", "/auth/psn", "/auth/clerk/link",
    "/me/psn/sync", "/me/psn/sync/status/x", "/me/sync", "/me/sync/status/x", "/news"])
def test_external_provider_routes_are_disabled_without_calling_out(client, path, monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("external call attempted")
    monkeypatch.setattr("httpx.AsyncClient.request", forbidden)
    monkeypatch.setattr("httpx.AsyncClient.get", forbidden)
    for method in ("GET", "POST"):
        assert client.request(method, path, headers=ORIGIN).status_code == 404


def test_demo_config_drops_provider_credentials():
    config = Settings(_env_file=None, demo_mode=True, clerk_enabled=True, psn_npsso="x" * 64,
                      igdb_client_id="id", igdb_client_secret="secret", steam_api_key="real-key",
                      jwt_secret="x" * 40, database_url="sqlite://")
    assert not (config.clerk_enabled or config.psn_enabled or config.igdb_enabled)
    assert config.steam_api_key.get_secret_value() == ""


def test_demo_production_config_needs_no_provider_keys_or_redis():
    base = dict(_env_file=None, demo_mode=True, environment="production", app_base_url="https://demo.example",
                jwt_secret="x" * 40, database_url="postgresql://u:p@db/app")
    Settings(**base)
    with pytest.raises(ValidationError):
        Settings(**{**base, "app_base_url": "http://demo.example"})
    with pytest.raises(ValidationError):
        Settings(**{**base, "jwt_secret": "short"})
    with pytest.raises(ValidationError):  # outside demo mode nothing is relaxed
        Settings(**{**base, "demo_mode": False})
    with pytest.raises(ValidationError):
        Settings(_env_file=None, demo_mode=False, jwt_secret="x" * 40, database_url="sqlite://", steam_api_key="")


def test_https_base_url_requires_production():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, steam_api_key="k", jwt_secret="x" * 40, database_url="sqlite://",
                 app_base_url="https://app.example", environment="development")


def test_non_demo_behaviour_is_unchanged(db):
    assert settings.demo_mode is False
    with TestClient(build(db), base_url="http://localhost:8000") as plain:
        assert plain.get("/auth/steam/login", follow_redirects=False).status_code == 307
        assert "demo" not in plain.get("/auth/config").json()
        assert plain.post("/demo/login", headers=ORIGIN).status_code == 404
        assert plain.post("/auth/logout").status_code == 401  # the real handler, not the guard


def test_memory_store_expires_and_rate_limits():
    store = MemoryStore()

    async def run():
        assert await store.set("a", "1", ex=100) and not await store.set("a", "2", nx=True)
        assert await store.getdel("a") == b"1" and await store.get("a") is None
        results = [await store.eval("", 1, "rate", 60, 2) for _ in range(3)]
        assert [r[2] for r in results] == [0, 0, 1]
    asyncio.run(run())


def test_unhandled_errors_still_carry_security_headers():
    app = FastAPI()

    @app.get("/boom")
    def boom():
        raise RuntimeError("secret detail")

    app.add_middleware(SecurityMiddleware)
    app.state.arq_pool = MemoryStore()
    with TestClient(app, base_url="http://localhost:8000", raise_server_exceptions=False) as plain:
        response = plain.get("/boom")
    assert response.status_code == 500 and "secret detail" not in response.text
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cache-control"] == "no-store"
    assert "default-src 'none'" in response.headers["content-security-policy"]


def test_the_real_app_serves_demo_login():
    assert "/demo/login" in real_app.openapi()["paths"]
    assert any(item.cls is DemoGuard for item in real_app.user_middleware)


def test_seed_creates_the_demo_player_and_refuses_a_second_run(db, monkeypatch):
    monkeypatch.setattr(seed_demo, "SessionLocal", sessionmaker(bind=db.get_bind()))
    assert seed_demo.main() == 0
    link = db.query(LinkedAccount).filter_by(platform_user_id=DEMO_STEAM_ID).one()
    assert db.get(User, link.user_id).display_name == "Demo Player"
    assert seed_demo.main() == 1


def _naive_utc(moment):
    return moment.replace(tzinfo=None) if moment.tzinfo is None else moment.astimezone(timezone.utc).replace(tzinfo=None)


def test_seed_fills_the_demo_with_varied_content(db, monkeypatch):
    monkeypatch.setattr(seed_demo, "SessionLocal", sessionmaker(bind=db.get_bind()))
    assert seed_demo.main() == 0
    assert db.query(Game).count() == 20
    assert db.query(Game).filter(Game.content_kind == "software").count() == 2
    assert db.query(User).count() == 9
    reviews = db.query(Review).all()
    assert 25 <= len(reviews) <= 40
    assert {review.rating for review in reviews} >= {2.5, 3.0, 4.0, 4.5, 5.0}
    assert all(review.rating * 2 == int(review.rating * 2) and 0.5 <= review.rating <= 5 for review in reviews)
    assert all(review.verified_source == "steam" for review in reviews)
    assert all(review.body and 1 <= review.body.count(".") + review.body.count("!") + review.body.count("?") <= 4
               for review in reviews)
    threads = {comment.review_id for comment in db.query(Comment)}
    assert len(threads) >= 5
    assert all(comment.user_id != db.get(Review, comment.review_id).user_id for comment in db.query(Comment))
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    assert all(now.toordinal() - _naive_utc(review.created_at).toordinal() <= 61 for review in reviews)
    assert all(_naive_utc(review.created_at) <= now for review in reviews)


def test_demo_player_has_a_library_and_three_reviews(db, monkeypatch):
    monkeypatch.setattr(seed_demo, "SessionLocal", sessionmaker(bind=db.get_bind()))
    assert seed_demo.main() == 0
    demo_id = db.query(LinkedAccount).filter_by(platform_user_id=DEMO_STEAM_ID).one().user_id
    assert db.query(Review).filter_by(user_id=demo_id).count() == 3
    latest = {}
    for snapshot in db.query(PlaytimeSnapshot).filter_by(user_id=demo_id).order_by(PlaytimeSnapshot.id):
        latest[snapshot.game_id] = snapshot.playtime_minutes
    assert len(latest) == 12
    assert sum(1 for minutes in latest.values() if minutes == 0) == 3
    assert len({minutes for minutes in latest.values() if minutes}) >= 6


def _seeded_reviews(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    monkeypatch.setattr(seed_demo, "SessionLocal", sessionmaker(bind=engine))
    assert seed_demo.main() == 0
    with sessionmaker(bind=engine)() as session:
        return [(r.user_id, r.game_id, r.rating, r.body) for r in session.query(Review).order_by(Review.id)]


def test_seed_is_deterministic_across_fresh_databases(monkeypatch):
    assert _seeded_reviews(monkeypatch) == _seeded_reviews(monkeypatch)
