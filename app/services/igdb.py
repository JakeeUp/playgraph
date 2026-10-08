"""Thin client for IGDB, the cross-console game database run by Twitch.

IGDB supplies what the store APIs don't: one ID per game across every
platform, cover art for games outside Steam, summaries, release dates and the
platforms a game came out on. PlayGraph uses it to give a Steam game and a
PlayStation game one page, and to let people find games nobody has synced.

Authentication is a Twitch application's Client ID and Secret, exchanged for
an app access token (client credentials grant). Users never touch it. Every
request is POST https://api.igdb.com/v4/<endpoint> with an Apicalypse query as
the body. IGDB allows 4 requests per second and 8 open requests; the client
stays inside both. Free for non-commercial use; a commercial launch needs an
IGDB partnership and visible attribution (PHASES.md release gates).

Schema notes, checked 2026-10-07 against the types generated from the IGDB
docs: external_games.category is deprecated in favour of
external_game_source, which references a table whose IDs are looked up by
name at runtime instead of hard-coded.
"""
from __future__ import annotations

import asyncio
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

import httpx

TOKEN_URL = "https://id.twitch.tv/oauth2/token"  # nosec B105 - a URL, not a secret
API_BASE = "https://api.igdb.com/v4"
IMAGE_BASE = "https://images.igdb.com/igdb/image/upload"

REQUESTS_PER_SECOND = 4
MAX_OPEN_REQUESTS = 8
PAGE_LIMIT = 500  # IGDB's maximum results per request
TOKEN_SKEW_SECONDS = 300  # renew this long before Twitch's stated expiry
MAX_RATE_LIMIT_RETRIES = 3
MAX_RETRY_AFTER = 30.0
MAX_QUERY_TEXT = 120
MAX_SUMMARY = 4000

# Source names in /external_game_sources, matched without regard to case
# (IGDB spells it "Playstation Store US"). Checked live 2026-10-07: Steam uids
# are Steam app IDs; PlayStation Store uids are Sony concept IDs, the same IDs
# the PSN gamelist returns, so PlayStation games match exactly too.
SOURCE_STEAM = "Steam"
SOURCE_PLAYSTATION_STORE = "PlayStation Store US"

GAME_FIELDS = ("name,slug,summary,first_release_date,cover.image_id,genres.name,"
               "platforms.slug,platforms.name,platforms.abbreviation,artworks.image_id,screenshots.image_id")
IMAGE_ID_RE = re.compile(r"[a-z0-9]{1,40}")
SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,60}")
# Control characters and the Apicalypse string delimiters never reach a query.
_UNSAFE_QUERY_CHARS = re.compile(r'[\x00-\x1f\x7f"\\;]')


class IGDBError(Exception):
    """An IGDB or Twitch failure. Messages never contain credentials or tokens."""

    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


class IGDBAuthError(IGDBError):
    """Twitch rejected the Client ID or Secret: the operator must fix them."""


class IGDBRateLimitedError(IGDBError):
    """IGDB kept answering 429 after the client's own retries."""


def search_text(value: str) -> str:
    """User search text made safe to place inside an Apicalypse string."""
    text = " ".join(_UNSAFE_QUERY_CHARS.sub(" ", value or "").split())
    return text[:MAX_QUERY_TEXT]


def _id_list(values) -> str:
    ids = sorted({int(v) for v in values})
    if not ids:
        raise ValueError("at least one ID is required")
    return ",".join(str(i) for i in ids)


def _uid_list(values) -> str:
    # External store IDs are matched as strings; only plain ID characters are
    # allowed so a value can never close the quoted string.
    uids = sorted({str(v) for v in values if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", str(v))})
    if not uids:
        raise ValueError("at least one valid external ID is required")
    return ",".join(f'"{uid}"' for uid in uids)


def source_id(sources: dict[str, int], name: str) -> int | None:
    return sources.get(name.casefold())


def cover_url(image_id: str | None, size: str = "t_cover_big") -> str | None:
    if not image_id or not IMAGE_ID_RE.fullmatch(image_id):
        return None
    return f"{IMAGE_BASE}/{size}/{image_id}.jpg"


def normalize_game(raw: dict) -> dict | None:
    """IGDB's game object reduced to what PlayGraph stores. None if unusable."""
    if not isinstance(raw, dict) or not isinstance(raw.get("id"), int) or not raw.get("name"):
        return None
    released = raw.get("first_release_date")
    cover = raw.get("cover") if isinstance(raw.get("cover"), dict) else {}
    image_id = cover.get("image_id")
    slug = raw.get("slug")
    # Wide art for banners: official artwork first, a screenshot otherwise.
    hero_image_id = None
    for field in ("artworks", "screenshots"):
        for image in raw.get(field) or []:
            candidate = image.get("image_id") if isinstance(image, dict) else None
            if isinstance(candidate, str) and IMAGE_ID_RE.fullmatch(candidate):
                hero_image_id = candidate
                break
        if hero_image_id:
            break
    platforms = []
    for platform in raw.get("platforms") or []:
        if isinstance(platform, dict) and isinstance(platform.get("slug"), str) and SLUG_RE.fullmatch(platform["slug"]):
            platforms.append({"slug": platform["slug"], "name": str(platform.get("name") or platform["slug"]),
                              "abbreviation": platform.get("abbreviation")})
    return {
        "igdb_id": raw["id"],
        "name": str(raw["name"]),
        "slug": slug if isinstance(slug, str) and SLUG_RE.fullmatch(slug) else None,
        "summary": (str(raw["summary"])[:MAX_SUMMARY] if raw.get("summary") else None),
        "first_release_date": (datetime.fromtimestamp(released, tz=timezone.utc)
                               if isinstance(released, int) and released > 0 else None),
        "cover_image_id": image_id if isinstance(image_id, str) and IMAGE_ID_RE.fullmatch(image_id) else None,
        "hero_image_id": hero_image_id,
        "genres": [str(g["name"]) for g in raw.get("genres") or [] if isinstance(g, dict) and g.get("name")],
        "platforms": platforms,
    }


class _State:
    """Process-wide token cache and request pacing, shared by every client."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.token: str | None = None
        self.expires_at = 0.0
        self.client_id: str | None = None
        self.starts: deque[float] = deque()
        self._loop = None
        self._token_lock: asyncio.Lock | None = None
        self._pace_lock: asyncio.Lock | None = None
        self._open: asyncio.Semaphore | None = None

    def primitives(self) -> tuple[asyncio.Lock, asyncio.Lock, asyncio.Semaphore]:
        # asyncio primitives belong to one event loop; repeated asyncio.run
        # calls (tests, scripts) get fresh ones instead of a cross-loop error.
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            self._loop = loop
            self._token_lock, self._pace_lock = asyncio.Lock(), asyncio.Lock()
            self._open = asyncio.Semaphore(MAX_OPEN_REQUESTS)
        return self._token_lock, self._pace_lock, self._open


_shared_state = _State()


class IGDBClient:
    """Async IGDB reader. Use as `async with IGDBClient() as igdb:`, or pass a
    shared httpx.AsyncClient (never closed by this class)."""

    def __init__(self, client_id: str | None = None, client_secret=None, *,
                 client: httpx.AsyncClient | None = None, state: _State | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep, timeout: float = 15):
        if client_id is None or client_secret is None:
            from app.config import settings
            client_id = settings.igdb_client_id if client_id is None else client_id
            client_secret = settings.igdb_client_secret if client_secret is None else client_secret
        getter = getattr(client_secret, "get_secret_value", None)
        self._client_id = (client_id or "").strip()
        self._client_secret = (getter() if getter else str(client_secret or "")).strip()
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout
        self._state = state or _shared_state
        self._sleep = sleep

    async def __aenter__(self) -> IGDBClient:
        return self

    async def __aexit__(self, *exc) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    # Authentication ---------------------------------------------------

    async def access_token(self) -> str:
        if not self._client_id or not self._client_secret:
            raise IGDBAuthError("IGDB_CLIENT_ID and IGDB_CLIENT_SECRET are not configured")
        state = self._state
        token_lock, _, _ = state.primitives()
        async with token_lock:
            if state.client_id != self._client_id:
                state.token, state.expires_at, state.client_id = None, 0.0, self._client_id
            if state.token and state.clock() < state.expires_at - TOKEN_SKEW_SECONDS:
                return state.token
            try:
                # Form body, not query string, so the secret never lands in a URL or access log.
                response = await self.http.post(TOKEN_URL, data={
                    "client_id": self._client_id, "client_secret": self._client_secret,
                    "grant_type": "client_credentials"})
            except httpx.HTTPError as exc:
                raise IGDBError(f"Twitch token request failed: {type(exc).__name__}") from None
            if response.status_code in (400, 401, 403):
                raise IGDBAuthError("Twitch rejected the IGDB client credentials", status=response.status_code)
            if response.status_code != 200:
                raise IGDBError("Twitch token request failed", status=response.status_code)
            try:
                body = response.json()
                token, expires_in = str(body["access_token"]), float(body["expires_in"])
            except (ValueError, KeyError, TypeError):
                raise IGDBError("Twitch token response was malformed", status=200) from None
            state.token, state.expires_at = token, state.clock() + expires_in
            return token

    def _invalidate_token(self) -> None:
        self._state.token, self._state.expires_at = None, 0.0

    # Transport ----------------------------------------------------------

    async def _pace(self) -> None:
        """At most REQUESTS_PER_SECOND request starts in any rolling second."""
        state = self._state
        _, pace_lock, _ = state.primitives()
        async with pace_lock:
            while True:
                now = state.clock()
                while state.starts and state.starts[0] <= now - 1.0:
                    state.starts.popleft()
                if len(state.starts) < REQUESTS_PER_SECOND:
                    break
                await self._sleep(state.starts[0] + 1.0 - now)
            state.starts.append(state.clock())

    async def query(self, endpoint: str, body: str) -> list:
        """POST an Apicalypse query and return IGDB's JSON list."""
        if not re.fullmatch(r"[a-z_]{1,40}", endpoint):
            raise ValueError("invalid IGDB endpoint")
        _, _, open_requests = self._state.primitives()
        renewed, throttled = False, 0
        while True:
            token = await self.access_token()
            async with open_requests:
                await self._pace()
                try:
                    response = await self.http.post(f"{API_BASE}/{endpoint}", content=body.encode(), headers={
                        "Client-ID": self._client_id, "Authorization": f"Bearer {token}",
                        "Accept": "application/json", "Content-Type": "text/plain"})
                except httpx.HTTPError as exc:
                    raise IGDBError(f"IGDB request failed: {type(exc).__name__}") from None
            if response.status_code == 401 and not renewed:
                renewed = True
                self._invalidate_token()
                continue
            if response.status_code == 429 and throttled < MAX_RATE_LIMIT_RETRIES:
                throttled += 1
                await self._sleep(_retry_after(response, throttled))
                continue
            break
        if response.status_code == 429:
            raise IGDBRateLimitedError("IGDB is rate limiting requests", status=429)
        if response.status_code in (401, 403):
            raise IGDBAuthError("IGDB rejected the app token", status=response.status_code)
        if response.status_code != 200:
            raise IGDBError("IGDB request failed", status=response.status_code)
        try:
            data = response.json()
        except ValueError:
            raise IGDBError("IGDB returned invalid JSON", status=200) from None
        if not isinstance(data, list):
            raise IGDBError("IGDB returned an unexpected shape", status=200)
        return data

    # Reads -------------------------------------------------------------

    async def external_game_sources(self) -> dict[str, int]:
        """{lower-cased source name: id}, e.g. {"steam": 1, ...}. Look names up
        with source_id()."""
        rows = await self.query("external_game_sources", f"fields id,name; limit {PAGE_LIMIT};")
        return {str(r["name"]).casefold(): r["id"] for r in rows if isinstance(r, dict)
                and isinstance(r.get("id"), int) and r.get("name")}

    async def external_games(self, source_id: int, uids) -> dict[str, int]:
        """{store uid: IGDB game id} for exact store-ID matches. Unknown uids are absent."""
        uids = list(dict.fromkeys(str(u) for u in uids))
        found: dict[str, int] = {}
        for start in range(0, len(uids), PAGE_LIMIT):
            chunk = uids[start:start + PAGE_LIMIT]
            rows = await self.query("external_games", (
                f"fields uid,game; where external_game_source = {int(source_id)} & uid = ({_uid_list(chunk)});"
                f" limit {PAGE_LIMIT};"))
            for row in rows:
                if isinstance(row, dict) and isinstance(row.get("game"), int) and row.get("uid") in chunk:
                    # One store ID can be listed for several editions; keep the first.
                    found.setdefault(row["uid"], row["game"])
        return found

    async def games(self, ids) -> list[dict]:
        ids = sorted({int(i) for i in ids})
        out = []
        for start in range(0, len(ids), PAGE_LIMIT):
            chunk = ids[start:start + PAGE_LIMIT]
            rows = await self.query("games", f"fields {GAME_FIELDS}; where id = ({_id_list(chunk)}); limit {PAGE_LIMIT};")
            out.extend(game for game in map(normalize_game, rows) if game)
        return out

    async def search(self, text: str, *, limit: int = 20, platform_slugs=()) -> list[dict]:
        """Main games matching the text, best match first."""
        text = search_text(text)
        if len(text) < 2:
            return []
        where = ["version_parent = null"]
        slugs = [s for s in platform_slugs if isinstance(s, str) and SLUG_RE.fullmatch(s)]
        if slugs:
            where.append("platforms.slug = (" + ",".join(f'"{s}"' for s in slugs) + ")")
        rows = await self.query("games", (f'search "{text}"; fields {GAME_FIELDS}; '
                                          f"where {' & '.join(where)}; limit {max(1, min(int(limit), 50))};"))
        return [game for game in map(normalize_game, rows) if game]


def _retry_after(response: httpx.Response, attempt: int) -> float:
    try:
        seconds = float(response.headers.get("retry-after", ""))
    except ValueError:
        seconds = float(attempt)  # 1s, 2s, 3s: IGDB's limit is per second
    return min(max(seconds, 0.25), MAX_RETRY_AFTER)
