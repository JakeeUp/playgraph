"""Demo mode: one fixed synthetic player, and a central read-only guard."""

from starlette.responses import JSONResponse

from app.config import settings

# The seeded player the demo login signs in as. Deliberately outside the real
# SteamID range, like the demo games' app ids.
DEMO_STEAM_ID = "76561190000000100"
DEMO_NAME = "Demo Player"

READ_ONLY = "The demo is read-only."
UNAVAILABLE = "Not available on the demo."
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
# The only writes the demo accepts.
WRITE_ALLOWLIST = {("POST", "/demo/login"), ("POST", "/auth/logout")}
# Anything that signs in with, links to, or calls Steam, PlayStation, Clerk,
# IGDB or the news providers. Refused for every method, before any route runs.
EXTERNAL_PREFIXES = ("/auth/steam", "/auth/psn", "/auth/clerk", "/me/psn", "/me/sync", "/news")


def _under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


class DemoGuard:
    """Checked on every request at run time, so it costs nothing when DEMO_MODE is off."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and settings.demo_mode:
            path = scope["path"].rstrip("/") or "/"
            method = scope["method"]
            response = None
            if any(_under(path, prefix) for prefix in EXTERNAL_PREFIXES):
                response = JSONResponse({"detail": UNAVAILABLE}, status_code=404)
            elif method in MUTATING and (method, path) not in WRITE_ALLOWLIST:
                response = JSONResponse({"detail": READ_ONLY, "demo": True}, status_code=403)
            if response is not None:
                return await response(scope, receive, send)
        await self.app(scope, receive, send)
