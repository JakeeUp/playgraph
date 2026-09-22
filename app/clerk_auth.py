"""Clerk verification and live revocation checks. Never trust browser profile data.

JWT validation follows Clerk's manual verification guidance. Keys come only
from the configured instance's authenticated Backend API, never a token URL.
Public JWKS keys are cached for a minute. A confirmed-active session is
remembered for ACTIVE_SECONDS, so a signed-in page load costs no Backend API
calls on most requests; revocation on Clerk's side takes effect within that
window, and a PlayGraph sign-out takes effect at once.
"""
import asyncio
import re
import time

import httpx
import jwt
from fastapi import HTTPException

from app.config import settings
from app.security import redis_call, state_key

ACTIVE_SECONDS = 30

_keys = {}
_keys_until = 0.0
_keys_secret = None
_key_lock = asyncio.Lock()


def unavailable():
    return HTTPException(503, "Account verification is unavailable. Please try again.")


def rejected():
    return HTTPException(401, "Your PlayGraph sign-in has expired or was revoked. Sign in again.")


async def backend(path: str, method: str = "GET") -> dict:
    if not settings.clerk_enabled:
        raise unavailable()
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.request(method, "https://api.clerk.com/v1" + path,
                headers={"Authorization": "Bearer " + settings.clerk_secret_key.get_secret_value()})
            if response.status_code == 404:
                raise rejected()
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError()
            return result
    except (httpx.HTTPError, ValueError):
        raise unavailable() from None


async def signing_key(kid: str):
    global _keys, _keys_until, _keys_secret
    secret = settings.clerk_secret_key.get_secret_value()
    async with _key_lock:
        if time.monotonic() >= _keys_until or secret != _keys_secret:
            document = await backend("/jwks")
            try:
                _keys = {item["kid"]: jwt.PyJWK.from_dict(item, algorithm="RS256").key
                         for item in document["keys"] if item.get("kty") == "RSA"
                         and item.get("use", "sig") == "sig" and item.get("alg", "RS256") == "RS256"}
            except (KeyError, TypeError, ValueError, jwt.PyJWTError):
                raise unavailable() from None
            _keys_until = time.monotonic() + 60
            _keys_secret = secret
        # Unknown keys fail closed until the bounded key-cache refresh.
        if kid not in _keys:
            raise rejected()
        return _keys[kid]


async def verify_token(token: str) -> dict:
    if not settings.clerk_enabled:
        raise HTTPException(404, "PlayGraph account sign-in is not enabled")
    try:
        if not isinstance(token, str) or not 1 <= len(token) <= 8192:
            raise ValueError()
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if header.get("alg") != "RS256" or not isinstance(kid, str) or len(kid) > 200:
            raise ValueError()
        key = await signing_key(kid)
        claims = jwt.decode(token, key, algorithms=["RS256"], issuer=settings.clerk_origin,
            options={"require": ["iss", "sub", "sid", "azp", "iat", "nbf", "exp"], "verify_aud": True})
        # Default Clerk session JWTs have no audience. Unexpected audiences are
        # rejected by PyJWT; azp must exactly match this site's configured origin.
        if (claims["azp"] != settings.app_base_url or claims.get("sts", "active") != "active"
                or claims.get("act") or claims["exp"] - claims["iat"] > 120):
            raise ValueError()
        validate_ids(claims["sid"], claims["sub"])
        return claims
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        raise rejected() from None


def validate_ids(sid, subject):
    if (not isinstance(sid, str) or not re.fullmatch(r"sess_[A-Za-z0-9]{1,100}", sid)
            or not isinstance(subject, str) or not re.fullmatch(r"user_[A-Za-z0-9]{1,100}", subject)):
        raise rejected()


async def active_account(sid: str, subject: str) -> dict:
    validate_ids(sid, subject)
    session = await backend("/sessions/" + sid)
    now_ms = time.time() * 1000
    if (session.get("id") != sid or session.get("user_id") != subject
            or session.get("status") != "active" or session.get("actor")
            or session.get("tasks")
            or not isinstance(session.get("expire_at"), (int, float))
            or session["expire_at"] <= now_ms):
        raise rejected()
    account = await backend("/users/" + subject)
    if account.get("id") != subject or account.get("banned") or account.get("locked"):
        raise rejected()
    return account


async def revoke(sid: str, subject: str):
    validate_ids(sid, subject)
    await backend("/sessions/" + sid + "/revoke", "POST")


async def confirm_active(request, sid: str, subject: str) -> None:
    """The live revocation check, remembered for ACTIVE_SECONDS.

    Two Backend API calls on every signed-in request would slow each page and
    run into Clerk's rate limits. Redis trouble surfaces as 503 through
    redis_call, so a cache outage never skips the check.
    """
    validate_ids(sid, subject)
    key = state_key("provider-active", sid)
    if await redis_call(request, "get", key) in (subject, subject.encode()):
        return
    await active_account(sid, subject)
    await redis_call(request, "set", key, subject, ex=ACTIVE_SECONDS)


async def forget_active(request, sid: str) -> None:
    await redis_call(request, "delete", state_key("provider-active", sid))
