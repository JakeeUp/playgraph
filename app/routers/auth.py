"""Steam OpenID with browser binding and one-time assertions.

The same verified round trip serves two purposes: signing in with Steam, and
connecting Steam to a PlayGraph account that signed up without it.
"""

import hashlib
import logging
import re
import secrets
from datetime import datetime
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import get_current_user
from app.models import AuthIdentity, LinkedAccount, Platform, User, utcnow
from app import clerk_auth
from app.security import (LOGIN_TTL, NONCE_TTL, csrf_token, issue_session, rate_limit,
                          redis_call, session_cookie_name, state_key)
from app.services import steam

router = APIRouter(prefix="/auth", tags=["auth"])
STEAM_OPENID_URL = "https://steamcommunity.com/openid/login"
OPENID_NS = "http://specs.openid.net/auth/2.0"
SIGNED_FIELDS = {"op_endpoint", "claimed_id", "identity", "return_to", "response_nonce", "assoc_handle"}
logger = logging.getLogger("playgraph.security")


def cookie_name():
    return "__Host-playgraph-login" if settings.app_base_url.startswith("https://") else "playgraph-login"


CONNECT_CALLBACK = "/auth/steam/connect/callback"


async def begin_steam(request: Request, callback_path: str, mode: dict, binding_suffix: str) -> tuple[str, str]:
    """Store single-use state bound to this browser; return (Steam URL, browser secret).

    The binding suffix names whatever else the callback must match, such as the
    login mode or the session and user a Steam connection is for.
    """
    state = secrets.token_urlsafe(32)
    browser_secret = secrets.token_urlsafe(32)
    binding = hashlib.sha256(browser_secret.encode()).hexdigest() + binding_suffix
    await redis_call(request, "set", state_key("login", state), binding, ex=LOGIN_TTL)
    return_to = f"{settings.app_base_url}{callback_path}?{urlencode({'state': state, **mode})}"
    params = {
        "openid.ns": OPENID_NS,
        "openid.mode": "checkid_setup",
        "openid.return_to": return_to,
        "openid.realm": settings.app_base_url,
        "openid.identity": f"{OPENID_NS}/identifier_select",
        "openid.claimed_id": f"{OPENID_NS}/identifier_select",
    }
    return f"{STEAM_OPENID_URL}?{urlencode(params)}", browser_secret


def with_login_cookie(response: Response, browser_secret: str) -> Response:
    response.set_cookie(cookie_name(), browser_secret, max_age=LOGIN_TTL,
                        httponly=True, secure=settings.app_base_url.startswith("https://"),
                        samesite="lax", path="/")
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/steam/login")
async def steam_login(request: Request, ui: bool = False):
    url, browser_secret = await begin_steam(request, "/auth/steam/callback",
                                            {"ui": "1"} if ui else {}, "|ui" if ui else "")
    return with_login_cookie(RedirectResponse(url), browser_secret)


async def verified_steam_id(request: Request, callback_path: str, mode: dict, binding_suffix: str) -> str:
    """Every check a Steam callback needs before its SteamID can be trusted.

    The state must be single-use and bound to this browser plus binding_suffix,
    the assertion must name exactly this return URL, its nonce must be fresh and
    unused, and Steam itself must confirm the signature.
    """
    pairs = request.query_params.multi_items()
    params = dict(pairs)
    if len(pairs) != len(params) or len(request.url.query) > 8192:
        raise HTTPException(status_code=400, detail="Invalid Steam callback")
    state = params.get("state", "")
    browser_secret = request.cookies.get(cookie_name(), "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", state):
        raise HTTPException(status_code=401, detail="Steam login state is invalid or expired. Start again.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", browser_secret):
        raise HTTPException(
            status_code=401,
            detail=f"Steam login cookie is missing. Start at {settings.app_base_url}/app and finish in that same browser.",
        )
    expected_return = f"{settings.app_base_url}{callback_path}?{urlencode({'state': state, **mode})}"
    identity = params.get("openid.claimed_id", "")
    match = re.fullmatch(r"https?://steamcommunity\.com/openid/id/([0-9]{17})", identity)
    if (params.get("openid.ns") != OPENID_NS or params.get("openid.mode") != "id_res"
            or params.get("openid.op_endpoint") != STEAM_OPENID_URL
            or params.get("openid.return_to") != expected_return
            or params.get("openid.identity") != identity or match is None
            or not SIGNED_FIELDS.issubset(set(params.get("openid.signed", "").split(",")))
            or not params.get("openid.sig") or not params.get("openid.assoc_handle")):
        raise HTTPException(status_code=401, detail="Invalid Steam assertion")
    nonce = params.get("openid.response_nonce", "")
    try:
        if not 21 <= len(nonce) <= 255:
            raise ValueError("Invalid nonce")
        timestamp = datetime.strptime(nonce[:20], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=utcnow().tzinfo)
        age = (utcnow() - timestamp).total_seconds()
        if not -60 <= age <= LOGIN_TTL:
            raise ValueError("Expired nonce")
    except ValueError:
        raise HTTPException(status_code=401, detail="Steam assertion expired") from None
    key = state_key("login", state)
    expected_browser = (hashlib.sha256(browser_secret.encode()).hexdigest() + binding_suffix).encode()
    stored = await redis_call(request, "get", key)
    if isinstance(stored, str):
        stored = stored.encode()
    if stored is None:
        raise HTTPException(status_code=401, detail="Steam login state expired. Start the Steam login again.")
    if not secrets.compare_digest(stored, expected_browser):
        raise HTTPException(status_code=401, detail="Steam login belongs to a different browser session. Start the Steam login again.")
    consumed = await redis_call(request, "getdel", key)
    if consumed != stored:
        raise HTTPException(status_code=401, detail="Steam login was already used. Start the Steam login again.")
    verify_params = {k: v for k, v in params.items() if k.startswith("openid.")}
    verify_params["openid.mode"] = "check_authentication"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            result = await client.post(STEAM_OPENID_URL, data=verify_params)
            result.raise_for_status()
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Steam login service unavailable") from None
    verified = dict(line.split(":", 1) for line in result.text.splitlines() if ":" in line)
    if verified.get("is_valid") != "true":
        raise HTTPException(status_code=401, detail="Steam login verification failed")
    if not await redis_call(request, "set", state_key("nonce", nonce), "used", nx=True, ex=NONCE_TTL):
        raise HTTPException(status_code=401, detail="Steam assertion already used")
    return match.group(1)


@router.get("/steam/callback")
async def steam_callback(request: Request, db: Session = Depends(get_db)):
    ui = request.query_params.get("ui") == "1"
    if "ui" in request.query_params and not ui:
        raise HTTPException(status_code=400, detail="Invalid login mode")
    steam_id = await verified_steam_id(request, "/auth/steam/callback",
                                       {"ui": "1"} if ui else {}, "|ui" if ui else "")
    linked = db.query(LinkedAccount).filter_by(platform=Platform.steam, platform_user_id=steam_id).first()
    if linked:
        user = linked.user
    else:
        summary = await steam.get_player_summary(steam_id)
        user = User(display_name=(summary or {}).get("persona_name") or f"Player{steam_id[-6:]}")
        db.add(user)
        try:
            db.flush()
            db.add(LinkedAccount(user_id=user.id, platform=Platform.steam, platform_user_id=steam_id))
            db.commit()
        except IntegrityError:
            db.rollback()
            linked = db.query(LinkedAccount).filter_by(platform=Platform.steam, platform_user_id=steam_id).first()
            if linked is None:
                raise
            user = linked.user
    if db.query(AuthIdentity).filter_by(user_id=user.id).first():
        raise HTTPException(401, "Use PlayGraph account sign-in for this account")
    token = await issue_session(request, user.id)
    logger.info("login_succeeded user_id=%s", user.id)
    if ui:
        response = RedirectResponse("/app", status_code=303)
        response.set_cookie(session_cookie_name(), token, max_age=settings.session_minutes * 60,
                            httponly=True, secure=settings.app_base_url.startswith("https://"),
                            samesite="lax", path="/")
    else:
        response = JSONResponse({"access_token": token, "token_type": "bearer",  # nosec B105
                                 "expires_in": settings.session_minutes * 60, "user_id": user.id,
                                 "display_name": user.display_name, "steam_id": steam_id})
    response.delete_cookie(cookie_name(), path="/", secure=settings.app_base_url.startswith("https://"),
                           httponly=True, samesite="lax")
    response.headers["Cache-Control"] = "no-store"
    return response



def connect_binding(request: Request, user: User) -> str:
    # The callback lands on the account that asked, from the browser that asked.
    return f"|connect|{request.state.session_id}|{user.id}"


def connect_outcome(outcome: str, page: str = "/account") -> RedirectResponse:
    response = RedirectResponse(f"{page}?steam={outcome}", status_code=303)
    response.delete_cookie(cookie_name(), path="/", secure=settings.app_base_url.startswith("https://"),
                           httponly=True, samesite="lax")
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/steam/connect")
async def start_steam_connect(request: Request, user: User = Depends(get_current_user),
                              db: Session = Depends(get_db)):
    """Begin connecting Steam to a PlayGraph account that signed up without it.

    Needs a fresh sign-in from the same Clerk user as this session, and binds
    the Steam round trip to this browser, session and user, so nobody can
    finish it on someone else's account or with someone else's Steam.
    """
    if not settings.clerk_enabled:
        raise HTTPException(404, "PlayGraph account sign-in is not enabled")
    if "authorization" in request.headers or request.state.auth_provider != "clerk":
        raise HTTPException(403, "Sign in with your PlayGraph account to connect Steam")
    if db.query(LinkedAccount).filter_by(user_id=user.id, platform=Platform.steam).first():
        raise HTTPException(409, "Steam is already connected to this account")
    await rate_limit(request, "steam-connect", str(user.id), 5, LOGIN_TTL)
    claims = await clerk_auth.verify_token(request.headers.get("x-clerk-token", ""))
    if claims["sub"] != request.state.provider["subject"]:
        raise clerk_auth.rejected()
    profile = await clerk_auth.active_account(claims["sid"], claims["sub"])
    clerk_auth.require_recent_factors(claims, profile, LOGIN_TTL,
        "For your security, sign out and sign in again, including MFA if enabled, then connect Steam.")
    url, browser_secret = await begin_steam(request, CONNECT_CALLBACK, {}, connect_binding(request, user))
    return with_login_cookie(JSONResponse({"redirect": url}), browser_secret)


@router.get(CONNECT_CALLBACK.removeprefix("/auth"))
async def steam_connect_callback(request: Request, user: User = Depends(get_current_user),
                                 db: Session = Depends(get_db)):
    steam_id = await verified_steam_id(request, CONNECT_CALLBACK, {}, connect_binding(request, user))
    if db.query(LinkedAccount).filter_by(user_id=user.id, platform=Platform.steam).first():
        return connect_outcome("already")
    owner = db.query(LinkedAccount).filter_by(platform=Platform.steam, platform_user_id=steam_id).first()
    if owner is not None:
        # Proving you own this Steam account does not move it: its reviews and
        # verified hours stay with the PlayGraph account that already has it.
        return connect_outcome("in-use")
    try:
        db.add(LinkedAccount(user_id=user.id, platform=Platform.steam, platform_user_id=steam_id))
        db.commit()
    except IntegrityError:
        # Another tab connected first. The database allows one Steam account
        # per user and one user per Steam account, so the loser changes nothing.
        db.rollback()
        return connect_outcome("already" if db.query(LinkedAccount).filter_by(
            user_id=user.id, platform=Platform.steam).first() else "in-use")
    logger.info("steam_connected user_id=%s", user.id)
    return connect_outcome("connected", page="/app")


@router.get("/session")
async def browser_session(request: Request, user: User = Depends(get_current_user)):
    linked = next((account for account in user.linked_accounts if account.platform == Platform.steam), None)
    return {"user": {"id": user.id, "display_name": user.display_name},
            "auth_provider": request.state.auth_provider, "has_steam": linked is not None,
            "csrf_token": csrf_token(request.state.session_id),
            "expires_at": request.state.session_expires,
            "last_synced_at": linked.last_synced_at if linked else None}


@router.post("/logout", status_code=204)
async def logout(request: Request, user: User = Depends(get_current_user)):
    await redis_call(request, "delete", state_key("session", request.state.session_id))
    await redis_call(request, "delete", state_key("provider-session", request.state.session_id))
    provider_signed_out = True
    if request.state.auth_provider == "clerk":
        provider = request.state.provider
        # Forget the remembered liveness first, so any other PlayGraph session
        # built on this provider session stops at its next request.
        await clerk_auth.forget_active(request, provider["sid"])
        try:
            await clerk_auth.revoke(provider["sid"], provider["subject"])
        except HTTPException as exc:
            if exc.status_code != 401:
                provider_signed_out = False
    logger.info("logout user_id=%s", user.id)
    # Clear-Site-Data also drops cookies and storage the browser kept for this
    # site. "cache" is left out: API responses are never stored, and that
    # directive makes sign-out noticeably slow in some browsers. The local
    # session has ended either way, so both answers carry it.
    response = (Response(status_code=204) if provider_signed_out else
                JSONResponse({"provider_signed_out": False}))
    response.headers["Cache-Control"] = "no-store"
    response.headers["Clear-Site-Data"] = '"cookies", "storage"'
    response.delete_cookie(session_cookie_name(), path="/", httponly=True,
                           secure=settings.app_base_url.startswith("https://"), samesite="lax")
    return response
