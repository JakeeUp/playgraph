"""PlayStation linking routes against a fake PSN client. No test talks to Sony."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app import psn_status
from app.config import settings
from app.database import get_db
from app.main import app
from app.models import LinkedAccount, Platform, User
from app.routers import psn as psn_routes
from app.security import session_cookie_name, state_key
from app.services import psn
from tests.security_helpers import MemoryRedis, auth_headers

ACCOUNT_A = "1111111111111111111"
ACCOUNT_B = "2222222222222222222"


class FakePSN:
    """Stands in for PSNClient: the profiles and About Me texts a test sets."""

    def __init__(self):
        self.accounts = {"GoodPlayer": ACCOUNT_A, "OtherPlayer": ACCOUNT_B}
        self.about = {}
        self.error = None
        self.calls = []
        self.during_check = None

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def resolve_profile(self, online_id):
        online_id = psn.validate_online_id(online_id)
        self.calls.append(("resolve", online_id))
        if self.error:
            raise self.error
        for name, account in self.accounts.items():
            if name.lower() == online_id.lower():
                return {"account_id": account, "online_id": name}
        raise psn.PSNNotFoundError("PSN 404")

    async def check_verification(self, account_id, code):
        self.calls.append(("check", account_id))
        if self.during_check:
            await self.during_check()
        if self.error:
            raise self.error
        return psn.verification_matches(self.about.get(account_id), code)


@pytest.fixture
def env(db, monkeypatch):
    store = MemoryRedis()
    store.enqueue_job = AsyncMock(side_effect=lambda name, user_id, _job_id: SimpleNamespace(job_id=_job_id))
    monkeypatch.setattr(app.state, "arq_pool", store, raising=False)
    monkeypatch.setitem(app.dependency_overrides, get_db, lambda: db)
    monkeypatch.setattr(settings, "psn_npsso", SecretStr("fake-server-npsso"))
    fake = FakePSN()
    monkeypatch.setattr(psn_routes.psn, "PSNClient", fake)
    db.add_all([User(id=1, display_name="One"), User(id=2, display_name="Two")])
    db.commit()

    def browser(user_id=1):
        """A separate browser: its own cookie jar and session."""
        client = TestClient(app, base_url="http://localhost:8000")
        client.cookies.set(session_cookie_name(), auth_headers(store, user_id)["Authorization"][7:])
        csrf = client.get("/auth/session").json()["csrf_token"]
        client.unsafe = {"Origin": settings.app_base_url, "X-CSRF-Token": csrf}
        client.start = lambda online_id="GoodPlayer": client.post("/auth/psn/link", json={"online_id": online_id},
                                                                   headers=client.unsafe)
        client.check = lambda: client.post("/auth/psn/link/check", headers=client.unsafe)
        return client

    return SimpleNamespace(store=store, fake=fake, db=db, browser=browser)


def links(db):
    db.expire_all()
    return db.query(LinkedAccount).filter_by(platform=Platform.psn).all()


def test_disabled_playstation_answers_404_everywhere_even_before_sign_in(env, monkeypatch):
    monkeypatch.setattr(settings, "psn_npsso", SecretStr(""))
    anonymous = TestClient(app, base_url="http://localhost:8000")
    signed_in = env.browser()
    for client in (anonymous, signed_in):
        assert client.get("/auth/psn").status_code == 404
        assert client.post("/auth/psn/link", json={"online_id": "GoodPlayer"}).status_code == 404
        assert client.post("/auth/psn/link/check").status_code == 404
        assert client.delete("/auth/psn/link").status_code == 404
        assert client.post("/me/psn/sync").status_code == 404
    session = signed_in.get("/auth/session").json()
    assert session["psn_enabled"] is False and session["has_psn"] is False
    assert env.fake.calls == []


def test_code_in_about_me_links_the_account_once_and_queues_a_sync(env):
    browser = env.browser()
    assert browser.get("/auth/psn").json()["pending"] is None
    started = browser.start("goodplayer")
    assert started.status_code == 200
    body = started.json()
    assert body["online_id"] == "GoodPlayer" and body["expires_in"] == 900
    assert body["code"].startswith("PLAYGRAPH-")
    status = browser.get("/auth/psn").json()
    assert status["linked"] is False and status["pending"]["code"] == body["code"]

    env.fake.about[ACCOUNT_A] = f"Hi! {body['code']}"
    checked = browser.check()
    assert checked.status_code == 200, checked.text
    assert checked.json() == {"linked": True, "online_id": "GoodPlayer", "job_id": "sync-psn-user-1"}
    [link] = links(env.db)
    assert (link.user_id, link.platform_user_id, link.display_handle) == (1, ACCOUNT_A, "GoodPlayer")
    assert link.verified_at is not None and link.verification_method == "psn_about_me"
    env.store.enqueue_job.assert_awaited_once_with("sync_psn_library", 1, _job_id="sync-psn-user-1")

    status = browser.get("/auth/psn").json()
    assert status["linked"] and status["online_id"] == "GoodPlayer" and status["pending"] is None
    assert browser.get("/auth/session").json()["has_psn"] is True
    # Replaying the check, or starting again, cannot add a second link.
    assert browser.check().status_code == 409
    assert browser.start("OtherPlayer").status_code == 409
    assert len(links(env.db)) == 1


def test_a_wrong_or_missing_code_does_not_link_and_keeps_the_code_for_a_retry(env):
    browser = env.browser()
    code = browser.start().json()["code"]
    env.fake.about[ACCOUNT_A] = "PLAYGRAPH-AAAAAA"
    refused = browser.check()
    assert refused.status_code == 409 and "not found in your About Me" in refused.json()["detail"]
    env.fake.about[ACCOUNT_A] = code + "X"  # a longer token does not count
    assert browser.check().status_code == 409
    assert links(env.db) == []
    env.fake.about[ACCOUNT_A] = code.lower()
    assert browser.check().status_code == 200


def test_codes_expire_after_fifteen_minutes(env):
    browser = env.browser()
    code = browser.start().json()["code"]
    env.fake.about[ACCOUNT_A] = code
    env.store.now += 901
    expired = browser.check()
    assert expired.status_code == 409 and "Start again" in expired.json()["detail"]
    assert links(env.db) == []


def test_a_code_is_bound_to_the_browser_session_that_asked(env):
    first = env.browser(1)
    code = first.start().json()["code"]
    env.fake.about[ACCOUNT_A] = code
    # The same user in another browser, and another user, cannot finish it.
    second = env.browser(1)
    assert second.get("/auth/psn").json()["pending"] is None
    assert second.check().status_code == 409
    other = env.browser(2)
    assert other.check().status_code == 409
    assert links(env.db) == []
    # A value planted under this session for another user is ignored too.
    env.store.data[state_key("psn-link", "x" * 43)] = b'{"user_id": 1}'
    assert first.check().status_code == 200
    assert links(env.db)[0].user_id == 1


def test_a_code_consumed_while_sony_answered_cannot_link_twice(env):
    browser = env.browser()
    code = browser.start().json()["code"]
    env.fake.about[ACCOUNT_A] = code

    async def other_request_won():
        # Another request finished first and consumed the code.
        for key in list(env.store.data):
            if key.startswith("playgraph:security:psn-link:"):
                del env.store.data[key]

    env.fake.during_check = other_request_won
    lost = browser.check()
    assert lost.status_code == 409 and "already used" in lost.json()["detail"]
    assert links(env.db) == []


def test_a_session_revoked_during_the_check_cannot_link(env):
    browser = env.browser()
    code = browser.start().json()["code"]
    env.fake.about[ACCOUNT_A] = code

    async def sign_out_elsewhere():
        for key in list(env.store.data):
            if key.startswith("playgraph:security:session:"):
                del env.store.data[key]

    env.fake.during_check = sign_out_elsewhere
    assert browser.check().status_code == 401
    assert links(env.db) == []


def test_one_psn_account_per_user_and_one_user_per_psn_account(env):
    owner = env.browser(1)
    owner.start()
    env.fake.about[ACCOUNT_A] = owner.get("/auth/psn").json()["pending"]["code"]
    assert owner.check().status_code == 200
    rival = env.browser(2)
    taken = rival.start("GoodPlayer")
    assert taken.status_code == 409 and "already linked" in taken.json()["detail"]
    assert {link.user_id for link in links(env.db)} == {1}


def test_an_account_linked_elsewhere_between_start_and_check_is_refused(env):
    rival = env.browser(2)
    code = rival.start("GoodPlayer").json()["code"]
    env.db.add(LinkedAccount(user_id=1, platform=Platform.psn, platform_user_id=ACCOUNT_A))
    env.db.commit()
    env.fake.about[ACCOUNT_A] = code
    assert rival.check().status_code == 409
    assert [link.user_id for link in links(env.db)] == [1]


def test_writes_need_the_origin_and_csrf_token(env):
    browser = env.browser()
    assert browser.post("/auth/psn/link", json={"online_id": "GoodPlayer"}).status_code == 403
    assert browser.post("/auth/psn/link", json={"online_id": "GoodPlayer"},
                        headers={**browser.unsafe, "Origin": "https://evil.test"}).status_code == 403
    assert browser.post("/auth/psn/link/check", headers={"Origin": settings.app_base_url}).status_code == 403
    assert browser.delete("/auth/psn/link").status_code == 403
    assert env.fake.calls == []


def test_starting_is_rate_limited_per_user(env):
    browser = env.browser()
    for _ in range(5):
        assert browser.start().status_code == 200
    limited = browser.start()
    assert limited.status_code == 429 and "retry-after" in limited.headers
    assert len([c for c in env.fake.calls if c[0] == "resolve"]) == 5


def test_bad_online_ids_never_reach_sony(env):
    browser = env.browser()
    for bad in ("ab", "1abc", "a/../profile", "abc def", "abc%2F", "x" * 17, "abc\nx", "abc\u0000"):
        assert browser.start(bad).status_code == 422, bad
    assert env.fake.calls == []


def test_sony_errors_become_clear_responses_without_details(env):
    browser = env.browser()
    missing = browser.start("NobodyHere")
    assert missing.status_code == 404 and missing.json()["detail"] == "No PSN account has that Online ID."

    env.fake.error = psn.PSNError("PSN 500 (12345: internal detail)", status=500)
    upstream = browser.start()
    assert upstream.status_code == 502 and "internal detail" not in upstream.text

    env.fake.error = psn.PSNRateLimitedError("PSN 429", retry_after=psn.DEFAULT_RATE_LIMIT_BACKOFF)
    busy = browser.start()
    assert busy.status_code == 503 and busy.headers["retry-after"] == "601"

    env.fake.error = psn.PSNRateLimitedError("budget spent", retry_after=42.5)
    assert browser.start().headers["retry-after"] == "43"

    env.fake.error = None
    code = browser.start().json()["code"]
    env.fake.about[ACCOUNT_A] = code
    env.fake.error = psn.PSNPrivateError("PSN 403 (2240526: Not permitted)", status=403)
    private = browser.check()
    assert private.status_code == 409 and "privacy settings" in private.json()["detail"]
    assert "2240526" not in private.text


def test_a_rejected_server_token_is_a_generic_503_and_stops_further_sony_calls(env, caplog):
    browser = env.browser()
    env.fake.error = psn.PSNAuthError("PSN_NPSSO has expired or is incorrect; set a fresh one")
    failed = browser.start()
    assert failed.status_code == 503
    assert failed.json()["detail"] == "PlayStation is temporarily unavailable. Try again later."
    assert "NPSSO" not in failed.text and "fake-server-npsso" not in failed.text
    assert "psn_operator_action_needed" in caplog.text and "fake-server-npsso" not in caplog.text
    calls = len(env.fake.calls)
    env.fake.error = None
    assert browser.start().status_code == 503
    assert len(env.fake.calls) == calls  # the flag stops the next request before Sony
    # A new NPSSO has a new fingerprint, so setting one lifts the stop.
    settings.psn_npsso = SecretStr("a-fresh-npsso")
    assert browser.start().status_code == 200


def test_cancel_forgets_the_pending_code(env):
    browser = env.browser()
    browser.start()
    assert browser.delete("/auth/psn/link", headers=browser.unsafe).status_code == 204
    assert browser.get("/auth/psn").json()["pending"] is None
    assert browser.check().status_code == 409


def test_sync_route_mirrors_the_steam_job_pattern(env):
    browser = env.browser()
    assert browser.post("/me/psn/sync", headers=browser.unsafe).status_code == 400
    env.db.add(LinkedAccount(user_id=1, platform=Platform.psn, platform_user_id=ACCOUNT_A))
    env.db.commit()
    queued = browser.post("/me/psn/sync", headers=browser.unsafe)
    assert queued.status_code == 202 and queued.json() == {"job_id": "sync-psn-user-1", "status": "queued"}
    env.store.enqueue_job.side_effect = None
    env.store.enqueue_job.return_value = None
    assert browser.post("/me/psn/sync", headers=browser.unsafe).status_code == 409
    assert browser.get("/me/psn/sync/status/sync-psn-user-2").status_code == 404
    assert browser.get("/me/psn/sync/status/sync-json-user-1").status_code == 404


def test_status_reports_the_last_sync_outcome_for_the_linked_account_only(env):
    import asyncio

    browser = env.browser()
    env.db.add(LinkedAccount(user_id=1, platform=Platform.psn, platform_user_id=ACCOUNT_A, display_handle="GoodPlayer"))
    env.db.commit()
    asyncio.run(psn_status.write_status(env.store, 1, {"problem": None, "trophies_visible": True,
                                                       "playtime_visible": False}))
    status = browser.get("/auth/psn").json()
    assert status["sync"]["playtime_visible"] is False and status["job_id"] == "sync-psn-user-1"
    assert env.browser(2).get("/auth/psn").json()["sync"] is None


def test_link_routes_never_wait_long_for_the_sony_budget(env, monkeypatch):
    seen = []

    def client(*args, **kwargs):
        seen.append(kwargs.get("max_wait"))
        return env.fake

    monkeypatch.setattr(psn_routes.psn, "PSNClient", client)
    browser = env.browser()
    code = browser.start().json()["code"]
    env.fake.about[ACCOUNT_A] = code
    assert browser.check().status_code == 200
    assert seen == [psn_routes.WEB_MAX_WAIT] * 2 and psn_routes.WEB_MAX_WAIT <= 15
