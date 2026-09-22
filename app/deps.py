import json
import secrets

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt
from jwt import InvalidTokenError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import AuthIdentity, User
from app import clerk_auth
from app.security import (AUDIENCE, ISSUER, csrf_token, rate_limit, redis_call,
                          session_cookie_name, state_key)

bearer_scheme = HTTPBearer(auto_error=False)


def decode_session(token: str) -> dict:
    try:
        if not token or len(token) > 2048:
            raise ValueError()
        payload = jwt.decode(token, settings.jwt_secret.get_secret_value(), algorithms=["HS256"],
            audience=AUDIENCE, issuer=ISSUER,
            options={"require": ["exp", "iat", "nbf", "sub", "jti", "aud", "iss"]})
        if (int(payload["sub"]) <= 0 or not isinstance(payload["jti"], str)
                or len(payload["jti"]) != 43
                or payload["exp"] - payload["iat"] > settings.session_minutes * 60):
            raise ValueError()
        return payload
    except (InvalidTokenError, KeyError, ValueError, TypeError, OverflowError):
        raise HTTPException(401, "Invalid or expired token") from None


async def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Verify a local session and its authentication source, then load its User.

    Any route that should only work for a logged-in user takes
    `user: User = Depends(get_current_user)` as a parameter.
    """
    # An explicit Authorization header must never silently fall back to cookies.
    cookie_auth = "authorization" not in request.headers
    token = request.cookies.get(session_cookie_name(), "") if cookie_auth else (creds.credentials if creds else "")
    payload = decode_session(token)
    user_id, session_id = int(payload["sub"]), payload["jti"]

    owner = await redis_call(request, "get", state_key("session", session_id))
    if owner not in (str(user_id), str(user_id).encode()):
        raise HTTPException(status_code=401, detail="Session expired or revoked")
    request.state.session_id = session_id
    request.state.session_expires = payload["exp"]
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        if cookie_auth:
            supplied = request.headers.get("x-csrf-token", "")
            if (request.headers.get("origin") != settings.app_base_url
                    or len(supplied) != 64
                    or not secrets.compare_digest(supplied.encode(), csrf_token(session_id).encode())):
                raise HTTPException(status_code=403, detail="Browser session check failed. Refresh and try again.")
        await rate_limit(request, "user-writes", str(user_id), 30, 60)
    else:
        # Private reads build whole per-account views (the full library, the
        # ranked feed), so each account gets its own budget as well as each
        # address. Normal use, sync polling included, stays well under it.
        await rate_limit(request, "user-reads", str(user_id), 90, 60)
    identity = db.query(AuthIdentity).filter_by(user_id=user_id).first()
    request.state.auth_provider = "steam"
    if payload.get("provider") == "clerk":
        try:
            provider = json.loads(await redis_call(request, "get", state_key("provider-session", session_id)))
            if (not settings.clerk_enabled or identity is None or identity.provider != "clerk"
                    or provider["issuer"] != settings.clerk_origin or identity.issuer != provider["issuer"]
                    or identity.subject != provider["subject"]):
                raise ValueError()
            clerk_auth.validate_ids(provider["sid"], provider["subject"])
        except (ValueError, TypeError, KeyError):
            raise clerk_auth.rejected() from None
        # Logout may clean up a revoked provider session, but still requires
        # the valid local session and all normal CSRF/Origin checks above.
        if request.url.path != "/auth/logout":
            await clerk_auth.confirm_active(request, provider["sid"], provider["subject"])
        request.state.auth_provider = "clerk"
        request.state.provider = provider
    elif payload.get("provider") is not None or identity is not None:
        raise HTTPException(401, "Use PlayGraph account sign-in for this account")
    return user
