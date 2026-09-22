"""Steam-only OpenID login with browser binding and one-time assertions."""

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
from app.security import (LOGIN_TTL, NONCE_TTL, csrf_token, issue_session,
                          redis_call, session_cookie_name, state_key)
from app.services import steam

router = APIRouter(prefix="/auth", tags=["auth"])
STEAM_OPENID_URL = "https://steamcommunity.com/openid/login"
OPENID_NS = "http://specs.openid.net/auth/2.0"
SIGNED_FIELDS = {"op_endpoint", "claimed_id", "identity", "return_to", "response_nonce", "assoc_handle"}
logger = logging.getLogger("playgraph.security")


def cookie_name():
    return "__Host-playgraph-login" if settings.app_base_url.startswith("https://") else "playgraph-login"


@router.get("/steam/login")
async def steam_login(request: Request, ui: bool = False):
    state = secrets.token_urlsafe(32)
    browser_secret = secrets.token_urlsafe(32)
    binding = hashlib.sha256(browser_secret.encode()).hexdigest() + ("|ui" if ui else "")
    await redis_call(request, "set", state_key("login", state), binding, ex=LOGIN_TTL)
    callback_query = {"state": state, **({"ui": "1"} if ui else {})}
    return_to = f"{settings.app_base_url}/auth/steam/callback?{urlencode(callback_query)}"
    params = {
        "openid.ns": OPENID_NS,
        "openid.mode": "checkid_setup",
        "openid.return_to": return_to,
        "openid.realm": settings.app_base_url,
        "openid.identity": f"{OPENID_NS}/identifier_select",
        "openid.claimed_id": f"{OPENID_NS}/identifier_select",
    }
    response = RedirectResponse(f"{STEAM_OPENID_URL}?{urlencode(params)}")
    response.set_cookie(cookie_name(), browser_secret, max_age=LOGIN_TTL,
                        httponly=True, secure=settings.app_base_url.startswith("https://"),
                        samesite="lax", path="/")
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/steam/callback")
async def steam_callback(request: Request, db: Session = Depends(get_db)):
    pairs = request.query_params.multi_items()
    params = dict(pairs)
    if len(pairs) != len(params) or len(request.url.query) > 8192:
        raise HTTPException(status_code=400, detail="Invalid Steam callback")
    state = params.get("state", "")
    ui = params.get("ui") == "1"
    if "ui" in params and not ui:
        raise HTTPException(status_code=400, detail="Invalid login mode")
    browser_secret = request.cookies.get(cookie_name(), "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", state):
        raise HTTPException(status_code=401, detail="Steam login state is invalid or expired. Start again.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", browser_secret):
        raise HTTPException(
            status_code=401,
            detail=f"Steam login cookie is missing. Start at {settings.app_base_url}/app and finish in that same browser.",
        )
    callback_query = {"state": state, **({"ui": "1"} if ui else {})}
    expected_return = f"{settings.app_base_url}/auth/steam/callback?{urlencode(callback_query)}"
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
    expected_browser = (hashlib.sha256(browser_secret.encode()).hexdigest() + ("|ui" if ui else "")).encode()
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
    steam_id = match.group(1)
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
