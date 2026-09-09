"""Shared, expiring security state. Redis failures never grant access."""

import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

from fastapi import HTTPException, Request
import jwt
from redis.exceptions import RedisError

from app.config import settings
from app.models import utcnow

ISSUER = "playgraph"
AUDIENCE = "playgraph-api"
LOGIN_TTL = 600
NONCE_TTL = 1200
logger = logging.getLogger("playgraph.security")


def session_cookie_name() -> str:
    return "__Host-playgraph-session" if settings.app_base_url.startswith("https://") else "playgraph-session"


def csrf_token(session_id: str) -> str:
    """A token bound to this session, separate from its authentication credential."""
    return hmac.new(settings.jwt_secret.get_secret_value().encode(),
                    f"playgraph-csrf:{session_id}".encode(), hashlib.sha256).hexdigest()


def state_key(purpose: str, value: str) -> str:
    return f"playgraph:security:{purpose}:{hashlib.sha256(value.encode()).hexdigest()}"


def security_store(request: Request):
    store = getattr(request.app.state, "arq_pool", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Security service unavailable")
    return store


async def redis_call(request: Request, method: str, *args, **kwargs):
    try:
        return await getattr(security_store(request), method)(*args, **kwargs)
    except (RedisError, OSError):
        raise HTTPException(status_code=503, detail="Security service unavailable") from None


async def issue_session(request: Request, user_id: int) -> str:
    now = utcnow()
    session_id = secrets.token_urlsafe(32)
    seconds = settings.session_minutes * 60
    token = jwt.encode(
        {"sub": str(user_id), "iat": now, "nbf": now,
         "exp": now + timedelta(seconds=seconds), "jti": session_id,
         "iss": ISSUER, "aud": AUDIENCE},
        settings.jwt_secret.get_secret_value(), algorithm="HS256",
    )
    await redis_call(request, "set", state_key("session", session_id), str(user_id), ex=seconds)
    return token


# Increment and expiry must be atomic so interrupted requests cannot leave permanent counters.
RATE_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return {count, redis.call('TTL', KEYS[1])}
"""


async def rate_limit(request: Request, bucket: str, identity: str, limit: int, seconds: int):
    count, ttl = await redis_call(
        request, "eval", RATE_SCRIPT, 1, state_key(f"rate:{bucket}", identity), seconds,
    )
    if count > limit:
        logger.warning("rate_limit_exceeded bucket=%s", bucket)
        raise HTTPException(status_code=429, detail="Too many requests",
                            headers={"Retry-After": str(max(1, ttl))})
