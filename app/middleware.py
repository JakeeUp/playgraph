"""Bound request work before parsing and keep private responses out of caches."""

import asyncio
import logging

from fastapi import HTTPException, Request
from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse

from app.config import settings
from app.security import rate_limit

MAX_BODY_BYTES = 65536
logger = logging.getLogger("playgraph.security")


class SecurityMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)

        async def secure_send(message):
            if message["type"] == "http.response.start":
                if request.url.path.startswith("/auth/") and message["status"] >= 400:
                    logger.warning("authentication_rejected status=%s", message["status"])
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "no-referrer"
                headers["Cache-Control"] = "no-store"
                headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
                if settings.environment == "production":
                    headers["Strict-Transport-Security"] = "max-age=31536000"
                if request.url.path == "/app":
                    headers["Content-Security-Policy"] = (
                        "default-src 'none'; script-src 'self'; style-src 'self'; "
                        "connect-src 'self'; img-src 'self' https://shared.fastly.steamstatic.com "
                        "https://shared.akamai.steamstatic.com https://cdn.akamai.steamstatic.com "
                        "https://cdn.cloudflare.steamstatic.com; "
                        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
                    )
                elif request.url.path not in {"/docs", "/docs/oauth2-redirect", "/redoc"}:
                    headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
            await send(message)

        try:
            if settings.environment == "production" and request.url.scheme != "https":
                raise HTTPException(400, "HTTPS required")
            if len(scope.get("query_string", b"")) > 8192:
                raise HTTPException(414, "Query string too long")
            length = request.headers.get("content-length")
            if length is not None:
                if not length.isascii() or not length.isdecimal():
                    raise HTTPException(400, "Invalid content length")
                if len(length) > 10 or int(length) > MAX_BODY_BYTES:
                    raise HTTPException(413, "Request body too large")
            if request.url.path != "/health":
                # Proxy headers are only meaningful when the ASGI server trusts the proxy.
                # Never parse X-Forwarded-For here: a direct client can forge it.
                address = request.client.host if request.client else "unknown"
                await rate_limit(request, "requests", address, 120, 60)
                if request.url.path.startswith("/auth/steam/"):
                    await rate_limit(request, "login", address, 10, 60)
            body = bytearray()
            async with asyncio.timeout(10):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    body.extend(message.get("body", b""))
                    if len(body) > MAX_BODY_BYTES:
                        raise HTTPException(413, "Request body too large")
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            return await JSONResponse({"detail": "Request body timed out"}, status_code=408)(scope, receive, secure_send)
        except HTTPException as exc:
            return await JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                                      headers=exc.headers)(scope, receive, secure_send)

        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, secure_send)
