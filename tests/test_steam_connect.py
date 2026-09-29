"""Connecting Steam to a PlayGraph account that signed up without it."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.models import LinkedAccount, Platform, User, utcnow
from app.routers import auth
from tests.security_helpers import auth_headers
from tests.test_accounts import accounts  # noqa: F401  signed Clerk tokens and a fake Backend API
from tests.test_security import verification  # noqa: F401  Steam's signature check, faked

STEAM_A = "76561198000000011"
STEAM_B = "76561198000000022"


def assertion(return_to, steam_id):
    """What Steam sends back after a sign-in, for the state inside return_to."""
    state = parse_qs(urlsplit(return_to).query)["state"][0]
    claimed = f"https://steamcommunity.com/openid/id/{steam_id}"
    return {"state": state, "openid.ns": auth.OPENID_NS, "openid.mode": "id_res",
            "openid.op_endpoint": auth.STEAM_OPENID_URL, "openid.return_to": return_to,
            "openid.identity": claimed, "openid.claimed_id": claimed,
            "openid.signed": ",".join(sorted(auth.SIGNED_FIELDS)), "openid.sig": "signed-by-steam",
            "openid.assoc_handle": "association",
            "openid.response_nonce": utcnow().strftime("%Y-%m-%dT%H:%M:%SZ") + state}


def return_to(redirect):
    target = urlsplit(redirect)
    assert target.scheme == "https" and target.netloc == "steamcommunity.com"
    return parse_qs(target.query)["openid.return_to"][0]


@pytest.fixture
def fresh(accounts, verification):
    a = accounts
    assert a.login("fresh").status_code == 204
    a.user_id = a.client.get("/auth/session").json()["user"]["id"]

    def headers(**claims):
        csrf = a.client.get("/auth/session").json()["csrf_token"]
        return {"Origin": settings.app_base_url, "X-CSRF-Token": csrf,
                "X-Clerk-Token": a.token(claims.pop("subject", "fresh"), fva=claims.pop("fva", [0, -1]), **claims)}

    def finish(started, steam_id=STEAM_A):
        back = return_to(started.json()["redirect"])
        return a.client.get(urlsplit(back).path, params=assertion(back, steam_id), follow_redirects=False)

    a.headers = headers
    a.start = lambda **claims: a.client.post("/auth/steam/connect", headers=headers(**claims))
    a.finish = finish
    a.links = lambda: a.db.query(LinkedAccount).filter_by(user_id=a.user_id).all()
    return a


def test_a_new_account_connects_steam_syncs_and_still_signs_in_only_through_clerk(fresh):
    a = fresh
    started = a.start()
    assert started.status_code == 200 and started.headers["cache-control"] == "no-store"
    done = a.finish(started)
    assert done.status_code == 303 and done.headers["location"] == "/app?steam=connected"
    [link] = a.links()
    assert (link.platform, link.platform_user_id) == (Platform.steam, STEAM_A)
    assert a.client.get("/auth/session").json()["has_steam"] is True
    a.store.enqueue_job = AsyncMock(return_value=SimpleNamespace(job_id=f"sync-json-user-{a.user_id}"))
    assert a.client.post("/me/sync", headers=a.headers()).status_code == 202
    assert a.start().status_code == 409  # one Steam account per PlayGraph account
    # Steam is now for library sync. Signing in with it cannot step around Clerk.
    login = a.client.get("/auth/steam/login", follow_redirects=False)
    back = return_to(login.headers["location"])
    refused = a.client.get("/auth/steam/callback", params=assertion(back, STEAM_A))
    assert refused.status_code == 401 and "Use PlayGraph account sign-in" in refused.json()["detail"]


def test_connecting_needs_a_playgraph_session_csrf_and_fresh_proof_from_the_same_person(fresh):
    a = fresh
    no_csrf = {k: v for k, v in a.headers().items() if k != "X-CSRF-Token"}
    assert a.client.post("/auth/steam/connect", headers=no_csrf).status_code == 403
    assert a.start(fva=[30, -1]).status_code == 403  # signed in half an hour ago
    assert a.start(fva=[-1, -1]).status_code == 403
    a.status["mfa"] = True
    assert a.start(fva=[0, -1]).status_code == 403  # MFA enrolled but not just used
    assert a.start(fva=[0, 0]).status_code == 200
    a.status["mfa"] = False
    assert a.start(subject="someone").status_code == 401  # another Clerk user's proof
    a.db.add(User(id=50, display_name="Steam only")); a.db.commit()
    steam_only = {**auth_headers(a.store, 50), "X-Clerk-Token": a.token("fresh", fva=[0, -1])}
    assert a.client.post("/auth/steam/connect", headers=steam_only).status_code == 403
    assert a.links() == []


def test_a_steam_account_that_belongs_to_someone_else_is_never_moved(fresh):
    a = fresh
    a.db.add(User(id=50, display_name="Steam beta")); a.db.flush()
    a.db.add(LinkedAccount(user_id=50, platform=Platform.steam, platform_user_id=STEAM_B)); a.db.commit()
    assert a.finish(a.start(), STEAM_B).headers["location"] == "/account?steam=in-use"
    assert a.db.query(LinkedAccount).filter_by(platform_user_id=STEAM_B).one().user_id == 50
    assert a.links() == []


def test_the_steam_round_trip_only_lands_on_the_browser_and_session_that_started_it(fresh):
    a = fresh
    started = a.start()
    binding = a.client.cookies.get(auth.cookie_name())
    a.client.cookies.delete(auth.cookie_name())  # a different browser has no binding cookie
    assert a.finish(started).headers["location"] == "/account?steam=failed"
    a.client.cookies.set(auth.cookie_name(), binding)
    csrf = a.client.get("/auth/session").json()["csrf_token"]
    assert a.client.post("/auth/logout", headers={"Origin": settings.app_base_url, "X-CSRF-Token": csrf}).status_code == 204
    a.status["state"] = "active"
    assert a.login("fresh").status_code == 204  # same person, new session
    a.client.cookies.set(auth.cookie_name(), binding)
    assert a.finish(started).headers["location"] == "/account?steam=failed"
    assert a.links() == []
    a.client.cookies.delete(auth.cookie_name())  # drop the planted cookie; start() issues a fresh one
    again = a.start()
    assert a.finish(again).headers["location"] == "/app?steam=connected"
    assert a.finish(again).headers["location"] == "/account?steam=failed"  # no replays
    assert [link.platform_user_id for link in a.links()] == [STEAM_A]


def test_a_second_tab_that_finishes_later_changes_nothing(fresh):
    a = fresh
    started = a.start()
    a.db.add(LinkedAccount(user_id=a.user_id, platform=Platform.steam, platform_user_id=STEAM_A)); a.db.commit()
    assert a.finish(started, STEAM_B).headers["location"] == "/account?steam=already"
    assert [link.platform_user_id for link in a.links()] == [STEAM_A]


def test_the_database_allows_one_steam_account_per_user(db):
    db.add(User(id=1, display_name="One")); db.flush()
    db.add(LinkedAccount(user_id=1, platform=Platform.steam, platform_user_id=STEAM_A)); db.commit()
    db.add(LinkedAccount(user_id=1, platform=Platform.steam, platform_user_id=STEAM_B))
    with pytest.raises(IntegrityError):
        db.commit()
