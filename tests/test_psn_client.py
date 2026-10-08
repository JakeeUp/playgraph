"""PSN client parsing, token handling and privacy behavior against a mocked
transport. No test here talks to Sony. Response bodies mirror the shapes in
PSNAWP's recorded integration cassettes."""
import asyncio
from datetime import datetime, timezone
from urllib.parse import parse_qs

import httpx
import pytest

from app.services import psn
from app.services.psn import (PSNAuthError, PSNClient, PSNError, PSNNotFoundError, PSNPrivateError,
                              PSNRateLimitedError, TokenState, generate_verification_code,
                              parse_duration_minutes, verification_matches)

ACCOUNT = "6515971742264256071"
FAKE_NPSSO = "fake-npsso-for-tests"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class FakeSony:
    """Answers the auth endpoints and routes API paths to canned handlers."""

    def __init__(self, expires_in=3600, refresh_expires_in=5_184_000):
        self.expires_in, self.refresh_expires_in = expires_in, refresh_expires_in
        self.calls: list[httpx.Request] = []
        self.routes: dict[str, object] = {}
        self.issued = 0
        self.authorize_location = None
        self.refresh_status = 200

    def kinds(self):
        out = []
        for r in self.calls:
            if r.url.path.endswith("/authorize"):
                out.append("authorize")
            elif r.url.path.endswith("/token"):
                out.append(parse_qs(r.content.decode())["grant_type"][0])
            else:
                out.append("api")
        return out

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path
        if path.endswith("/oauth/authorize"):
            location = self.authorize_location or f"{psn.REDIRECT_URI}/?code=v3.CODE&cid=abc"
            return httpx.Response(302, headers={"location": location})
        if path.endswith("/oauth/token"):
            form = parse_qs(request.content.decode())
            if form["grant_type"][0] == "refresh_token" and self.refresh_status != 200:
                return httpx.Response(self.refresh_status, json={"error": "invalid_grant"})
            self.issued += 1
            return httpx.Response(200, json={
                "access_token": f"access-{self.issued}", "token_type": "bearer",
                "expires_in": self.expires_in, "refresh_token": f"refresh-{self.issued}",
                "refresh_token_expires_in": self.refresh_expires_in, "scope": psn.SCOPE})
        handler = self.routes[path]
        return handler(request) if callable(handler) else handler


def make_client(sony, clock=None, **kwargs):
    state = TokenState(clock or Clock())
    http = httpx.AsyncClient(transport=httpx.MockTransport(sony))
    return PSNClient(FAKE_NPSSO, client=http, state=state, min_interval=0, **kwargs), state


def run(coro):
    return asyncio.run(coro)


# Durations ----------------------------------------------------------------

@pytest.mark.parametrize("value, minutes", [
    ("PT228H56M33S", 228 * 60 + 56),
    ("PT0S", 0),
    ("PT8M", 8),
    ("PT59S", 0),
    ("PT18H", 18 * 60),
    ("PT18H20S", 18 * 60),
    ("PT4H21M", 4 * 60 + 21),
    ("PT1000H", 60_000),  # hours are never rolled into days
    ("P1DT2H", 26 * 60),
    ("PT1M30.75S", 1),
    ("pt2h", 120),
])
def test_parse_duration_minutes(value, minutes):
    assert parse_duration_minutes(value) == minutes


@pytest.mark.parametrize("value", [None, "", "PT", "P", "garbage", "228:56:33", "PT-5M", 42])
def test_parse_duration_rejects_malformed_values_instead_of_returning_zero(value):
    assert parse_duration_minutes(value) is None


# Verification codes -------------------------------------------------------

def test_verification_codes_are_prefixed_unambiguous_and_unique():
    codes = {generate_verification_code() for _ in range(200)}
    assert len(codes) == 200
    for code in codes:
        assert code.startswith("PLAYGRAPH-") and len(code) == len("PLAYGRAPH-") + 6
        assert not set(code[len("PLAYGRAPH-"):]) & set("01IO")


def test_verification_match_is_case_insensitive_and_needs_the_whole_code():
    assert verification_matches("Hi! playgraph-abc234 is me", "PLAYGRAPH-ABC234")
    assert verification_matches("PLAYGRAPH-ABC234", " PLAYGRAPH-ABC234 ")
    assert verification_matches("line one\nPLAYGRAPH-ABC234.", "PLAYGRAPH-ABC234")
    assert not verification_matches("PLAYGRAPH-ABC2345", "PLAYGRAPH-ABC234")
    assert not verification_matches("PLAYGRAPH-ABC23", "PLAYGRAPH-ABC234")
    assert not verification_matches("", "PLAYGRAPH-ABC234")
    assert not verification_matches(None, "PLAYGRAPH-ABC234")
    assert not verification_matches("anything", "")


def test_check_verification_reads_about_me_for_the_account():
    sony = FakeSony()
    sony.routes[f"/api/userProfile/v1/internal/users/{ACCOUNT}/profiles"] = httpx.Response(200, json={
        "onlineId": "ikemenzi", "aboutMe": "PSN since 2013 playgraph-K7P2QX",
        "avatars": [{"size": "xl", "url": "https://example.test/xl.png"}], "isMe": False})
    client, _ = make_client(sony)
    assert run(client.check_verification(ACCOUNT, "PLAYGRAPH-K7P2QX"))
    assert not run(client.check_verification(ACCOUNT, "PLAYGRAPH-AAAAAA"))
    profile = run(client.get_profile(ACCOUNT))
    assert profile == {"online_id": "ikemenzi", "about_me": "PSN since 2013 playgraph-K7P2QX",
                       "avatar_url": "https://example.test/xl.png"}


# Tokens -----------------------------------------------------------------

def profile_route(sony):
    sony.routes[f"/api/userProfile/v1/internal/users/{ACCOUNT}/profiles"] = lambda request: httpx.Response(
        200, json={"onlineId": "x", "aboutMe": request.headers["authorization"]})


def test_npsso_exchange_sends_cookie_per_request_and_caches_the_access_token():
    sony, clock = FakeSony(), Clock()
    profile_route(sony)
    client, _ = make_client(sony, clock)

    assert run(client.get_about_me(ACCOUNT)) == "Bearer access-1"
    clock.now += 3000  # still inside the hour, outside nothing
    assert run(client.get_about_me(ACCOUNT)) == "Bearer access-1"
    assert sony.kinds() == ["authorize", "authorization_code", "api", "api"]

    authorize, token = sony.calls[0], sony.calls[1]
    assert authorize.headers["cookie"] == f"npsso={FAKE_NPSSO}"
    assert authorize.url.params["client_id"] == psn.CLIENT_ID
    assert authorize.url.params["redirect_uri"] == psn.REDIRECT_URI
    assert parse_qs(token.content.decode())["code"] == ["v3.CODE"]
    assert token.headers["authorization"] == psn.CLIENT_BASIC
    # The NPSSO never rides along on API calls through the shared client.
    assert "cookie" not in sony.calls[2].headers


def test_access_token_is_renewed_with_the_refresh_token_shortly_before_expiry():
    sony, clock = FakeSony(), Clock()
    profile_route(sony)
    client, _ = make_client(sony, clock)
    run(client.get_about_me(ACCOUNT))
    clock.now += 3600 - psn.TOKEN_SKEW_SECONDS + 1
    assert run(client.get_about_me(ACCOUNT)) == "Bearer access-2"
    assert sony.kinds() == ["authorize", "authorization_code", "api", "refresh_token", "api"]
    refresh = parse_qs(sony.calls[3].content.decode())
    assert refresh["refresh_token"] == ["refresh-1"] and refresh["scope"] == [psn.SCOPE]


def test_rejected_or_expired_refresh_token_falls_back_to_the_npsso():
    sony, clock = FakeSony(), Clock()
    profile_route(sony)
    client, _ = make_client(sony, clock)
    run(client.get_about_me(ACCOUNT))
    clock.now += 3600
    sony.refresh_status = 400
    assert run(client.get_about_me(ACCOUNT)) == "Bearer access-2"
    assert sony.kinds()[3:] == ["refresh_token", "authorize", "authorization_code", "api"]

    clock.now += 5_184_000  # refresh token itself has lapsed: skip straight to NPSSO
    sony.calls.clear()
    run(client.get_about_me(ACCOUNT))
    assert sony.kinds() == ["authorize", "authorization_code", "api"]


def test_token_cache_is_shared_between_clients_and_reset_when_npsso_changes():
    sony, clock = FakeSony(), Clock()
    profile_route(sony)
    state = TokenState(clock)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(sony)) as http:
            first = PSNClient(FAKE_NPSSO, client=http, state=state, min_interval=0)
            second = PSNClient(FAKE_NPSSO, client=http, state=state, min_interval=0)
            rotated = PSNClient("another-fake-npsso", client=http, state=state, min_interval=0)
            return [await first.get_about_me(ACCOUNT), await second.get_about_me(ACCOUNT),
                    await rotated.get_about_me(ACCOUNT)]

    assert run(scenario()) == ["Bearer access-1", "Bearer access-1", "Bearer access-2"]


def test_concurrent_calls_share_one_token_exchange():
    sony = FakeSony()
    profile_route(sony)
    client, _ = make_client(sony)

    async def scenario():
        return await asyncio.gather(*(client.get_about_me(ACCOUNT) for _ in range(5)))

    assert run(scenario()) == ["Bearer access-1"] * 5
    assert sony.kinds().count("authorization_code") == 1


def test_api_401_renews_once_then_reports_an_operator_auth_problem():
    sony = FakeSony()
    statuses = iter([401, 200])
    sony.routes[f"/api/userProfile/v1/internal/users/{ACCOUNT}/profiles"] = lambda request: httpx.Response(
        next(statuses), json={"aboutMe": "ok"})
    client, _ = make_client(sony)
    assert run(client.get_about_me(ACCOUNT)) == "ok"
    assert sony.kinds() == ["authorize", "authorization_code", "api", "refresh_token", "api"]

    sony.routes[f"/api/userProfile/v1/internal/users/{ACCOUNT}/profiles"] = httpx.Response(401, json={})
    with pytest.raises(PSNAuthError):
        run(client.get_about_me(ACCOUNT))


def test_expired_npsso_is_an_auth_error_that_never_echoes_the_token():
    sony = FakeSony()
    sony.authorize_location = f"{psn.REDIRECT_URI}/?error=login_required&error_code=4165&error_description=x"
    client, _ = make_client(sony)
    with pytest.raises(PSNAuthError) as caught:
        run(client.get_about_me(ACCOUNT))
    assert "expired" in str(caught.value) and FAKE_NPSSO not in str(caught.value)


def test_missing_npsso_is_an_auth_error_without_any_request():
    sony = FakeSony()
    client, _ = make_client(sony)
    client._npsso = ""
    with pytest.raises(PSNAuthError):
        run(client.get_about_me(ACCOUNT))
    assert sony.calls == []


def test_requests_are_paced_by_the_minimum_interval():
    sony, clock = FakeSony(), Clock()
    profile_route(sony)
    waits = []

    async def fake_sleep(seconds):
        waits.append(seconds)
        clock.now += seconds

    state = TokenState(clock)
    http = httpx.AsyncClient(transport=httpx.MockTransport(sony))
    client = PSNClient(FAKE_NPSSO, client=http, state=state, min_interval=3.0, sleep=fake_sleep)
    run(client.get_about_me(ACCOUNT))
    assert waits == [3.0, 3.0]  # authorize -> token -> api, each 3s apart


def paced_client(sony, clock, **kwargs):
    waits = []

    async def fake_sleep(seconds):
        waits.append(seconds)
        clock.now += seconds

    http = httpx.AsyncClient(transport=httpx.MockTransport(sony))
    client = PSNClient(FAKE_NPSSO, client=http, state=TokenState(clock), sleep=fake_sleep, **kwargs)
    return client, waits


def test_budget_bursts_at_the_min_gap_then_waits_for_the_window_edge():
    sony, clock = FakeSony(), Clock()
    profile_route(sony)
    client, waits = paced_client(sony, clock, min_interval=0.5, budget=5, window=60)

    async def scenario():
        for _ in range(5):
            await client.get_about_me(ACCOUNT)

    run(scenario())  # 2 auth requests + 5 API requests = 7 starts
    assert len(sony.calls) == 7
    # Starts 2-5 go out at the 0.5s gap; the 6th waits for the first start
    # (t=1000) to leave the 60s window; the 7th then waits for the second.
    assert waits[:4] == [0.5] * 4
    assert waits[4] == pytest.approx(60 - 2.0)
    assert waits[5] == pytest.approx(0.5)


def test_a_spent_budget_fails_fast_when_the_caller_cannot_wait():
    sony, clock = FakeSony(), Clock()
    profile_route(sony)
    client, waits = paced_client(sony, clock, min_interval=0, budget=3, window=60, max_wait=10)
    run(client.get_about_me(ACCOUNT))  # authorize, token, profile: the whole budget
    sent = len(sony.calls)
    with pytest.raises(PSNRateLimitedError) as caught:
        run(client.get_about_me(ACCOUNT))
    assert caught.value.retry_after == pytest.approx(60)
    assert len(sony.calls) == sent and waits == []  # nothing sent, nothing slept


def test_config_defaults_and_old_interval_setting_still_apply(monkeypatch):
    from app.config import Settings, settings
    fields = Settings.model_fields
    assert fields["psn_request_budget"].default == 200
    assert fields["psn_budget_window_seconds"].default == 900.0
    assert fields["psn_min_request_interval"].default == 0.5
    monkeypatch.setattr(settings, "psn_min_request_interval", 3.0)  # an owner's existing value
    client = PSNClient(FAKE_NPSSO, state=TokenState())
    assert client._min_interval == 3.0 and client._budget == settings.psn_request_budget


def test_short_429_is_slept_out_and_retried_once():
    sony, clock = FakeSony(), Clock()
    statuses = iter([429, 200])
    sony.routes[f"/api/userProfile/v1/internal/users/{ACCOUNT}/profiles"] = lambda request: httpx.Response(
        next(statuses), headers={"Retry-After": "7"}, json={"aboutMe": "ok"})
    client, waits = paced_client(sony, clock, min_interval=0, budget=1000, window=60)
    assert run(client.get_about_me(ACCOUNT)) == "ok"
    assert waits == [7.0]


def test_long_429_fails_fast_with_retry_after_and_pauses_later_requests():
    sony, clock = FakeSony(), Clock()
    sony.routes[f"/api/userProfile/v1/internal/users/{ACCOUNT}/profiles"] = httpx.Response(
        429, headers={"Retry-After": "120"}, json={})
    client, waits = paced_client(sony, clock, min_interval=0, budget=1000, window=60)
    with pytest.raises(PSNRateLimitedError) as caught:
        run(client.get_about_me(ACCOUNT))
    assert caught.value.retry_after == 120
    sent = len(sony.calls)
    with pytest.raises(PSNRateLimitedError):
        run(client.get_about_me(ACCOUNT))
    assert len(sony.calls) == sent and waits == []  # nothing sent during the pause
    clock.now += 121
    sony.routes[f"/api/userProfile/v1/internal/users/{ACCOUNT}/profiles"] = httpx.Response(200, json={"aboutMe": "x"})
    assert run(client.get_about_me(ACCOUNT)) == "x"


def test_429_on_token_exchange_is_rate_limited_not_an_auth_failure():
    sony = FakeSony()
    sony.authorize_location = None
    original = sony.__call__

    def throttled(request):
        if request.url.path.endswith("/oauth/token"):
            sony.calls.append(request)
            return httpx.Response(429, json={})
        return original(request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(throttled))
    client = PSNClient(FAKE_NPSSO, client=http, state=TokenState(Clock()), min_interval=0)
    with pytest.raises(PSNRateLimitedError):
        run(client.access_token())


def test_retry_after_parsing():
    now = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)
    assert psn.parse_retry_after("30") == 30
    assert psn.parse_retry_after("Wed, 07 Oct 2026 12:01:00 GMT", now) == 60
    assert psn.parse_retry_after("99999") == psn.MAX_RETRY_AFTER
    assert psn.parse_retry_after("soon") is None and psn.parse_retry_after(None) is None


def test_title_lookup_halves_the_batch_when_sony_rejects_its_size():
    sony = FakeSony()
    seen = []

    def lookup(request):
        ids = request.url.params["npTitleIds"].split(",")
        seen.append(len(ids))
        if len(ids) > 2:  # pretend Sony lowered its limit to 2
            return httpx.Response(400, json={"error": {"code": psn.ERR_BAD_TITLE_QUERY,
                                                       "message": "Bad Request (query: npTitleId)"}})
        return httpx.Response(200, json={"titles": [
            {"npTitleId": t, "trophyTitles": [{"npCommunicationId": "NPWR" + t[4:9] + "_00"}]} for t in ids]})

    sony.routes[f"/api/trophy/v1/users/{ACCOUNT}/titles/trophyTitles"] = lookup
    client, state = make_client(sony)
    titles = [f"CUSA{i:05d}_00" for i in range(1, 7)]
    found = run(client.get_trophy_lists_for_titles(ACCOUNT, titles))
    assert sorted(found) == titles
    assert seen == [5, 2, 2, 2]  # 5 rejected -> halved to 2 for the rest
    assert state.lookup_batch == 2


def test_title_lookup_does_not_halve_for_other_errors():
    sony = FakeSony()
    sony.routes[f"/api/trophy/v1/users/{ACCOUNT}/titles/trophyTitles"] = httpx.Response(503, text="down")
    client, state = make_client(sony)
    with pytest.raises(PSNError):
        run(client.get_trophy_lists_for_titles(ACCOUNT, ["CUSA00001_00", "CUSA00002_00"]))
    assert state.lookup_batch == psn.TITLE_LOOKUP_BATCH


# Lookups ------------------------------------------------------------------

def test_resolve_profile_uses_the_legacy_profile_lookup():
    sony = FakeSony()
    sony.routes["/userProfile/v1/users/ikemenzi/profile2"] = httpx.Response(
        200, json={"profile": {"onlineId": "ikemenzi", "accountId": ACCOUNT}})
    client, _ = make_client(sony)
    assert run(client.resolve_profile("ikemenzi")) == {"account_id": ACCOUNT, "online_id": "ikemenzi"}
    assert sony.calls[-1].url.params["fields"] == "accountId,onlineId,currentOnlineId"


def test_unknown_online_id_and_account_id_are_not_found():
    sony = FakeSony()
    sony.routes["/userProfile/v1/users/nobody_here/profile2"] = httpx.Response(
        404, json={"error": {"code": 2105356, "message": "User not found (user: 'nobody_here')"}})
    sony.routes["/api/userProfile/v1/internal/users/0000000000000000000/profiles"] = httpx.Response(
        400, json={"error": {"referenceId": "r", "code": 2281473, "message": "Bad Request (path: accountId)"}})
    client, _ = make_client(sony)
    with pytest.raises(PSNNotFoundError):
        run(client.resolve_profile("nobody_here"))
    with pytest.raises(PSNNotFoundError) as caught:
        run(client.get_about_me("0000000000000000000"))
    assert caught.value.code == 2281473


@pytest.mark.parametrize("bad", ["", "ab", "1starts_with_digit", "has space", "../../etc", "a" * 17])
def test_invalid_online_ids_are_rejected_before_any_request(bad):
    sony = FakeSony()
    client, _ = make_client(sony)
    with pytest.raises(ValueError):
        run(client.resolve_profile(bad))
    assert sony.calls == []


def test_invalid_account_ids_are_rejected_before_any_request():
    sony = FakeSony()
    client, _ = make_client(sony)
    for bad in ["me", "12/34", "", "1" * 21]:
        with pytest.raises(ValueError):
            run(client.get_trophy_titles(bad))
    assert sony.calls == []


# Trophy titles ------------------------------------------------------------

def trophy_title(i, platform="PS4", service="trophy"):
    return {"npServiceName": service, "npCommunicationId": f"NPWR{i:05d}_00", "trophySetVersion": "01.00",
            "trophyTitleName": f"Game {i}", "trophyTitleIconUrl": f"https://image.test/{i}.png",
            "trophyTitlePlatform": platform, "hasTrophyGroups": False,
            "definedTrophies": {"bronze": 9, "silver": 3, "gold": 1, "platinum": 0}, "progress": 23,
            "earnedTrophies": {"bronze": 5, "silver": 0, "gold": 0, "platinum": 0},
            "hiddenFlag": False, "lastUpdatedDateTime": "2026-03-05T04:21:57Z"}


def test_trophy_titles_follow_next_offset_and_normalize():
    sony = FakeSony()
    pages = {0: {"trophyTitles": [trophy_title(0, "PS4,PSVITA"), trophy_title(1, "PS5", "trophy2")],
                 "nextOffset": 2, "totalItemCount": 3},
             2: {"trophyTitles": [trophy_title(2)], "previousOffset": 1, "totalItemCount": 3}}
    sony.routes[f"/api/trophy/v1/users/{ACCOUNT}/trophyTitles"] = lambda request: httpx.Response(
        200, json=pages[int(request.url.params["offset"])])
    client, _ = make_client(sony)
    titles = run(client.get_trophy_titles(ACCOUNT))

    api = [r for r in sony.calls if "trophyTitles" in r.url.path]
    assert [(r.url.params["limit"], r.url.params["offset"]) for r in api] == [("800", "0"), ("800", "2")]
    assert [t["np_communication_id"] for t in titles] == ["NPWR00000_00", "NPWR00001_00", "NPWR00002_00"]
    assert titles[0] == {
        "np_communication_id": "NPWR00000_00", "np_service_name": "trophy", "name": "Game 0",
        "platforms": ["PS4", "PSVITA"], "icon_url": "https://image.test/0.png",
        "earned": {"bronze": 5, "silver": 0, "gold": 0, "platinum": 0},
        "defined": {"bronze": 9, "silver": 3, "gold": 1, "platinum": 0}, "progress": 23,
        "trophy_set_version": "01.00",
        "last_updated": datetime(2026, 3, 5, 4, 21, 57, tzinfo=timezone.utc)}
    assert titles[1]["np_service_name"] == "trophy2" and titles[1]["platforms"] == ["PS5"]


def test_private_trophy_list_raises_private():
    sony = FakeSony()
    sony.routes[f"/api/trophy/v1/users/{ACCOUNT}/trophyTitles"] = httpx.Response(403, json={
        "error": {"referenceId": "r", "code": 2240526, "message": "Not permitted by access control"}})
    client, _ = make_client(sony)
    with pytest.raises(PSNPrivateError) as caught:
        run(client.get_trophy_titles(ACCOUNT))
    assert caught.value.code == psn.ERR_ACCESS_CONTROL


def test_rate_limit_and_server_errors_are_distinct():
    sony = FakeSony()
    sony.routes[f"/api/trophy/v1/users/{ACCOUNT}/trophyTitles"] = httpx.Response(429, text="slow down")
    client, state = make_client(sony)
    with pytest.raises(PSNRateLimitedError):
        run(client.get_trophy_titles(ACCOUNT))
    state.blocked_until = 0.0  # let the 429 pause lapse
    sony.routes[f"/api/trophy/v1/users/{ACCOUNT}/trophyTitles"] = httpx.Response(503, text="down")
    with pytest.raises(PSNError) as caught:
        run(client.get_trophy_titles(ACCOUNT))
    assert type(caught.value) is PSNError and caught.value.status == 503


def test_runaway_pager_is_cut_off():
    sony = FakeSony()
    sony.routes[f"/api/trophy/v1/users/{ACCOUNT}/trophyTitles"] = lambda request: httpx.Response(
        200, json={"trophyTitles": [trophy_title(0)], "nextOffset": int(request.url.params["offset"]) + 1})
    client, _ = make_client(sony)
    with pytest.raises(PSNError):
        run(client.get_trophy_titles(ACCOUNT))


# Played titles -----------------------------------------------------------

def played(title_id, category, duration):
    return {"titleId": title_id, "name": f"Name {title_id}", "localizedName": "x",
            "imageUrl": f"https://image.test/{title_id}.png", "category": category,
            "service": "none(purchased)", "playCount": 5,
            "concept": {"id": 232352, "titleIds": [title_id, "CUSA00001_00"], "name": "Concept", "media": {}},
            "media": {}, "firstPlayedDateTime": "2022-03-29T13:21:34.000000Z",
            "lastPlayedDateTime": "2024-08-03T19:28:27.12Z", "playDuration": duration}


def test_played_titles_page_filter_and_parse_play_time():
    sony = FakeSony()
    pages = {0: {"titles": [played("PPSA04873_00", "ps5_native_game", "PT226H55M18S"),
                            played("PPSA00001_00", "ps5_native_media_app", "PT3H")],
                 "nextOffset": 2, "previousOffset": 0, "totalItemCount": 3},
             2: {"titles": [played("CUSA03105_00", "ps4_game", "PT0S")], "previousOffset": 1, "totalItemCount": 3}}
    sony.routes[f"/api/gamelist/v2/users/{ACCOUNT}/titles"] = lambda request: httpx.Response(
        200, json=pages[int(request.url.params["offset"])])
    client, _ = make_client(sony)
    titles = run(client.get_played_titles(ACCOUNT))

    first_call = next(r for r in sony.calls if "gamelist" in r.url.path)
    assert first_call.url.params["categories"] == "ps4_game,ps5_native_game"
    assert first_call.url.params["limit"] == "200"
    assert [t["title_id"] for t in titles] == ["PPSA04873_00", "CUSA03105_00"]  # media app dropped
    assert titles[0] == {
        "title_id": "PPSA04873_00", "concept_id": 232352, "concept_title_ids": ["PPSA04873_00", "CUSA00001_00"],
        "name": "Name PPSA04873_00", "image_url": "https://image.test/PPSA04873_00.png",
        "category": "ps5_native_game", "platform": "PS5", "play_minutes": 226 * 60 + 55, "play_count": 5,
        "first_played": datetime(2022, 3, 29, 13, 21, 34, tzinfo=timezone.utc),
        "last_played": datetime(2024, 8, 3, 19, 28, 27, 120000, tzinfo=timezone.utc)}
    assert titles[1]["platform"] == "PS4" and titles[1]["play_minutes"] == 0


def test_hidden_play_time_returns_none_instead_of_raising():
    sony = FakeSony()
    sony.routes[f"/api/gamelist/v2/users/{ACCOUNT}/titles"] = httpx.Response(403, json={
        "error": {"referenceId": "r", "code": 2240526, "message": "Not permitted by access control"}})
    client, _ = make_client(sony)
    assert run(client.get_played_titles(ACCOUNT)) is None


def test_played_titles_still_raise_for_not_found_and_auth():
    sony = FakeSony()
    sony.routes[f"/api/gamelist/v2/users/{ACCOUNT}/titles"] = httpx.Response(404, json={})
    client, _ = make_client(sony)
    with pytest.raises(PSNNotFoundError):
        run(client.get_played_titles(ACCOUNT))


def test_owned_http_client_is_closed_and_shared_one_is_not():
    async def scenario():
        shared = httpx.AsyncClient(transport=httpx.MockTransport(FakeSony()))
        async with PSNClient(FAKE_NPSSO, client=shared, state=TokenState(), min_interval=0):
            pass
        assert not shared.is_closed
        await shared.aclose()
        owned = PSNClient(FAKE_NPSSO, state=TokenState(), min_interval=0)
        http = owned.http
        await owned.aclose()
        assert http.is_closed

    run(scenario())
