import secrets

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt
from jwt import InvalidTokenError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import User
from app.security import (AUDIENCE, ISSUER, csrf_token, rate_limit, redis_call,
                          session_cookie_name, state_key)

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Decode the JWT issued at /auth/steam/callback and load the User.

    Any route that should only work for a logged-in user takes
    `user: User = Depends(get_current_user)` as a parameter.
    """
    # An explicit Authorization header must never silently fall back to cookies.
    cookie_auth = "authorization" not in request.headers
    token = request.cookies.get(session_cookie_name(), "") if cookie_auth else (creds.credentials if creds else "")
    try:
        if not token or len(token) > 2048:
            raise ValueError("Token too long")
        payload = jwt.decode(
            token, settings.jwt_secret.get_secret_value(), algorithms=["HS256"],
            audience=AUDIENCE, issuer=ISSUER,
            options={"require": ["exp", "iat", "nbf", "sub", "jti", "aud", "iss"]},
        )
        user_id = int(payload["sub"])
        session_id = payload["jti"]
        if (user_id <= 0 or not isinstance(session_id, str) or len(session_id) != 43
                or payload["exp"] - payload["iat"] > settings.session_minutes * 60):
            raise ValueError("Invalid session")
    except (InvalidTokenError, KeyError, ValueError, TypeError, OverflowError):
        raise HTTPException(status_code=401, detail="Invalid or expired token")

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
    return user
