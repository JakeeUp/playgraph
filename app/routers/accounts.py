"""Exchange a verified provider session for a short, revocable browser cookie."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import clerk_auth
from app.config import settings
from app.database import get_db
from app.deps import decode_session
from app.models import AuthIdentity, User
from app.security import issue_session, rate_limit, redis_call, session_cookie_name, state_key

router = APIRouter(prefix="/auth", tags=["accounts"])


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
            existing = db.query(AuthIdentity).filter_by(user_id=int(old["sub"]),
                provider="clerk", issuer=settings.clerk_origin, subject=claims["sub"]).first()
            if old.get("provider") != "clerk" or existing is None:
                raise HTTPException(409, "Sign out of your current account first. Account linking is not available yet.")
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
    response = Response(status_code=204)
    response.set_cookie(session_cookie_name(), token, max_age=settings.session_minutes * 60,
        httponly=True, secure=settings.app_base_url.startswith("https://"), samesite="lax", path="/")
    return response
