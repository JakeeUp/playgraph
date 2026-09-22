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


async def issue_session(request: Request, user_id: int, *, provider: dict | None = None) -> str:
    now = utcnow()
    session_id = secrets.token_urlsafe(32)
    seconds = settings.session_minutes * 60
    token = jwt.encode(
        {"sub": str(user_id), "iat": now, "nbf": now,
         "exp": now + timedelta(seconds=seconds), "jti": session_id,
         "iss": ISSUER, "aud": AUDIENCE, **({"provider": "clerk"} if provider else {})},
        settings.jwt_secret.get_secret_value(), algorithm="HS256",
    )
    if provider:
        import json
        await redis_call(request, "set", state_key("provider-session", session_id),
                         json.dumps(provider), ex=seconds)
    await redis_call(request, "set", state_key("session", session_id), str(user_id), ex=seconds)
    return token


# A sliding-window counter: the current fixed window, plus the previous one
# weighted by how much of it still overlaps now. A plain fixed window lets a
# caller spend a full budget at the end of one window and another at the start
# of the next, twice the stated rate back to back. Redis's own clock picks the
# window, so every app instance agrees on it. Blocked calls are not counted,
# so a client that backs off recovers on schedule. The script runs atomically,
# so an interrupted request can never leave a counter without its expiry.
RATE_SCRIPT = """
local window = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local now = tonumber(redis.call('TIME')[1])
local slot = math.floor(now / window)
local elapsed = now - slot * window
local current = KEYS[1] .. ':' .. slot
local previous = tonumber(redis.call('GET', KEYS[1] .. ':' .. (slot - 1)) or '0')
local carried = math.floor(previous * (window - elapsed) / window)
local count = tonumber(redis.call('GET', current) or '0')
if carried + count >= limit then
  return {0, window - elapsed, 1}
end
count = redis.call('INCR', current)
if count == 1 then redis.call('EXPIRE', current, window * 2) end
return {limit - carried - count, window - elapsed, 0}
"""


def rate_headers(limit: int, remaining: int, reset: int) -> dict[str, str]:
    """The RateLimit fields from the IETF draft, in the widely deployed draft 6 form."""
    return {"RateLimit-Limit": str(limit), "RateLimit-Remaining": str(max(0, remaining)),
            "RateLimit-Reset": str(max(1, reset))}


async def rate_limit(request: Request, bucket: str, identity: str, limit: int, seconds: int) -> tuple[int, int]:
    """Spend one unit of a bucket, or refuse with 429.

    Returns (remaining, seconds until the current window ends) so callers can
    report the budget. A Redis failure surfaces as 503 through redis_call, so
    an outage never lets a request through unlimited.
    """
    remaining, reset, blocked = await redis_call(
        request, "eval", RATE_SCRIPT, 1, state_key(f"rate:{bucket}", identity), seconds, limit,
    )
    if blocked:
        logger.warning("rate_limit_exceeded bucket=%s", bucket)
        raise HTTPException(status_code=429, detail="Too many requests",
                            headers={"Retry-After": str(max(1, reset)), **rate_headers(limit, 0, reset)})
    return remaining, reset
