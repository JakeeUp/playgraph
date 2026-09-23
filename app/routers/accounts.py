"""Exchange a verified provider session for a short, revocable browser cookie."""
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import clerk_auth
from app.config import settings
from app.database import get_db
from app.deps import decode_session, get_current_user
from app.models import AuthIdentity, LinkedAccount, Platform, User
from app.security import issue_session, rate_limit, redis_call, session_cookie_name, state_key

router = APIRouter(prefix="/auth", tags=["accounts"])
LINK_SECONDS = 600


def browser_cookie(token: str) -> Response:
    response = Response(status_code=204)
    response.set_cookie(session_cookie_name(), token, max_age=settings.session_minutes * 60,
        httponly=True, secure=settings.app_base_url.startswith("https://"), samesite="lax", path="/")
    return response


def steam_owner(request: Request, user: User, db: Session, *, fresh=False):
    if not settings.clerk_enabled:
        raise HTTPException(404, "PlayGraph account sign-in is not enabled")
    if "authorization" in request.headers or request.state.auth_provider != "steam":
        raise HTTPException(403, "Start from your Steam beta browser session")
    if not db.query(LinkedAccount).filter_by(user_id=user.id, platform=Platform.steam).first():
        raise HTTPException(409, "This account has no Steam library connection")
    if fresh and time.time() - request.state.session_issued >= LINK_SECONDS:
        raise HTTPException(403, "Sign in with Steam again, then return here to connect your account")


@router.get("/clerk/link")
async def link_status(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    steam_owner(request, user, db)
    intent = await redis_call(request, "get", state_key("account-link", request.state.session_id))
    return {"pending": intent in (str(user.id), str(user.id).encode())
            and time.time() - request.state.session_issued < LINK_SECONDS}


@router.post("/clerk/link", status_code=204)
async def start_link(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    steam_owner(request, user, db, fresh=True)
    await rate_limit(request, "account-link", str(user.id), 5, LINK_SECONDS)
    # The HttpOnly session cookie binds this explicit intent to this browser;
    # a provider redirect never carries local credentials or a target user ID.
    await redis_call(request, "set", state_key("account-link", request.state.session_id), str(user.id), ex=LINK_SECONDS)
    return Response(status_code=204)


@router.delete("/clerk/link", status_code=204)
async def cancel_link(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    steam_owner(request, user, db)
    await redis_call(request, "delete", state_key("account-link", request.state.session_id))
    return Response(status_code=204)


@router.post("/clerk/link/complete", status_code=204)
async def complete_link(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    steam_owner(request, user, db, fresh=True)
    key = state_key("account-link", request.state.session_id)
    owner = (str(user.id), str(user.id).encode())
    if await redis_call(request, "get", key) not in owner:
        raise HTTPException(409, "Start a new account connection in this browser")
    # Keep the local cookie as the authentication source. A separate header
    # proves the destination identity without accepting a browser-supplied ID.
    claims = await clerk_auth.verify_token(request.headers.get("x-clerk-token", ""))
    profile = await clerk_auth.active_account(claims["sid"], claims["sub"])
    ages = claims.get("fva")
    elapsed = max(0, time.time() - claims["iat"])
    if (not isinstance(ages, list) or len(ages) != 2
            or any(type(age) is not int or age < -1 for age in ages)
            or type(profile.get("two_factor_enabled")) is not bool
            or ages[0] < 0
            or not 0 <= ages[0] * 60 + elapsed < LINK_SECONDS
            or (profile["two_factor_enabled"] and (ages[1] < 0 or not 0 <= ages[1] * 60 + elapsed < LINK_SECONDS))):
        raise HTTPException(403, "Sign out of the PlayGraph sign-in below and sign in again, including MFA if enabled")
    if db.query(AuthIdentity).filter_by(issuer=settings.clerk_origin, subject=claims["sub"]).first():
        raise HTTPException(409, "This sign-in already owns a PlayGraph account. Separate accounts cannot be merged here.")
    # External verification awaited I/O: recheck the initiating session and
    # freshness before consuming the intent. GETDEL admits only one completion.
    steam_owner(request, user, db, fresh=True)
    if await redis_call(request, "get", state_key("session", request.state.session_id)) not in owner:
        raise clerk_auth.rejected()
    if await redis_call(request, "getdel", key) not in owner:
        raise HTTPException(409, "Account connection expired or was already used")
    try:
        db.add(AuthIdentity(user_id=user.id, provider="clerk", issuer=settings.clerk_origin, subject=claims["sub"]))
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "An account was connected elsewhere. Refresh and sign in again.") from None
    # No user, review, snapshot or platform row changes owner. Existing Steam
    # tokens are rejected by get_current_user as soon as this identity exists.
    token = await issue_session(request, user.id,
        provider={"issuer": settings.clerk_origin, "subject": claims["sub"], "sid": claims["sid"]})
    await redis_call(request, "delete", state_key("session", request.state.session_id))
    return browser_cookie(token)


@router.get("/config")
def configuration():
    return {"enabled": settings.clerk_enabled,
            "publishable_key": settings.clerk_publishable_key if settings.clerk_enabled else None,
            "frontend_api": settings.clerk_origin if settings.clerk_enabled else None}


@router.post("/clerk/session", status_code=204)
async def exchange(request: Request, db: Session = Depends(get_db)):
    if not settings.clerk_enabled:
        raise HTTPException(404, "PlayGraph account sign-in is not enabled")
    if request.headers.get("origin") != settings.app_base_url or request.headers.get("x-playgraph-auth") != "1":
        raise HTTPException(403, "Start sign-in on PlayGraph in this browser")
    address = request.client.host if request.client else "unknown"
    await rate_limit(request, "account-login", address, 10, 60)
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        raise clerk_auth.rejected()
    claims = await clerk_auth.verify_token(authorization[7:])
    profile = await clerk_auth.active_account(claims["sid"], claims["sub"])
    old = None
    if cookie := request.cookies.get(session_cookie_name()):
        try:
            old = decode_session(cookie)
        except HTTPException:
            pass  # Expired local cookies may be replaced after full verification.
        if old and await redis_call(request, "get", state_key("session", old["jti"])):
            # Conversion invalidates all Steam-only sessions through the durable
            # identity mapping. Those stale cookies must not block provider login.
            converted = old.get("provider") is None and db.query(AuthIdentity).filter_by(user_id=int(old["sub"])).first() is not None
            existing = db.query(AuthIdentity).filter_by(user_id=int(old["sub"]),
                provider="clerk", issuer=settings.clerk_origin, subject=claims["sub"]).first()
            if not converted and (old.get("provider") != "clerk" or existing is None):
                raise HTTPException(409, "Sign out of your current account first, or connect your Steam account from the account page.")
    identity = db.query(AuthIdentity).filter_by(issuer=settings.clerk_origin, subject=claims["sub"]).first()
    if identity is None:
        # Names are presentation only; matching names/emails never merges users.
        name = profile.get("username") or profile.get("first_name") or "Player"
        user = User(display_name=str(name)[:80])
        try:
            db.add(user)
            db.flush()
            identity = AuthIdentity(user_id=user.id, provider="clerk", issuer=settings.clerk_origin, subject=claims["sub"])
            db.add(identity)
            db.commit()
        except IntegrityError:
            db.rollback()
            identity = db.query(AuthIdentity).filter_by(issuer=settings.clerk_origin, subject=claims["sub"]).first()
            if identity is None:
                raise
    if identity.provider != "clerk":
        raise clerk_auth.rejected()
    token = await issue_session(request, identity.user_id,
        provider={"issuer": settings.clerk_origin, "subject": claims["sub"], "sid": claims["sid"]})
    if old:
        await redis_call(request, "delete", state_key("session", old["jti"]))
        await redis_call(request, "delete", state_key("provider-session", old["jti"]))
    return browser_cookie(token)
