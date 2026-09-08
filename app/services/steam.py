"""Thin wrapper around the two Steam Web API endpoints we need.

Kept separate from the worker/router logic so the actual HTTP-calling code
is unit-testable on its own (mock httpx, don't need a real Steam account to
test that these functions parse responses correctly).
"""

import httpx

from app.config import settings

STEAM_API_BASE = "https://api.steampowered.com"


async def get_owned_games(steam_id: str) -> list[dict]:
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
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, params=params, headers={"x-webapi-key": settings.steam_api_key.get_secret_value()})
        resp.raise_for_status()
    return resp.json().get("response", {}).get("games", [])


async def get_player_achievements(steam_id: str, appid: int) -> dict | None:
    """Returns {"unlocked": int, "total": int} or None if the game has no
    achievement schema (most Steam games don't have achievements at all -
    GetPlayerAchievements returns success=False with an error message in
    that case, which is a normal/expected response, not a real error).
    """
    url = f"{STEAM_API_BASE}/ISteamUserStats/GetPlayerAchievements/v1/"
    params = {"steamid": steam_id, "appid": appid}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, params=params, headers={"x-webapi-key": settings.steam_api_key.get_secret_value()})

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


async def get_app_genres(appid: int) -> list[str] | None:
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
    """
    url = f"{STORE_API_BASE}/appdetails"
    params = {"appids": appid, "filters": "genres"}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, params=params)

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
        resp = await client.get(url, params=params, headers={"x-webapi-key": settings.steam_api_key.get_secret_value()})

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
