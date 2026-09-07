"""Steam login via OpenID 2.0.

Worth understanding why this looks different from a typical OAuth2 "Login
with X" flow: Steam never adopted OAuth for identity. "Sign in through
Steam" is OpenID 2.0 - an older, simpler protocol. There's no client
secret and no token exchange step; instead you redirect the user to Steam,
Steam redirects back with a set of signed `openid.*` query params, and you
verify that signature by POSTing the params back to Steam and checking it
says "is_valid".

Verification matters: without that round trip anyone could hand us a
handcrafted callback URL claiming any SteamID they liked. The signature
check is the only thing making this authentication rather than a suggestion.

If valid, `openid.claimed_id` contains the user's SteamID64.

This does NOT get us an API token for the Steam Web API - that's a separate
flat API key (STEAM_API_KEY) tied to this app, used with the SteamID we get
from login. Two different auth systems for two different purposes: login
(who is this) vs API access (fetch this person's data).
"""

from datetime import timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from jose import jwt
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import LinkedAccount, Platform, User, utcnow
from app.services import steam

router = APIRouter(prefix="/auth", tags=["auth"])

STEAM_OPENID_URL = "https://steamcommunity.com/openid/login"


@router.get("/steam/login")
def steam_login():
    """Redirect the user to Steam to sign in."""
    return_to = f"{settings.app_base_url}/auth/steam/callback"
    params = {
        "openid.ns": "http://specs.openid.net/auth/2.0",
        "openid.mode": "checkid_setup",
        "openid.return_to": return_to,
        "openid.realm": settings.app_base_url,
        "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
        "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return RedirectResponse(f"{STEAM_OPENID_URL}?{query}")


@router.get("/steam/callback")
async def steam_callback(request: Request, db: Session = Depends(get_db)):
    """Where Steam sends the user back after they sign in.

    The params arrive as query string values, so they are read off the
    Request directly. They cannot be declared as a typed body model the way
    a POST payload would be, since this is a GET redirect and there is no
    body to parse.
    """
    params = dict(request.query_params)
    if not params.get("openid.claimed_id"):
        raise HTTPException(status_code=400, detail="Not a valid Steam callback")

    # Hand the exact params back to Steam and ask whether it really signed
    # them. Only the mode changes; every other value must be echoed
    # untouched or the signature will not match.
    verify_params = dict(params)
    verify_params["openid.mode"] = "check_authentication"

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(STEAM_OPENID_URL, data=verify_params)

    if "is_valid:true" not in resp.text:
        raise HTTPException(status_code=401, detail="Steam login verification failed")

    # claimed_id looks like https://steamcommunity.com/openid/id/76561198...
    steam_id = params["openid.claimed_id"].rstrip("/").rsplit("/", 1)[-1]
    if not steam_id.isdigit():
        raise HTTPException(status_code=400, detail="Could not parse SteamID")

    linked = (
        db.query(LinkedAccount)
        .filter_by(platform=Platform.steam, platform_user_id=steam_id)
        .first()
    )

    if linked:
        user = linked.user
    else:
        summary = await steam.get_player_summary(steam_id)
        display_name = (summary or {}).get("persona_name") or f"Player{steam_id[-6:]}"
        user = User(display_name=display_name)
        db.add(user)
        db.flush()
        linked = LinkedAccount(
            user_id=user.id, platform=Platform.steam, platform_user_id=steam_id
        )
        db.add(linked)

    db.commit()

    token = jwt.encode(
        {"sub": str(user.id), "exp": utcnow() + timedelta(days=30)},
        settings.jwt_secret,
        algorithm="HS256",
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "user_id": user.id,
        "display_name": user.display_name,
        "steam_id": steam_id,
    }
