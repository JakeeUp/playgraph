"""Thin wrapper around the unofficial PlayStation Network endpoints we need.

PlayStation has no public third-party API for libraries, trophies or play
time. These are the internal endpoints the PlayStation App uses, as
documented by the community libraries psn-api (achievements-app) and PSNAWP.
They can change or disappear without notice, so everything Sony-shaped is
kept in this one module and normalized before it reaches the rest of the app.

Authentication uses ONE server-owned NPSSO token (the site owner's, from the
PSN_NPSSO setting). Users never hand PlayGraph their PSN credentials or
tokens; they only prove they own an Online ID by putting a one-time code in
their public "About Me" text. The server then reads that account's public
trophy list and, where the player's privacy settings allow it, play time.

Like app/services/steam.py, this module has no database or worker logic, so
it can be unit tested against a mocked httpx transport.

Errors are split by who has to act:
  PSNAuthError     - the server NPSSO is missing, wrong or expired. Operator problem.
  PSNNotFoundError - the Online ID or account ID does not exist.
  PSNPrivateError  - the account exists but its privacy settings hide the data.
  PSNRateLimitedError / PSNError - Sony throttled us or something else broke.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import secrets
import time
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, urlsplit

import httpx

AUTH_BASE = "https://ca.account.sony.com/api/authz/v3/oauth"
LEGACY_PROFILE_BASE = "https://us-prof.np.community.playstation.net/userProfile/v1/users"
PROFILE_BASE = "https://m.np.playstation.com/api/userProfile/v1/internal/users"
TROPHY_BASE = "https://m.np.playstation.com/api/trophy/v1"
GAMELIST_BASE = "https://m.np.playstation.com/api/gamelist/v2"

# The PlayStation App's own OAuth client. These are not PlayGraph secrets:
# they are the fixed, publicly published values that psn-api and PSNAWP both
# ship. The Basic header is base64("<client_id>:<client_secret>").
CLIENT_ID = "09515159-7237-4370-9b40-3806e67c0891"
CLIENT_BASIC = "Basic MDk1MTUxNTktNzIzNy00MzcwLTliNDAtMzgwNmU2N2MwODkxOnVjUGprYTV0bnRCMktxc1A="
REDIRECT_URI = "com.scee.psxandroid.scecompcall://redirect"
SCOPE = "psn:mobile.v2.core psn:clientapp"

# Renew a little before Sony's stated expiry so a request never goes out with
# a token that dies in flight.
TOKEN_SKEW_SECONDS = 60
# Request pacing (budget, window, minimum gap) comes from the psn_* settings in
# app/config.py, which also explain the chosen values.
# On a 429 without Retry-After, pause this long.
DEFAULT_RATE_LIMIT_BACKOFF = 600.0
MAX_RETRY_AFTER = 3600.0
# A 429 pause up to this long is slept through and the request retried once;
# a longer one fails fast so a web request never hangs and arq can defer.
INLINE_RETRY_MAX = 30.0
TROPHY_PAGE_SIZE = 800  # documented maximum for trophyTitles
GAMELIST_PAGE_SIZE = 200  # what PSNAWP pages with; larger is unverified
MAX_PAGES = 100  # backstop against a pager that never ends
TROPHY_GRADES = ("platinum", "gold", "silver", "bronze")
PLAYED_CATEGORIES = ("ps4_game", "ps5_native_game")
CATEGORY_PLATFORM = {"ps4_game": "PS4", "ps5_native_game": "PS5"}

# Sony error codes seen in recorded responses.
ERR_ACCESS_CONTROL = 2240526  # 403 "Not permitted by access control" (private trophies)
ERR_BAD_ACCOUNT_ID = 2281473  # 400 "Bad Request (path: accountId)" (no such account)
ERR_USER_NOT_FOUND = 2105356  # 404 "User not found" (legacy profile2 lookup)
ERR_BAD_TITLE_QUERY = 2240513  # 400 "Bad Request (query: npTitleId)" (too many IDs)
NPSSO_EXPIRED_CODE = "4165"  # authorize redirect error_code for a bad/expired NPSSO

# Used with fullmatch, so a trailing newline cannot slip past "$". Every
# accepted character is URL-unreserved, so the ID is safe in a request path.
ONLINE_ID_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,15}")
ACCOUNT_ID_RE = re.compile(r"[0-9]{1,20}")
TITLE_ID_RE = re.compile(r"[A-Z]{4}[0-9]{5}_[0-9]{2}")  # e.g. PPSA01506_00, CUSA12057_00
# Title IDs per titles/trophyTitles lookup. psn-api documents a limit of 5,
# and a live check (2026-10) confirmed it: 5 IDs -> 200, 6 IDs -> 400
# ERR_BAD_TITLE_QUERY. If Sony ever lowers it, the client halves the batch.
TITLE_LOOKUP_BATCH = 5
DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$")

CODE_PREFIX = "PLAYGRAPH-"
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I lookalikes
CODE_LENGTH = 6


class PSNError(Exception):
    """An upstream PSN failure that is not one of the specific cases below."""

    def __init__(self, message: str, *, status: int | None = None, code: int | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


class PSNAuthError(PSNError):
    """The server NPSSO is missing, invalid or expired. The operator must
    sign in to PlayStation as the server account and set a fresh PSN_NPSSO."""


class PSNNotFoundError(PSNError):
    """The Online ID or account ID does not exist on PSN."""


class PSNPrivateError(PSNError):
    """The account exists, but its privacy settings hide this data."""


class PSNRateLimitedError(PSNError):
    """Sony answered 429, a 429 pause is still running, or the request budget
    is spent. retry_after is the wait in seconds (Sony's Retry-After, or the
    default backoff), so a job can be deferred by that much."""

    def __init__(self, message: str, *, retry_after: float, status: int | None = 429, code: int | None = None):
        super().__init__(message, status=status, code=code)
        self.retry_after = retry_after


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Seconds from a Retry-After header (delta-seconds or an HTTP date),
    clamped to [0, MAX_RETRY_AFTER]. None when absent or unparseable."""
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = (when - (now or datetime.now(timezone.utc))).total_seconds()
    if seconds != seconds:  # NaN
        return None
    return min(max(seconds, 0.0), MAX_RETRY_AFTER)


def _retry_after(response: httpx.Response) -> float:
    """How long a 429 asks us to pause: Retry-After, or the default backoff."""
    seconds = parse_retry_after(response.headers.get("retry-after"))
    return DEFAULT_RATE_LIMIT_BACKOFF if seconds is None else seconds


def parse_duration_minutes(value: str | None) -> int | None:
    """Whole minutes from an ISO-8601 duration such as "PT228H56M33S".

    PSN reports hours without rolling them into days, so "PT250H" is normal.
    Days are accepted anyway in case Sony ever emits them. Seconds are
    truncated, matching Steam's whole-minute playtime. Anything unparseable
    returns None rather than 0: missing data must not read as "never played".
    """
    if not isinstance(value, str):
        return None
    match = DURATION_RE.match(value.strip().upper())
    if not match or not any(match.groupdict().values()):
        return None
    parts = match.groupdict()
    seconds = (int(parts["days"] or 0) * 86400 + int(parts["hours"] or 0) * 3600
               + int(parts["minutes"] or 0) * 60 + float(parts["seconds"] or 0))
    return int(seconds // 60)


def parse_timestamp(value: str | None) -> datetime | None:
    """Sony's ISO timestamps ("2024-08-03T19:28:27.12Z") as aware datetimes."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def generate_verification_code() -> str:
    """A one-time code the user pastes into their PSN "About Me"."""
    return CODE_PREFIX + "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def verification_matches(about_me: str | None, code: str) -> bool:
    """True when the code appears in the About Me text, ignoring case.

    The code must stand alone: "PLAYGRAPH-ABC234" does not match inside
    "PLAYGRAPH-ABC2345", so a code cannot be satisfied by a longer one.
    """
    if not about_me or not code or not code.strip():
        return False
    pattern = r"(?<![A-Za-z0-9])" + re.escape(code.strip()) + r"(?![A-Za-z0-9])"
    return re.search(pattern, about_me, re.IGNORECASE) is not None


def validate_online_id(online_id: str) -> str:
    """PSN Online IDs are 3-16 characters: a letter, then letters, digits,
    hyphens or underscores. Rejecting anything else keeps user input out of
    the request path."""
    online_id = (online_id or "").strip()
    if not ONLINE_ID_RE.fullmatch(online_id):
        raise ValueError("Not a valid PSN Online ID")
    return online_id


def _validate_account_id(account_id: str) -> str:
    account_id = str(account_id or "").strip()
    if not ACCOUNT_ID_RE.fullmatch(account_id):
        raise ValueError("Not a valid PSN account ID")
    return account_id


def _secret_value(value) -> str:
    if value is None:
        return ""
    getter = getattr(value, "get_secret_value", None)
    return (getter() if getter else str(value)).strip()


def _counts(raw) -> dict[str, int]:
    raw = raw if isinstance(raw, dict) else {}
    return {grade: int(raw.get(grade) or 0) for grade in TROPHY_GRADES}


def normalize_trophy_title(raw: dict) -> dict:
    platforms = [p.strip() for p in str(raw.get("trophyTitlePlatform") or "").split(",") if p.strip()]
    progress = raw.get("progress")
    return {
        "np_communication_id": raw.get("npCommunicationId"),
        # "trophy" = PS3/PS4/Vita trophy set, "trophy2" = PS5 trophy set.
        "np_service_name": raw.get("npServiceName"),
        "name": raw.get("trophyTitleName"),
        "platforms": platforms,
        "icon_url": raw.get("trophyTitleIconUrl"),
        "earned": _counts(raw.get("earnedTrophies")),
        "defined": _counts(raw.get("definedTrophies")),
        "progress": int(progress) if isinstance(progress, (int, float)) else None,
        "trophy_set_version": raw.get("trophySetVersion"),
        "last_updated": parse_timestamp(raw.get("lastUpdatedDateTime")),
    }


def normalize_played_title(raw: dict) -> dict:
    concept = raw.get("concept") if isinstance(raw.get("concept"), dict) else {}
    category = raw.get("category")
    return {
        "title_id": raw.get("titleId"),
        "concept_id": concept.get("id"),
        # Every regional/platform title ID Sony groups under the same concept.
        "concept_title_ids": list(concept.get("titleIds") or []),
        "name": raw.get("name") or concept.get("name"),
        "image_url": raw.get("imageUrl"),
        "category": category,
        "platform": CATEGORY_PLATFORM.get(category),
        "play_minutes": parse_duration_minutes(raw.get("playDuration")),
        "play_count": raw.get("playCount"),
        "first_played": parse_timestamp(raw.get("firstPlayedDateTime")),
        "last_played": parse_timestamp(raw.get("lastPlayedDateTime")),
    }


class TokenState:
    """Process-wide token cache and request budget for the server account.

    One instance is shared by every PSNClient by default, so a worker that
    builds a client per job still reuses the access token and refresh token
    until they near expiry instead of re-running the NPSSO exchange, and every
    client in the process draws on one request budget.

    The budget is per process, not per Sony account: the API and the worker
    each keep their own. See docs/PSN_INTEGRATION.md.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.reset()
        self.request_starts: deque[float] = deque()  # recent request start times
        self.blocked_until = 0.0  # set by a 429: no request starts before this
        self.lookup_batch = TITLE_LOOKUP_BATCH  # shrinks if Sony rejects a batch size
        self._loop = None
        self._token_lock: asyncio.Lock | None = None
        self._pace_lock: asyncio.Lock | None = None

    def reset(self, fingerprint: str | None = None) -> None:
        self.fingerprint = fingerprint
        self.access_token: str | None = None
        self.access_expires_at = 0.0
        self.refresh_token: str | None = None
        self.refresh_expires_at = 0.0

    def locks(self) -> tuple[asyncio.Lock, asyncio.Lock]:
        # asyncio locks belong to one event loop; tests and scripts that call
        # asyncio.run repeatedly get fresh locks instead of a cross-loop error.
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            self._loop, self._token_lock, self._pace_lock = loop, asyncio.Lock(), asyncio.Lock()
        return self._token_lock, self._pace_lock

    def access_valid(self) -> bool:
        return bool(self.access_token) and self.clock() < self.access_expires_at - TOKEN_SKEW_SECONDS

    def refresh_valid(self) -> bool:
        return bool(self.refresh_token) and self.clock() < self.refresh_expires_at - TOKEN_SKEW_SECONDS


_shared_state = TokenState()


class PSNClient:
    """Async PSN reader authenticated as the server account.

    Use as `async with PSNClient() as psn:`, or pass an existing
    httpx.AsyncClient (which this class will then never close) so a sync job
    can reuse one connection pool across many calls.

    max_wait caps how long a request may wait for the budget. A web request
    passes one so a spent budget answers at once instead of hanging for up to
    a whole window; the worker leaves it unset and simply waits.
    """

    def __init__(self, npsso=None, *, client: httpx.AsyncClient | None = None,
                 state: TokenState | None = None, min_interval: float | None = None,
                 budget: int | None = None, window: float | None = None, max_wait: float | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep, timeout: float = 15):
        from app.config import settings
        npsso = settings.psn_npsso if npsso is None else npsso
        min_interval = settings.psn_min_request_interval if min_interval is None else min_interval
        budget = settings.psn_request_budget if budget is None else budget
        window = settings.psn_budget_window_seconds if window is None else window
        self._npsso = _secret_value(npsso)
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout
        self._state = state or _shared_state
        self._min_interval = max(float(min_interval), 0.0)
        self._budget = int(budget)
        self._window = float(window)
        self._max_wait = max_wait
        self._sleep = sleep

    async def __aenter__(self) -> PSNClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
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
        """A valid bearer token, renewed via the refresh token when possible
        and via the NPSSO only when the refresh token is gone or rejected."""
        if not self._npsso:
            raise PSNAuthError("PSN_NPSSO is not configured")
        state = self._state
        fingerprint = hashlib.sha256(self._npsso.encode()).hexdigest()
        token_lock, _ = state.locks()
        async with token_lock:
            if state.fingerprint != fingerprint:
                state.reset(fingerprint)  # operator rotated the NPSSO
            if state.access_valid():
                return state.access_token
            if state.refresh_valid():
                try:
                    await self._token_request({"refresh_token": state.refresh_token,
                                               "grant_type": "refresh_token", "scope": SCOPE,
                                               "token_format": "jwt"})  # nosec B105
                    return state.access_token
                except PSNAuthError:
                    state.reset(fingerprint)  # fall back to a full NPSSO exchange
            code = await self._authorization_code()
            await self._token_request({"code": code, "grant_type": "authorization_code",
                                       "redirect_uri": REDIRECT_URI, "scope": SCOPE,
                                       "token_format": "jwt"})  # nosec B105
            return state.access_token

    def invalidate_access_token(self) -> None:
        self._state.access_token = None
        self._state.access_expires_at = 0.0

    async def _authorization_code(self) -> str:
        params = {"access_type": "offline", "client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI,
                  "response_type": "code", "scope": SCOPE}
        # Sent as a per-request header, not through the client cookie jar, so
        # a shared AsyncClient never replays the NPSSO to any other request.
        response = await self._send("GET", f"{AUTH_BASE}/authorize", params=params,
                                    headers={"Cookie": f"npsso={self._npsso}"}, follow_redirects=False)
        location = response.headers.get("location", "")
        query = parse_qs(urlsplit(location).query) if location else {}
        if "code" in query and query["code"][0]:
            return query["code"][0]
        if NPSSO_EXPIRED_CODE in query.get("error_code", []):
            raise PSNAuthError("PSN_NPSSO has expired or is incorrect; set a fresh one",
                               status=response.status_code)
        raise PSNAuthError("PSN did not issue an authorization code for PSN_NPSSO",
                           status=response.status_code)

    async def _token_request(self, form: dict) -> None:
        response = await self._send("POST", f"{AUTH_BASE}/token", data=form,
                                    headers={"Authorization": CLIENT_BASIC,
                                             "Content-Type": "application/x-www-form-urlencoded"})
        if response.status_code != 200:
            raise PSNAuthError("PSN token request was rejected", status=response.status_code)
        try:
            body = response.json()
            access, expires_in = body["access_token"], float(body["expires_in"])
        except (ValueError, KeyError, TypeError):
            raise PSNAuthError("PSN token response was malformed", status=response.status_code) from None
        state, now = self._state, self._state.clock()
        state.access_token, state.access_expires_at = access, now + expires_in
        if body.get("refresh_token"):
            state.refresh_token = body["refresh_token"]
            state.refresh_expires_at = now + float(body.get("refresh_token_expires_in") or 0)

    # Transport ----------------------------------------------------------

    def _slot_wait(self, now: float) -> float:
        """Seconds until the next request may start (<= 0 means now)."""
        state = self._state
        starts = state.request_starts
        while starts and starts[0] <= now - self._window:
            starts.popleft()
        wait = state.blocked_until - now
        if len(starts) >= self._budget:
            # Budget spent: wait until enough of the oldest starts age out.
            wait = max(wait, starts[-self._budget] + self._window - now)
        if starts:
            wait = max(wait, starts[-1] + self._min_interval - now)
        return wait

    async def _acquire(self) -> None:
        """Wait for a request slot. Requests burst at min_interval until the
        rolling-window budget is spent, then wait for the oldest start to age
        out. Nothing starts during a 429 pause; a pause longer than
        INLINE_RETRY_MAX fails fast instead of hanging the caller."""
        state = self._state
        _, pace_lock = state.locks()
        async with pace_lock:
            while True:
                now = state.clock()
                paused = state.blocked_until - now
                if paused > INLINE_RETRY_MAX:
                    raise PSNRateLimitedError(f"PSN rate limit pause, {paused:.0f}s left", retry_after=paused)
                wait = self._slot_wait(now)
                if wait <= 0:
                    break
                if self._max_wait is not None and wait > self._max_wait:
                    raise PSNRateLimitedError(f"PSN request budget spent, {wait:.0f}s until a slot",
                                              retry_after=wait)
                await self._sleep(wait)
            state.request_starts.append(state.clock())

    async def _send(self, method: str, url: str, **kwargs) -> httpx.Response:
        await self._acquire()
        try:
            response = await self.http.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise PSNError(f"PSN request failed: {type(exc).__name__}") from None
        if response.status_code == 429:
            # Sony considers the budget spent: pause every request in this
            # process, then report it. Auth requests land here too, so a
            # throttled token exchange never reads as a bad NPSSO.
            error = _error_for(response)
            state = self._state
            state.blocked_until = max(state.blocked_until, state.clock() + error.retry_after)
            raise error
        return response

    async def _get_json(self, url: str, params: dict | None = None) -> dict:
        renewed = retried = False
        while True:
            token = await self.access_token()
            try:
                response = await self._send("GET", url, params=params, headers={
                    "Authorization": f"Bearer {token}", "Accept-Language": "en-US"})
            except PSNRateLimitedError as exc:
                if retried or exc.retry_after > INLINE_RETRY_MAX:
                    raise
                retried = True  # short pause: _acquire sleeps it out, then retry once
                continue
            if response.status_code == 401 and not renewed:
                renewed = True
                self.invalidate_access_token()  # revoked early; renew once
                continue
            break
        if response.status_code == 200:
            try:
                return response.json()
            except ValueError:
                raise PSNError("PSN returned invalid JSON", status=200) from None
        raise _error_for(response)

    # Reads -------------------------------------------------------------

    async def resolve_profile(self, online_id: str) -> dict:
        """{"account_id", "online_id"} with Sony's canonical Online ID spelling."""
        online_id = validate_online_id(online_id)
        body = await self._get_json(f"{LEGACY_PROFILE_BASE}/{online_id}/profile2",
                                    {"fields": "accountId,onlineId,currentOnlineId"})
        profile = body.get("profile") or {}
        if not profile.get("accountId"):
            raise PSNNotFoundError("PSN user not found", status=200)
        return {"account_id": str(profile["accountId"]),
                "online_id": profile.get("currentOnlineId") or profile.get("onlineId") or online_id}

    async def get_profile(self, account_id: str) -> dict:
        """{"online_id", "about_me", "avatar_url"} for an accountId."""
        body = await self._get_json(f"{PROFILE_BASE}/{_validate_account_id(account_id)}/profiles")
        avatars = {a.get("size"): a.get("url") for a in body.get("avatars") or [] if isinstance(a, dict)}
        return {"online_id": body.get("onlineId"), "about_me": body.get("aboutMe") or "",
                "avatar_url": avatars.get("xl") or avatars.get("l") or avatars.get("m")}

    async def get_about_me(self, account_id: str) -> str:
        return (await self.get_profile(account_id))["about_me"]

    async def check_verification(self, account_id: str, code: str) -> bool:
        return verification_matches(await self.get_about_me(account_id), code)

    async def get_trophy_titles(self, account_id: str) -> list[dict]:
        """Every trophy list on the account, PS3 through PS5. Raises
        PSNPrivateError when the trophy list is hidden."""
        account_id = _validate_account_id(account_id)
        raw = await self._paged(f"{TROPHY_BASE}/users/{account_id}/trophyTitles", "trophyTitles",
                                TROPHY_PAGE_SIZE)
        return [normalize_trophy_title(t) for t in raw]

    async def get_played_titles(self, account_id: str) -> list[dict] | None:
        """PS4/PS5 games with play time. None when privacy hides the list;
        that is a normal state for a player to be in, not an error."""
        account_id = _validate_account_id(account_id)
        try:
            raw = await self._paged(f"{GAMELIST_BASE}/users/{account_id}/titles", "titles",
                                    GAMELIST_PAGE_SIZE, {"categories": ",".join(PLAYED_CATEGORIES)})
        except PSNPrivateError:
            return None
        # Filter locally too: without the filter Sony also lists media apps.
        return [normalize_played_title(t) for t in raw if t.get("category") in PLAYED_CATEGORIES]

    async def get_trophy_lists_for_titles(self, account_id: str, title_ids) -> dict[str, list[str]]:
        """titleId -> [npCommunicationId, ...] for the given PS4/PS5 title IDs.

        Trophy lists carry no title ID or concept, so this is how a trophy list
        is tied to a played game. Sony only answers for titles this account
        has played; others are simply absent. Malformed IDs are skipped
        without a request. Raises PSNPrivateError when trophies are hidden.
        """
        account_id = _validate_account_id(account_id)
        wanted = list(dict.fromkeys(t for t in title_ids if isinstance(t, str) and TITLE_ID_RE.fullmatch(t)))
        found: dict[str, list[str]] = {}
        state = self._state
        start = 0
        while start < len(wanted):
            batch = wanted[start:start + max(1, state.lookup_batch)]
            try:
                body = await self._get_json(f"{TROPHY_BASE}/users/{account_id}/titles/trophyTitles",
                                            {"npTitleIds": ",".join(batch)})
            except PSNError as exc:
                if len(batch) > 1 and exc.status == 400 and exc.code == ERR_BAD_TITLE_QUERY:
                    # Sony refused this many IDs: halve the batch for the rest
                    # of this process's life and retry the same titles.
                    state.lookup_batch = len(batch) // 2
                    continue
                raise
            start += len(batch)
            for title in body.get("titles") or []:
                if not isinstance(title, dict) or title.get("npTitleId") not in batch:
                    continue
                lists = [t.get("npCommunicationId") for t in title.get("trophyTitles") or []
                         if isinstance(t, dict) and t.get("npCommunicationId")]
                if lists:
                    found[title["npTitleId"]] = lists
        return found

    async def _paged(self, url: str, key: str, page_size: int, extra: dict | None = None) -> list[dict]:
        items: list[dict] = []
        offset = 0
        for _ in range(MAX_PAGES):
            body = await self._get_json(url, {**(extra or {}), "limit": page_size, "offset": offset})
            page = body.get(key) or []
            items.extend(item for item in page if isinstance(item, dict))
            next_offset = body.get("nextOffset")
            if not page or not isinstance(next_offset, int) or next_offset <= offset:
                return items
            offset = next_offset
        raise PSNError(f"PSN paging did not finish after {MAX_PAGES} pages")


def _error_for(response: httpx.Response) -> PSNError:
    code, message = None, ""
    try:
        error = response.json().get("error") or {}
        code, message = error.get("code"), str(error.get("message") or "")
    except (ValueError, AttributeError):
        pass
    status = response.status_code
    detail = f"PSN {status}" + (f" ({code}: {message})" if code or message else "")
    if status == 401:
        return PSNAuthError(detail + " - the server PSN token was rejected", status=status, code=code)
    if status == 403:
        return PSNPrivateError(detail, status=status, code=code)
    if status == 404 or code in (ERR_USER_NOT_FOUND, ERR_BAD_ACCOUNT_ID):
        return PSNNotFoundError(detail, status=status, code=code)
    if status == 429:
        return PSNRateLimitedError(detail, retry_after=_retry_after(response), code=code)
    return PSNError(detail, status=status, code=code)
