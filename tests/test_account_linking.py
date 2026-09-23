"""Conversion uses two real verification paths against synthetic identities."""
import time

import pytest
from fastapi.testclient import TestClient
from redis.exceptions import RedisError
from sqlalchemy.exc import IntegrityError

from app import clerk_auth
from app.config import settings
from app.deps import decode_session
from app.main import app
from app.models import AuthIdentity, Comment, Game, LinkedAccount, Platform, PlaytimeSnapshot, Review, User
from app.security import session_cookie_name, state_key
from tests.security_helpers import auth_headers
from tests.test_accounts import accounts  # Reuse the signed-token/provider fixture.


@pytest.fixture
def steam(accounts):
    a = accounts
    a.db.add(User(id=1, display_name="Steam owner"))
    a.db.add(Game(id=1, steam_appid=10, name="Game"))
    a.db.flush()
    a.db.add(LinkedAccount(id=1, user_id=1, platform=Platform.steam, platform_user_id="76561198000000001"))
    a.db.add(PlaytimeSnapshot(id=1, user_id=1, game_id=1, playtime_minutes=123))
    a.db.add(Review(id=1, user_id=1, game_id=1, rating=4.5, body="Keep this", verified_playtime_minutes=60))
    a.db.flush()
    a.db.add(Comment(id=1, user_id=1, review_id=1, body="Keep this too"))
    a.db.commit()
    a.legacy = auth_headers(a.store)
    a.client.cookies.set(session_cookie_name(), a.legacy["Authorization"][7:])
    a.session = a.client.get("/auth/session").json()
    a.headers = {"Origin": settings.app_base_url, "X-CSRF-Token": a.session["csrf_token"]}
    a.complete = lambda **claims: a.client.post("/auth/clerk/link/complete",
        headers={**a.headers, "X-Clerk-Token": a.token(fva=claims.pop("fva", [0, -1]), **claims)})
    return a


def test_conversion_preserves_ownership_and_disables_every_legacy_session(steam):
    a = steam
    other_legacy = auth_headers(a.store)
    before = a.client.get("/me/library").json()
    assert a.client.get("/auth/clerk/link").json() == {"pending": False}
    assert a.client.post("/auth/clerk/link", headers=a.headers).status_code == 204
    assert a.client.get("/auth/clerk/link").json() == {"pending": True}
    assert a.complete().status_code == 204
    session = a.client.get("/auth/session").json()
    assert session["user"]["id"] == 1 and session["auth_provider"] == "clerk" and session["has_steam"]
    assert a.client.get("/me/library").json() == before
    assert a.db.query(User).count() == 1
    assert a.db.get(User, 1).display_name == "Steam owner"
    assert a.db.get(Review, 1).user_id == 1 and a.db.get(Review, 1).verified_playtime_minutes == 60
    assert a.db.get(Comment, 1).body == "Keep this too"
    assert a.db.get(LinkedAccount, 1).user_id == 1
    assert a.db.query(AuthIdentity).one().user_id == 1
    for legacy in (a.legacy, other_legacy):
        assert a.client.get("/me/library", headers=legacy).status_code == 401
    assert a.complete().status_code == 403  # Old CSRF cookie cannot replay the operation.
    a.client.cookies.clear()
    a.client.cookies.set(session_cookie_name(), other_legacy["Authorization"][7:])
    assert a.login().status_code == 204  # Another browser's now-invalid Steam cookie is replaceable.
    assert a.client.get("/auth/session").json()["user"]["id"] == 1


def test_intent_is_browser_bound_cancelable_and_expires(steam):
    a = steam
    assert a.complete().status_code == 409
    assert a.client.post("/auth/clerk/link", headers=a.headers).status_code == 204
    another = TestClient(app, base_url=settings.app_base_url)
    another.cookies.set(session_cookie_name(), auth_headers(a.store)["Authorization"][7:])
    other = another.get("/auth/session").json()
    assert another.get("/auth/clerk/link").json() == {"pending": False}
    assert another.post("/auth/clerk/link/complete", headers={"Origin": settings.app_base_url,
        "X-CSRF-Token": other["csrf_token"], "X-Clerk-Token": a.token(fva=[0, -1])}).status_code == 409
    assert a.client.delete("/auth/clerk/link", headers=a.headers).status_code == 204
    assert a.complete().status_code == 409
    assert a.client.post("/auth/clerk/link", headers=a.headers).status_code == 204
    a.store.now += 601
    assert a.complete().status_code == 409
    assert a.db.query(AuthIdentity).count() == 0


def test_link_requires_browser_csrf_origin_and_recent_steam(steam):
    a = steam
    for headers in ({}, {**a.headers, "Origin": "https://evil.example"}, a.legacy):
        assert a.client.post("/auth/clerk/link", headers=headers).status_code == 403
    # An otherwise valid but older Steam session cannot start the operation.
    a.client.cookies.clear()
    a.client.cookies.set(session_cookie_name(), auth_headers(a.store,
        iat=int(time.time()) - 601, exp=int(time.time()) + 60)["Authorization"][7:])
    session = a.client.get("/auth/session").json()
    assert a.client.post("/auth/clerk/link", headers={**a.headers, "X-CSRF-Token": session["csrf_token"]}).status_code == 403
    assert a.db.query(AuthIdentity).count() == 0


@pytest.mark.parametrize("ages,mfa", [
    (None, False), ([10, -1], False), ([-1, -1], False), ([0], False), ([False, -1], False),
    (["0", -1], False), ([0, -2], False), ([0, -1], True), ([0, 10], True),
    ([10, 0], True), ([0, 0], None),
])
def test_missing_stale_or_incomplete_factor_proof_cannot_link(steam, ages, mfa):
    a = steam
    a.status["mfa"] = mfa
    assert a.client.post("/auth/clerk/link", headers=a.headers).status_code == 204
    assert a.complete(fva=ages).status_code == 403
    assert a.db.query(AuthIdentity).count() == 0
    assert a.client.get("/me/library").status_code == 200


def test_recent_mfa_and_no_cached_provider_status(steam):
    a = steam
    a.status["mfa"] = True
    assert a.client.post("/auth/clerk/link", headers=a.headers).status_code == 204
    a.store.data[state_key("provider-active", "sess_one")] = b"user_one"
    a.status["state"] = "revoked"
    assert a.complete(fva=[0, 0]).status_code == 401
    a.status["state"] = "active"
    assert a.complete(fva=[0, 0]).status_code == 204


def test_existing_provider_account_is_never_merged(steam):
    a = steam
    a.db.add(User(id=2, display_name="Steam owner"))
    a.db.flush()
    a.db.add(AuthIdentity(user_id=2, provider="clerk", issuer=settings.clerk_origin, subject="user_one"))
    a.db.commit()
    assert a.client.post("/auth/clerk/link", headers=a.headers).status_code == 204
    assert a.complete().status_code == 409
    assert a.db.query(AuthIdentity).one().user_id == 2
    assert a.db.get(Review, 1).user_id == 1


def test_session_revoked_during_provider_verification_cannot_link(steam, monkeypatch):
    a = steam
    assert a.client.post("/auth/clerk/link", headers=a.headers).status_code == 204
    original = clerk_auth.active_account
    async def revoked_while_checking(sid, subject):
        profile = await original(sid, subject)
        jti = decode_session(a.legacy["Authorization"][7:])["jti"]
        await a.store.delete(state_key("session", jti))
        return profile
    monkeypatch.setattr(clerk_auth, "active_account", revoked_while_checking)
    assert a.complete().status_code == 401
    assert a.db.query(AuthIdentity).count() == 0


def test_link_storage_failure_fails_closed_and_duplicate_rolls_back(steam, monkeypatch):
    a = steam
    assert a.client.post("/auth/clerk/link", headers=a.headers).status_code == 204
    async def unavailable(*args):
        raise RedisError("synthetic")
    with monkeypatch.context() as patch:
        patch.setattr(a.store, "getdel", unavailable)
        assert a.complete().status_code == 503
    assert a.db.query(AuthIdentity).count() == 0
    def duplicate():
        raise IntegrityError("synthetic", {}, Exception("unique constraint"))
    monkeypatch.setattr(a.db, "commit", duplicate)
    assert a.complete().status_code == 409
    assert a.db.query(AuthIdentity).count() == 0
    assert a.db.get(Review, 1).body == "Keep this"
