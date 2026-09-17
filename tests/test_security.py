from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from redis.exceptions import ConnectionError as RedisConnectionError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import Settings, settings
from app.database import get_db
from app.middleware import MAX_BODY_BYTES, SecurityMiddleware
from app.models import Game, LinkedAccount, Platform, User, utcnow
from app.routers import auth, library, reviews
from app.security import LOGIN_TTL, state_key
from tests.security_helpers import MemoryRedis, auth_headers


@pytest.fixture
def client(db, monkeypatch):
    app = FastAPI()
    for router in (auth.router, library.router, reviews.router):
        app.include_router(router)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost"])
    app.add_middleware(SecurityMiddleware)
    app.state.arq_pool = MemoryRedis()
    app.dependency_overrides[get_db] = lambda: db
    db.add_all([User(id=1, display_name="One"), User(id=2, display_name="Two"),
                Game(id=1, steam_appid=10, name="Game")])
    db.flush()
    db.add(LinkedAccount(user_id=1, platform=Platform.steam, platform_user_id="76561198000000001"))
    db.commit()
    monkeypatch.setattr(auth.steam, "get_player_summary", AsyncMock(return_value={"persona_name": "New"}))
    with TestClient(app, base_url="http://localhost:8000") as value:
        yield value


@pytest.fixture
def verification(monkeypatch):
    stub = SimpleNamespace(text="ns:http://specs.openid.net/auth/2.0\nis_valid:true\n", status=200, calls=[])

    class SteamClient:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, data):
            assert url == auth.STEAM_OPENID_URL
            stub.calls.append(data)
            return httpx.Response(stub.status, text=stub.text, request=httpx.Request("POST", url))

    monkeypatch.setattr(auth.httpx, "AsyncClient", SteamClient)
    return stub


def begin(client, nonce=None, ui=False):
    response = client.get("/auth/steam/login", params={"ui": "1"} if ui else {}, follow_redirects=False)
    assert response.status_code == 307
    target = urlsplit(response.headers["location"])
    assert target.scheme == "https" and target.netloc == "steamcommunity.com"
    return_to = parse_qs(target.query)["openid.return_to"][0]
    state = parse_qs(urlsplit(return_to).query)["state"][0]
    return {"state": state, **({"ui": "1"} if ui else {}), "openid.ns": auth.OPENID_NS, "openid.mode": "id_res",
            "openid.op_endpoint": auth.STEAM_OPENID_URL, "openid.return_to": return_to,
            "openid.identity": "https://steamcommunity.com/openid/id/76561198000000001",
            "openid.claimed_id": "https://steamcommunity.com/openid/id/76561198000000001",
            "openid.signed": ",".join(sorted(auth.SIGNED_FIELDS)), "openid.sig": "signed-by-steam",
            "openid.assoc_handle": "association",
            "openid.response_nonce": nonce or utcnow().strftime("%Y-%m-%dT%H:%M:%SZ") + state}


def callback(client, params):
    return client.get("/auth/steam/callback", params=params)


def test_login_issues_revocable_short_session(client, verification, db):
    params = begin(client)
    assert client.cookies.get(auth.cookie_name())
    result = callback(client, params)
    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"
    assert client.cookies.get(auth.cookie_name()) is None
    token = result.json()["access_token"]
    claims = jwt.decode(token, settings.jwt_secret.get_secret_value(), algorithms=["HS256"],
                        audience="playgraph-api", issuer="playgraph")
    assert claims["exp"] - claims["iat"] == 1800
    headers = {"Authorization": "Bearer " + token}
    assert client.get("/me/genres", headers=headers).status_code == 200
    assert client.post("/auth/logout", headers=headers).status_code == 204
    assert client.get("/me/genres", headers=headers).status_code == 401
    assert db.query(User).count() == 2
    assert verification.calls[0]["openid.mode"] == "check_authentication"
    assert "state" not in verification.calls[0]


@pytest.mark.parametrize("field,value", [
    ("openid.mode", "cancel"), ("openid.ns", "wrong"),
    ("openid.op_endpoint", "https://attacker.example/openid"),
    ("openid.return_to", "https://attacker.example/callback"),
    ("openid.identity", "https://steamcommunity.com/openid/id/76561198000000002"),
    ("openid.claimed_id", "https://attacker.example/76561198000000001"),
    ("openid.signed", "claimed_id"), ("openid.sig", ""),
    ("openid.response_nonce", "invalid"), ("state", "wrong"),
])
def test_rejects_malformed_assertions_before_network(client, verification, field, value):
    params = begin(client)
    params[field] = value
    assert callback(client, params).status_code == 401
    assert not verification.calls


def test_login_cookie_binding_and_expiry(client, verification):
    params = begin(client)
    cookie = client.cookies.get(auth.cookie_name())
    client.cookies.clear()
    assert callback(client, params).status_code == 401
    client.cookies.set(auth.cookie_name(), "x" * 43)
    assert callback(client, params).status_code == 401
    client.cookies.set(auth.cookie_name(), cookie)
    client.app.state.arq_pool.now += LOGIN_TTL + 1
    assert callback(client, params).status_code == 401
    assert not verification.calls


def test_duplicate_callback_parameters_rejected(client, verification):
    params = begin(client)
    assert callback(client, list(params.items()) + [("openid.mode", "id_res")]).status_code == 400
    assert not verification.calls


@pytest.mark.parametrize("seconds", [-601, 61])
def test_expired_or_future_nonce_rejected(client, verification, seconds):
    nonce = (utcnow() + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ") + "nonce"
    assert callback(client, begin(client, nonce)).status_code == 401
    assert not verification.calls


def test_state_and_nonce_are_single_use(client, verification):
    params = begin(client)
    cookie = client.cookies.get(auth.cookie_name())
    assert callback(client, params).status_code == 200
    client.cookies.set(auth.cookie_name(), cookie)
    assert callback(client, params).status_code == 401
    second = begin(client, params["openid.response_nonce"])
    assert callback(client, second).status_code == 401


@pytest.mark.parametrize("reply", ["is_valid:false", "invalid_is_valid:true", "is_valid:truejunk"])
def test_verification_response_requires_exact_value(client, verification, reply):
    verification.text = reply
    assert callback(client, begin(client)).status_code == 401


def test_provider_failure_does_not_create_user(client, verification, db):
    verification.status = 503
    assert callback(client, begin(client)).status_code == 502
    assert db.query(User).count() == 2


def test_new_account_creation(client, verification, db):
    params = begin(client)
    params["openid.claimed_id"] = params["openid.identity"] = "https://steamcommunity.com/openid/id/76561198000000003"
    response = callback(client, params)
    assert response.status_code == 200
    assert response.json()["display_name"] == "New"
    assert db.query(LinkedAccount).count() == 2


@pytest.mark.parametrize("claim,value", [("aud", "another-api"), ("iss", "another-app"),
                                         ("sub", None), ("sub", "-1"), ("jti", "short"),
                                         ("exp", 1), ("nbf", 9999999999), ("iat", 9999999999)])
def test_invalid_claims_rejected(client, claim, value):
    headers = auth_headers(client.app.state.arq_pool, **{claim: value})
    assert client.get("/me/genres", headers=headers).status_code == 401


@pytest.mark.parametrize("claim", ["exp", "iat", "nbf", "sub", "jti", "aud", "iss"])
def test_required_claims(client, claim):
    headers = auth_headers(client.app.state.arq_pool)
    claims = jwt.decode(headers["Authorization"][7:], options={"verify_signature": False})
    del claims[claim]
    token = jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm="HS256")
    assert client.get("/me/genres", headers={"Authorization": "Bearer " + token}).status_code == 401


def test_wrong_signing_algorithm_and_signature(client):
    headers = auth_headers(client.app.state.arq_pool)
    claims = jwt.decode(headers["Authorization"][7:], options={"verify_signature": False})
    for key, algorithm in [("wrong-algorithm-key" * 4, "HS384"), ("wrong" * 10, "HS256")]:
        token = jwt.encode(claims, key, algorithm=algorithm)
        assert client.get("/me/genres", headers={"Authorization": "Bearer " + token}).status_code == 401


def test_session_owner_mismatch_and_expiration(client):
    headers = auth_headers(client.app.state.arq_pool)
    claims = jwt.decode(headers["Authorization"][7:], options={"verify_signature": False})
    store = client.app.state.arq_pool
    store.data[state_key("session", claims["jti"])] = b"2"
    assert client.get("/me/genres", headers=headers).status_code == 401
    headers = auth_headers(store)
    store.now += 1801
    assert client.get("/me/genres", headers=headers).status_code == 401


def test_sync_status_is_owner_only(client, monkeypatch):
    job = AsyncMock()
    job.status.return_value = SimpleNamespace(name="complete")
    job.result_info.return_value = SimpleNamespace(success=True, result={"games_synced": 2, "secret": "hidden"})
    factory = __import__('unittest.mock', fromlist=['Mock']).Mock(return_value=job)
    monkeypatch.setattr(library, "Job", factory)
    assert client.get("/me/sync/status/sync-json-user-1").status_code in (401, 403)
    headers = auth_headers(client.app.state.arq_pool, 2)
    assert client.get("/me/sync/status/sync-json-user-1", headers=headers).status_code == 404
    factory.assert_not_called()
    headers = auth_headers(client.app.state.arq_pool)
    response = client.get("/me/sync/status/sync-json-user-1", headers=headers)
    assert response.json()["result"] == {"games_synced": 2}
    job.result_info.return_value = SimpleNamespace(success=False, result="sensitive exception")
    response = client.get("/me/sync/status/sync-json-user-1", headers=headers)
    assert response.json()["status"] == "failed"
    assert "sensitive" not in response.text


def test_sync_hourly_budget(client):
    store = client.app.state.arq_pool
    store.enqueue_job = AsyncMock(return_value=SimpleNamespace(job_id="sync-json-user-1"))
    headers = auth_headers(store)
    for _ in range(3):
        assert client.post("/me/sync", headers=headers).status_code == 202
    assert client.post("/me/sync", headers=headers).status_code == 429
    assert store.enqueue_job.await_count == 3


def test_login_limit_cannot_be_bypassed_with_forwarded_for(client):
    for _ in range(10):
        assert client.get("/auth/steam/login", follow_redirects=False).status_code == 307
    response = client.get("/auth/steam/login", headers={"X-Forwarded-For": "8.8.8.8"})
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) > 0
    client.app.state.arq_pool.now += 61
    assert client.get("/auth/steam/login", follow_redirects=False).status_code == 307


def test_security_service_failure_is_closed(client):
    client.app.state.arq_pool.eval = AsyncMock(side_effect=RedisConnectionError("secret Redis URL"))
    response = client.get("/me/genres")
    assert response.status_code == 503
    assert "secret Redis" not in response.text


def test_security_headers_host_and_body_limit(client):
    response = client.get("/games/1/reviews")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert client.get("/games/1/reviews", headers={"Host": "evil.example"}).status_code == 400
    assert client.post("/games/1/reviews", content=b"x" * (MAX_BODY_BYTES + 1)).status_code == 413
    assert client.post("/games/1/reviews", content=iter([b"x" * 40000, b"x" * 40000])).status_code == 413
    assert client.post("/games/1/reviews", headers={"Content-Length": "invalid"}).status_code == 400


def test_production_transport_and_cookie(client, monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "app_base_url", "https://localhost")
    assert client.get("/games/1/reviews").status_code == 400
    response = client.get("https://localhost/auth/steam/login", follow_redirects=False)
    assert response.status_code == 307
    cookie = response.headers["set-cookie"]
    assert "__Host-playgraph-login=" in cookie
    assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert "Domain=" not in cookie
    assert "max-age" in response.headers["strict-transport-security"]


@pytest.mark.parametrize("values", [{"jwt_secret": "short"}, {"redis_url": "https://redis.example"},
    {"app_base_url": "http://public.example"}, {"app_base_url": "https://public.example/path"},
    {"environment": "production"},
    {"environment": "production", "app_base_url": "https://public.example", "redis_url": "redis://localhost"}])
def test_rejects_insecure_config(values):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


def test_secrets_are_masked_in_config():
    config = Settings(_env_file=None)
    assert config.jwt_secret.get_secret_value() not in repr(config)
    assert config.steam_api_key.get_secret_value() not in repr(config)


def browser_login(client):
    response = client.get("/auth/steam/callback", params=begin(client, ui=True), follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/app"
    assert "access_token" not in response.text
    cookie = response.headers["set-cookie"]
    assert "playgraph-session=" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert "Domain=" not in cookie
    session = client.get("/auth/session")
    assert session.status_code == 200
    assert session.json()["user"] == {"id": 1, "display_name": "One"}
    assert "access_token" not in session.text
    return session.json()["csrf_token"]


def test_review_edit_delete_enforce_browser_origin_csrf_and_revocation(client, verification):
    csrf = browser_login(client)
    headers = {"Origin": settings.app_base_url, "X-CSRF-Token": csrf}
    row = client.post("/games/1/reviews", json={"rating": 4, "body": "Original"}, headers=headers).json()
    target = f"/reviews/{row['id']}"
    for method in ("patch", "delete"):
        options = {"json": {"rating": 3, "body": "Changed"}} if method == "patch" else {}
        for invalid in ({}, {"Origin": "https://attacker.example", "X-CSRF-Token": csrf},
                        {"Origin": settings.app_base_url, "X-CSRF-Token": "bad"}):
            assert getattr(client, method)(target, headers=invalid, **options).status_code == 403
    assert client.get("/games/1/reviews").json() == [row]
    assert client.patch(target, json={"rating": 3, "body": "Changed"}, headers=headers).status_code == 200
    assert client.delete(target, headers=headers).status_code == 204
    cookie = client.cookies.get(auth.session_cookie_name())
    assert client.post("/auth/logout", headers=headers).status_code == 204
    client.cookies.set(auth.session_cookie_name(), cookie)
    assert client.patch(target, json={"rating": 5}, headers=headers).status_code == 401
    assert client.delete(target, headers=headers).status_code == 401


def test_browser_login_and_csrf_protected_review_and_logout(client, verification):
    csrf = browser_login(client)
    assert len(csrf) == 64
    for headers in [{}, {"Origin": settings.app_base_url}, {"X-CSRF-Token": csrf},
                    {"Origin": "https://attacker.example", "X-CSRF-Token": csrf},
                    {"Origin": "null", "X-CSRF-Token": csrf},
                    {"Origin": settings.app_base_url, "X-CSRF-Token": "x" * 64}]:
        assert client.post("/games/1/reviews", json={"rating": 4}, headers=headers).status_code == 403
    headers = {"Origin": settings.app_base_url, "X-CSRF-Token": csrf}
    review = client.post("/games/1/reviews", json={"rating": 4, "body": "A real review"}, headers=headers)
    assert review.status_code == 201
    assert review.json()["author_name"] == "One"
    assert client.post("/auth/logout").status_code == 403
    cookie = client.cookies.get(auth.session_cookie_name())
    assert client.post("/auth/logout", headers=headers).status_code == 204
    assert client.cookies.get(auth.session_cookie_name()) is None
    client.cookies.set(auth.session_cookie_name(), cookie)
    assert client.get("/auth/session").status_code == 401


@pytest.mark.parametrize("start_ui", [True, False])
def test_login_mode_cannot_be_switched_even_with_matching_return_to(client, verification, start_ui):
    params = begin(client, ui=start_ui)
    if start_ui:
        params.pop("ui")
        params["openid.return_to"] = params["openid.return_to"].replace("&ui=1", "")
    else:
        params["ui"] = "1"
        params["openid.return_to"] += "&ui=1"
    assert client.get("/auth/steam/callback", params=params, follow_redirects=False).status_code == 401
    assert not verification.calls


def test_cookie_session_does_not_override_bad_authorization(client, verification):
    browser_login(client)
    for header in ["Bearer bad", "Basic abc", "Bearer"]:
        assert client.get("/auth/session", headers={"Authorization": header}).status_code == 401


def test_csrf_from_another_session_is_rejected(client, verification):
    first_csrf = browser_login(client)
    second_csrf = browser_login(client)
    assert first_csrf != second_csrf
    response = client.post("/auth/logout", headers={"Origin": settings.app_base_url, "X-CSRF-Token": first_csrf})
    assert response.status_code == 403
    assert client.get("/auth/session").status_code == 200


def test_browser_session_expiry_and_owner_boundary(client, verification):
    browser_login(client)
    assert client.get("/me/library").status_code == 200
    assert client.get("/me/sync/status/sync-json-user-2").status_code == 404
    client.app.state.arq_pool.now += 1801
    assert client.get("/auth/session").status_code == 401


def test_browser_cookie_is_host_only_and_secure_under_https(client, verification, monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "app_base_url", "https://localhost")
    client.base_url = "https://localhost"
    response = client.get("/auth/steam/callback", params=begin(client, ui=True), follow_redirects=False)
    assert response.status_code == 303
    cookies = response.headers.get_list("set-cookie")
    session = next(cookie for cookie in cookies if cookie.startswith("__Host-playgraph-session="))
    assert "HttpOnly" in session and "Secure" in session and "Path=/" in session and "SameSite=lax" in session
    assert "Domain=" not in session
