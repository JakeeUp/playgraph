"""Invite-only beta: new users need an invite, existing users never get locked out."""
from app import beta
from app.config import settings
from app.models import AuthIdentity, LinkedAccount, User
from tests.test_accounts import accounts  # noqa: F401  (fixture)
from tests.test_security import begin, callback, client, verification  # noqa: F401  (fixtures)

NEW_STEAM = "76561198000000003"


def steam_login(client, steam_id):
    params = begin(client)
    params["openid.claimed_id"] = params["openid.identity"] = f"https://steamcommunity.com/openid/id/{steam_id}"
    return callback(client, params)


def test_uninvited_steam_id_cannot_create_a_user(client, verification, db, monkeypatch):
    monkeypatch.setattr(settings, "beta_invite_only", True)
    monkeypatch.setattr(settings, "beta_steam_ids", "76561198000000009")
    users = db.query(User).count()
    response = steam_login(client, NEW_STEAM)
    assert response.status_code == 403
    assert response.json()["detail"] == beta.NOT_INVITED
    assert db.query(User).count() == users
    assert db.query(LinkedAccount).filter_by(platform_user_id=NEW_STEAM).count() == 0


def test_invited_steam_id_can_join(client, verification, db, monkeypatch):
    monkeypatch.setattr(settings, "beta_invite_only", True)
    monkeypatch.setattr(settings, "beta_steam_ids", f" 76561198000000009 , {NEW_STEAM} ")
    assert steam_login(client, NEW_STEAM).status_code == 200


def test_existing_steam_user_is_never_locked_out(client, verification, monkeypatch):
    monkeypatch.setattr(settings, "beta_invite_only", True)
    monkeypatch.setattr(settings, "beta_steam_ids", "")
    # The fixture's default identity already has a linked account.
    assert callback(client, begin(client)).status_code == 200


def test_invite_list_ignored_when_beta_is_off(client, verification, monkeypatch):
    monkeypatch.setattr(settings, "beta_invite_only", False)
    monkeypatch.setattr(settings, "beta_steam_ids", "")
    assert steam_login(client, NEW_STEAM).status_code == 200


def clerk_profile(email, status="verified", primary="idn_1"):
    return {"primary_email_address_id": primary,
            "email_addresses": [{"id": "idn_1", "email_address": email, "verification": {"status": status}}]}


def test_clerk_email_must_be_verified_primary_and_listed(monkeypatch):
    monkeypatch.setattr(settings, "beta_invite_only", True)
    monkeypatch.setattr(settings, "beta_emails", "friend@example.com")
    assert beta.clerk_invited(clerk_profile("Friend@Example.com"))
    assert not beta.clerk_invited(clerk_profile("friend@example.com", status="unverified"))
    assert not beta.clerk_invited(clerk_profile("friend@example.com", primary="idn_2"))
    assert not beta.clerk_invited(clerk_profile("stranger@example.com"))
    assert not beta.clerk_invited({})
    assert not beta.clerk_invited({"primary_email_address_id": "idn_1", "email_addresses": ["junk"]})


def test_uninvited_clerk_signup_is_refused(accounts, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "beta_invite_only", True)
    monkeypatch.setattr(settings, "beta_emails", "friend@example.com")
    response = accounts.login()
    assert response.status_code == 403
    assert accounts.db.query(AuthIdentity).count() == 0
    assert accounts.db.query(User).count() == 0


def production(**values):
    from app.config import Settings
    return Settings(_env_file=None, environment="production", app_base_url="https://beta.example",
                    jwt_secret="x" * 40, database_url="sqlite://", steam_api_key="synthetic",
                    redis_url="rediss://:synthetic@cache.example:6380/0", **values)


def test_production_with_real_users_requires_invite_only():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match="BETA_INVITE_ONLY"):
        production()
    assert production(beta_invite_only=True).beta_invite_only


def test_uninvited_browser_login_gets_a_readable_message():
    import asyncio
    from fastapi import HTTPException
    from starlette.requests import Request
    from app.main import browser_login_error
    def outcome(error):
        request = Request({"type": "http", "method": "GET", "path": "/auth/steam/callback",
                           "query_string": b"ui=1", "headers": []})
        return asyncio.run(browser_login_error(request, error)).headers["location"]
    assert outcome(HTTPException(403, beta.NOT_INVITED)) == "/app?login_error=invite"
    assert outcome(HTTPException(403, "Something else")) == "/app?login_error=1"
    assert outcome(HTTPException(401, "Invalid")) == "/app?login_error=1"


def test_suspended_user_loses_sessions_and_cannot_sign_back_in(client, verification, monkeypatch):
    token = callback(client, begin(client)).json()["access_token"]
    headers = {"Authorization": "Bearer " + token}
    assert client.get("/me/genres", headers=headers).status_code == 200
    monkeypatch.setattr(settings, "suspended_user_ids", "1")
    assert client.get("/me/genres", headers=headers).status_code == 401
    response = callback(client, begin(client))
    assert response.status_code == 403
    assert response.json()["detail"] == beta.SUSPENDED


def test_suspending_one_user_leaves_others_alone(client, verification, monkeypatch):
    token = callback(client, begin(client)).json()["access_token"]
    monkeypatch.setattr(settings, "suspended_user_ids", "2, 3")
    assert client.get("/me/genres", headers={"Authorization": "Bearer " + token}).status_code == 200


def test_plain_redis_only_on_fly_private_network():
    import pytest
    from pydantic import ValidationError

    def boot(url):
        from app.config import Settings
        return Settings(_env_file=None, environment="production", app_base_url="https://beta.example",
                        jwt_secret="x" * 40, database_url="sqlite://", steam_api_key="synthetic",
                        beta_invite_only=True, redis_url=url)

    for url in ("redis://default:synthetic@fly-playgraph-beta-redis.upstash.io:6379",
                "redis://:synthetic@playgraph-redis.internal:6379", "redis://:synthetic@cache.flycast:6379",
                "rediss://:synthetic@anything.example:6380"):
        boot(url)
    for url in ("redis://default:synthetic@public-host.upstash.io:6379",     # public Upstash: needs TLS
                "redis://default:synthetic@fly-x.upstash.io.evil.example:6379",
                "redis://:synthetic@cache.example:6379",
                "redis://fly-playgraph-beta-redis.upstash.io:6379",            # no password
                "rediss://anything.example:6380"):
        with pytest.raises(ValidationError):
            boot(url)
