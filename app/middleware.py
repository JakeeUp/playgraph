"""Bound request work before parsing and keep private responses out of caches."""

import asyncio
import logging
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse

from app.config import settings
from app.security import rate_headers, rate_limit
from app.services.news import IMAGE_HOSTS
from app.services.steam_news import CLAN_IMAGES

MAX_BODY_BYTES = 65536
REQUESTS_PER_MINUTE = 120
LOGINS_PER_MINUTE = 10
NEWS_IMAGES = " ".join([*(f"https://*.{host}" for host in IMAGE_HOSTS), urlsplit(CLAN_IMAGES)._replace(path="").geturl()])
logger = logging.getLogger("playgraph.security")

TAIL = "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
APP_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; "
    "connect-src 'self'; img-src 'self' https://shared.fastly.steamstatic.com "
    "https://shared.akamai.steamstatic.com https://cdn.akamai.steamstatic.com "
    "https://cdn.cloudflare.steamstatic.com https://images.igdb.com "
    "https://image.api.playstation.com https://psnobj.prod.dl.playstation.net "
    f"{NEWS_IMAGES}; font-src 'self'; " + TAIL
)
NOT_FOUND_CSP = "default-src 'none'; style-src 'self'; font-src 'self'; img-src 'self'; " + TAIL
API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
DOCS_PATHS = {"/docs", "/docs/oauth2-redirect", "/redoc"}


def account_csp() -> str:
    provider = settings.clerk_origin if settings.clerk_enabled else ""
    # Clerk's runtime CSS is confined to the account page. No
    # unsafe script execution is enabled on this or the app page.
    return (
        "default-src 'none'; script-src 'self' " + provider +
        " https://challenges.cloudflare.com https://*.protect.clerk.com; "
        "style-src 'self' 'unsafe-inline'; connect-src 'self' " + provider +
        " https://*.protect.clerk.com:*; img-src 'self' https://img.clerk.com data:; "
        "worker-src 'self' blob:; frame-src 'self' https://challenges.cloudflare.com "
        "https://*.protect.clerk.com; font-src 'self'; " + TAIL
    )


def security_headers(path: str, status: int, content_type: str) -> dict[str, str]:
    """Every header the middleware adds to a response, in the order it adds them."""
    headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        # Public static files are stored but revalidated against their ETag,
        # so repeat visits get 304s. Everything else, every API response
        # included, is never stored.
        "Cache-Control": "no-cache" if path.startswith("/assets/") else "no-store",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        # Isolate this origin's windows from other sites' windows, and refuse
        # to let another site's page embed these responses, JSON included.
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-origin",
        "X-Permitted-Cross-Domain-Policies": "none",
    }
    if settings.environment == "production":
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if path == "/app":
        headers["Content-Security-Policy"] = APP_CSP
    elif path == "/account":
        headers["Content-Security-Policy"] = account_csp()
    elif status == 404 and content_type.startswith("text/html"):
        # The not-found page: its own stylesheet, fonts and icon, and no script.
        headers["Content-Security-Policy"] = NOT_FOUND_CSP
    elif path not in DOCS_PATHS:
        headers["Content-Security-Policy"] = API_CSP
    return headers


class SecurityMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)
        # Remaining budget in the per-address bucket, reported on every response
        # so well-behaved clients can slow down before they are refused.
        budget = None

        async def secure_send(message):
            if message["type"] == "http.response.start":
                if request.url.path.startswith("/auth/") and message["status"] >= 400:
                    logger.warning("authentication_rejected status=%s", message["status"])
                headers = MutableHeaders(scope=message)
                if budget is not None and "ratelimit-limit" not in headers:
                    headers.update(rate_headers(REQUESTS_PER_MINUTE, *budget))
                for name, value in security_headers(request.url.path, message["status"],
                                                    headers.get("content-type", "")).items():
                    headers[name] = value
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
                budget = await rate_limit(request, "requests", address, REQUESTS_PER_MINUTE, 60)
                if request.url.path.startswith("/auth/steam/"):
                    await rate_limit(request, "login", address, LOGINS_PER_MINUTE, 60)
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
