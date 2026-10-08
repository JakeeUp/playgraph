"""Thin wrapper around the two Steam Web API endpoints we need.

Kept separate from the worker/router logic so the actual HTTP-calling code
is unit-testable on its own (mock httpx, don't need a real Steam account to
test that these functions parse responses correctly).
"""

import asyncio
import time
from contextlib import asynccontextmanager

import httpx

from app.config import settings

STEAM_API_BASE = "https://api.steampowered.com"

# 429 is Steam saying slow down; the 5xx codes are Steam having a bad moment.
# Both are worth a short wait and another try. Anything else (400 for a game
# with no achievement schema, 401 for a bad key) will not change on retry.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 4
BACKOFF_SECONDS = 1.0
# A Retry-After longer than this is Steam asking us to stop for a while, not
# to pause. Failing the call beats wedging the sync for an hour.
MAX_RETRY_AFTER = 60.0


class SteamUnavailable(Exception):
    """A rate limit, 5xx or network error that outlasted every retry.

    Raised rather than returned as None, because None already means "this game
    has no data", and callers persist that. Persisting it for a transient
    failure would make the game look permanently empty.
    """


class RateLimiter:
    """Spaces request start times at least `interval` seconds apart.

    Each caller reserves the next free slot before sleeping, so any number of
    concurrent tasks share one pace instead of each keeping its own. There is
    no await between reading and writing the slot, so no lock is needed.
    """

    def __init__(self, interval: float):
        self.interval = interval
        self._next = 0.0

    async def wait(self) -> None:
        now = time.monotonic()
        slot = max(now, self._next)
        self._next = slot + self.interval
        if slot > now:
            await asyncio.sleep(slot - now)

    def defer(self, seconds: float) -> None:
        """Push every future slot back. A 429 is about our IP, not one request,
        so all waiting callers back off together rather than each walking into
        the same limit on its own."""
        self._next = max(self._next, time.monotonic() + seconds)


def make_client() -> httpx.AsyncClient:
    """One client for a whole sync, so hundreds of calls reuse a few kept-alive
    TLS connections instead of paying a fresh handshake each."""
    return httpx.AsyncClient(
        timeout=15, limits=httpx.Limits(max_connections=8, max_keepalive_connections=8)
    )


@asynccontextmanager
async def _client(client: httpx.AsyncClient | None):
    if client is not None:
        yield client
    else:
        async with make_client() as owned:
            yield owned


def _retry_delay(resp: httpx.Response | None, attempt: int) -> float:
    header = resp.headers.get("retry-after", "").strip() if resp is not None else ""
    if header.isdigit():
        return min(float(header), MAX_RETRY_AFTER)
    return BACKOFF_SECONDS * 2**attempt


async def _get(client, url, params, headers=None, limiter: RateLimiter | None = None) -> httpx.Response:
    """GET with pacing and bounded retries. Returns the first non-retryable
    response, or raises SteamUnavailable once the attempts run out."""
    for attempt in range(MAX_ATTEMPTS):
        if limiter is not None:
            await limiter.wait()
        resp = None
        try:
            resp = await client.get(url, params=params, headers=headers)
        except httpx.TransportError as exc:
            if attempt == MAX_ATTEMPTS - 1:
                raise SteamUnavailable(type(exc).__name__) from None
        else:
            if resp.status_code not in RETRY_STATUSES:
                return resp
            if attempt == MAX_ATTEMPTS - 1:
                raise SteamUnavailable(f"HTTP {resp.status_code}")
        delay = _retry_delay(resp, attempt)
        if limiter is not None:
            limiter.defer(delay)  # the next wait() sleeps it off, for every caller
        else:
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")


def _key_header() -> dict:
    return {"x-webapi-key": settings.steam_api_key.get_secret_value()}


async def get_owned_games(steam_id: str, client: httpx.AsyncClient | None = None) -> list[dict]:
    """Returns the raw list of owned-game dicts from Steam, each with at
    least appid, name, playtime_forever (all in minutes, despite some Steam
    docs calling it playtime_2weeks etc. - `playtime_forever` is total
    all-time minutes, which is the one we want).
    """
    url = f"{STEAM_API_BASE}/IPlayerService/GetOwnedGames/v1/"
    params = {
        "steamid": steam_id,
        "include_appinfo": 1,
        "include_played_free_games": 1,
        "format": "json",
    }
    async with _client(client) as c:
        resp = await _get(c, url, params, _key_header())
    resp.raise_for_status()
    return resp.json().get("response", {}).get("games", [])


async def get_player_achievements(
    steam_id: str, appid: int, client: httpx.AsyncClient | None = None,
    limiter: RateLimiter | None = None,
) -> dict | None:
    """Returns {"unlocked": int, "total": int} or None if the game has no
    achievement schema (most Steam games don't have achievements at all -
    GetPlayerAchievements returns success=False with an error message in
    that case, which is a normal/expected response, not a real error).
    Raises SteamUnavailable if Steam kept throttling or failing.
    """
    url = f"{STEAM_API_BASE}/ISteamUserStats/GetPlayerAchievements/v1/"
    params = {"steamid": steam_id, "appid": appid}
    async with _client(client) as c:
        resp = await _get(c, url, params, _key_header(), limiter)

    if resp.status_code != 200:
        # Steam returns 400 (not 200-with-success:false) for some no-schema
        # games too - treat as "no achievement data", not a fatal error.
        return None

    data = resp.json().get("playerstats", {})
    if not data.get("success"):
        return None

    achievements = data.get("achievements", [])
    if not achievements:
        return None

    unlocked = sum(1 for a in achievements if a.get("achieved") == 1)
    return {"unlocked": unlocked, "total": len(achievements)}


STORE_API_BASE = "https://store.steampowered.com/api"


async def get_app_genres(
    appid: int, client: httpx.AsyncClient | None = None, limiter: RateLimiter | None = None,
) -> list[str] | None:
    """Genre tags for one game, from the Steam STORE api.

    This is a different API from the Web API functions above: different host,
    different response shape, no API key, and much stricter rate limiting
    (community consensus is roughly 200 requests per 5 minutes per IP, and
    Valve documents none of it).

    We only ever call this once per game, because the result is written to
    Game.genres and reused from then on. So the cost lands on the first sync
    that sees a given game and is amortized to zero afterward, even across
    different users who own the same game.

    Returns None when the app has no usable store entry - delisted titles,
    tools, playtests, and region-locked apps all hit this. That is a normal
    outcome, not an error, so it is not raised.

    Being throttled is NOT that outcome. The caller stores None as "" (asked,
    nothing there) and never asks again, so a throttled answer raises
    SteamUnavailable instead, leaving the game to be looked up next sync. 403
    counts too: it is how the store turns away an IP that has asked too much.
    """
    url = f"{STORE_API_BASE}/appdetails"
    params = {"appids": appid, "filters": "genres"}
    async with _client(client) as c:
        resp = await _get(c, url, params, limiter=limiter)

    if resp.status_code == 403:
        raise SteamUnavailable("HTTP 403 from the store")
    if resp.status_code != 200:
        return None

    payload = resp.json().get(str(appid)) or {}
    if not payload.get("success"):
        return None

    genres = (payload.get("data") or {}).get("genres") or []
    names = [g.get("description") for g in genres if g.get("description")]
    return names or None


async def get_player_summary(steam_id: str) -> dict | None:
    """Public profile info: persona name and avatar.

    Used at login so a new account gets the user's actual Steam name instead
    of a placeholder. Returns None if the profile is private or the ID does
    not resolve, which callers should treat as "fall back to a placeholder"
    rather than an error - a private profile is a perfectly normal thing for
    someone to have.
    """
    url = f"{STEAM_API_BASE}/ISteamUser/GetPlayerSummaries/v2/"
    params = {"steamids": steam_id}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, params=params, headers=_key_header())

    if resp.status_code != 200:
        return None

    players = resp.json().get("response", {}).get("players", [])
    if not players:
        return None

    p = players[0]
    return {
        "persona_name": p.get("personaname"),
        "avatar": p.get("avatarfull"),
    }
