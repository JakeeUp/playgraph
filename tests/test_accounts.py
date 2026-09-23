"""Signed synthetic Clerk tokens; no credentials or real provider requests."""
import base64
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import clerk_auth
from app.config import settings
from app.database import get_db
from app.main import app
from app.models import AuthIdentity, Game, PlaytimeSnapshot, Review, User
from app.security import state_key
from tests.security_helpers import MemoryRedis, auth_headers


@pytest.fixture
def accounts(db, monkeypatch):
    origin = "https://synthetic.clerk.accounts.dev"
    monkeypatch.setattr(settings, "clerk_enabled", True)
    monkeypatch.setattr(settings, "clerk_publishable_key", "pk_test_" + base64.b64encode(b"synthetic.clerk.accounts.dev$").decode())
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    jwk["kid"] = "synthetic"
    monkeypatch.setattr(clerk_auth, "_keys_until", 0)
    store = MemoryRedis()
    monkeypatch.setattr(app.state, "arq_pool", store, raising=False)
    monkeypatch.setitem(app.dependency_overrides, get_db, lambda: db)
    status = {"state": "active", "banned": False, "outage": False}
    async def backend(path, method="GET"):
        if status["outage"]:
            raise HTTPException(503, "Synthetic outage")
        if path == "/jwks":
            return {"keys": [jwk]}
        if path.endswith("/revoke"):
            status["state"] = "revoked"
            return {}
        if path.startswith("/sessions/"):
            sid = path.rsplit("/", 1)[1]
            return {"id": sid, "user_id": "user_" + sid.removeprefix("sess_"),
                    "status": status["state"], "expire_at": (time.time() + 3600) * 1000}
        if path.startswith("/users/"):
            return {"id": path.rsplit("/", 1)[1], "first_name": "Synthetic", "banned": status["banned"],
                    "two_factor_enabled": status.get("mfa", False)}
        raise AssertionError(path)
    monkeypatch.setattr(clerk_auth, "backend", backend)
    def token(subject="one", **claims):
        now = int(time.time())
        return jwt.encode({"iss": origin, "sub": "user_" + subject, "sid": "sess_" + subject,
            "azp": settings.app_base_url, "iat": now, "nbf": now, "exp": now + 60, **claims},
            private, algorithm="RS256", headers={"kid": "synthetic"})
    client = TestClient(app, base_url=settings.app_base_url)
    def login(subject="one", **claims):
        return client.post("/auth/clerk/session", headers={"Origin": settings.app_base_url,
            "X-PlayGraph-Auth": "1", "Authorization": "Bearer " + token(subject, **claims)})
    return SimpleNamespace(client=client, login=login, token=token, status=status, store=store, db=db)


def test_signup_mapping_isolation_and_logout(accounts):
    a = accounts
    a.db.add(User(id=1, display_name="Synthetic"))
    a.db.add(Game(id=1, steam_appid=10, name="Game")); a.db.flush()
    a.db.add(PlaytimeSnapshot(user_id=1, game_id=1, playtime_minutes=100)); a.db.commit()
    response = a.login()
    assert response.status_code == 204
    assert "HttpOnly" in response.headers["set-cookie"] and "SameSite=lax" in response.headers["set-cookie"]
    session = a.client.get("/auth/session").json()
    assert session["user"]["id"] != 1 and session["has_steam"] is False
    assert session["auth_provider"] == "clerk"
    assert a.client.get("/me/library").json() == []
    assert a.login("two").status_code == 409  # No implicit account switching.
    headers = {"Origin": settings.app_base_url, "X-CSRF-Token": session["csrf_token"]}
    assert a.client.post("/auth/logout", headers={}).status_code == 403
    assert a.client.post("/auth/logout", headers=headers).status_code == 204
    assert a.client.get("/auth/session").status_code == 401
    a.status["state"] = "active"
    assert a.login().status_code == 204
    assert a.db.query(AuthIdentity).count() == 1
    assert a.db.query(User).count() == 2


@pytest.mark.parametrize("claims", [
    {"exp": 1}, {"nbf": 9999999999}, {"iss": "https://other.clerk.accounts.dev"},
    {"azp": "https://evil.example"}, {"sts": "pending"}, {"act": {"sub": "actor"}},
    {"sub": "../../users"}, {"sid": "../sessions"}, {"aud": "another-app"},
    {"exp": int(time.time()) + 1000}, {"sid": "sess_somebodyelse"},
])
def test_bad_provider_claims_rejected_before_account_creation(accounts, claims):
    assert accounts.login(**claims).status_code == 401
    assert accounts.db.query(AuthIdentity).count() == 0


def test_origin_signature_and_missing_claim_rejections(accounts):
    a = accounts
    for origin in (None, "https://evil.example"):
        headers = {"X-PlayGraph-Auth": "1", "Authorization": "Bearer " + a.token()}
        if origin:
            headers["Origin"] = origin
        assert a.client.post("/auth/clerk/session", headers=headers).status_code == 403
    forged = jwt.encode({"sub": "user_one"}, "fake-key-with-enough-bytes-for-test", algorithm="HS256")
    for token in (forged, "broken", a.token()[:-10] + "xxxxxxxxxx"):
        assert a.client.post("/auth/clerk/session", headers={"Origin": settings.app_base_url,
            "X-PlayGraph-Auth": "1", "Authorization": "Bearer " + token}).status_code == 401
    assert a.db.query(User).count() == 0


@pytest.mark.parametrize("change,status", [("revoked", 401), ("pending", 401), ("banned", 401), ("outage", 503)])
def test_provider_status_rechecked_for_existing_local_cookie(accounts, change, status):
    a = accounts
    assert a.login().status_code == 204
    if change in ("banned", "outage"):
        a.status[change] = True
    else:
        a.status["state"] = change
    assert a.client.get("/me/library").status_code == status


def test_missing_provider_state_and_legacy_token_cannot_bypass_clerk(accounts):
    a = accounts
    assert a.login().status_code == 204
    session = a.client.get("/auth/session").json()
    legacy = auth_headers(a.store, session["user"]["id"])
    assert a.client.get("/me/library", headers=legacy).status_code == 401
    for key in list(a.store.data):
        if ":provider-session:" in key:
            del a.store.data[key]
    assert a.client.get("/me/library").status_code == 401


def test_logout_invalidates_local_cookie_during_provider_outage(accounts):
    a = accounts
    assert a.login().status_code == 204
    session = a.client.get("/auth/session").json()
    cookie = a.client.cookies.get("playgraph-session")
    a.status["outage"] = True
    response = a.client.post("/auth/logout", headers={"Origin": settings.app_base_url,
        "X-CSRF-Token": session["csrf_token"]})
    assert response.status_code == 200 and response.json()["provider_signed_out"] is False
    assert "Max-Age=0" in response.headers["set-cookie"]
    a.status["outage"] = False
    assert a.client.get("/me/library", headers={"Authorization": "Bearer " + cookie}).status_code == 401


def test_public_config_and_account_page_keep_secret_server_side(accounts):
    a = accounts
    config = a.client.get("/auth/config").json()
    assert set(config) == {"enabled", "publishable_key", "frontend_api"}
    page = a.client.get("/account")
    assert page.status_code == 200 and '/assets/account.js' in page.text
    csp = page.headers["content-security-policy"]
    assert settings.clerk_origin in csp and "unsafe-eval" not in csp
    assert "unsafe-inline" not in csp.split("script-src", 1)[1].split(";", 1)[0]
    assert "unsafe-inline" not in a.client.get("/app").headers["content-security-policy"]


def test_a_confirmed_provider_session_is_remembered_briefly(accounts, monkeypatch):
    a = accounts
    assert a.login().status_code == 204
    calls = []
    live = clerk_auth.backend

    async def counted(path, method="GET"):
        calls.append(path)
        return await live(path, method)
    monkeypatch.setattr(clerk_auth, "backend", counted)
    for _ in range(3):
        assert a.client.get("/me/library").status_code == 200
    # One live check, then remembered: a page load is not a dozen Backend API calls.
    assert sum(path.startswith("/sessions/") for path in calls) == 1
    a.status["state"] = "revoked"  # revoked on Clerk's side, not through PlayGraph
    assert a.client.get("/me/library").status_code == 200  # still inside the window
    a.store.now += clerk_auth.ACTIVE_SECONDS + 1
    assert a.client.get("/me/library").status_code == 401


def test_signing_out_ends_every_session_on_that_provider_session_at_once(accounts):
    a = accounts
    assert a.login().status_code == 204
    first = a.client.cookies.get("playgraph-session")
    a.client.cookies.clear()
    assert a.login().status_code == 204  # the same Clerk session, a second PlayGraph session
    bearer = lambda token: {"Authorization": "Bearer " + token}
    assert a.client.get("/me/library", headers=bearer(first)).status_code == 200  # now remembered
    session = a.client.get("/auth/session").json()
    assert a.client.post("/auth/logout", headers={"Origin": settings.app_base_url,
                                                  "X-CSRF-Token": session["csrf_token"]}).status_code == 204
    # No grace window after a PlayGraph sign-out: the remembered liveness is gone.
    assert a.client.get("/me/library", headers=bearer(first)).status_code == 401
