"""Small shared PSN state kept in Redis by the API and the worker.

Two things live here:
- The operator flag. When Sony rejects the server NPSSO, every PSN call will
  fail until the owner sets a new PSN_NPSSO. The flag records a fingerprint of
  the rejected value so neither process keeps hammering Sony with it. A new
  NPSSO has a different fingerprint, so setting one clears the stop at once.
- Each user's last PSN sync outcome (trophies or play time hidden, account
  gone), so the account page can explain what was imported.

Nothing here stores a token or the NPSSO itself. Redis failures are ignored:
this is advisory state, not security state.
"""
import hashlib
import json

from redis.exceptions import RedisError

from app.config import settings

AUTH_FLAG_KEY = "playgraph:psn:auth-failed"
AUTH_FLAG_SECONDS = 86400  # retry a rejected NPSSO at most daily even if nobody changes it
STATUS_SECONDS = 90 * 86400


def status_key(user_id: int) -> str:
    return f"playgraph:psn:status:{int(user_id)}"


def npsso_fingerprint() -> str:
    value = settings.psn_npsso.get_secret_value().strip()
    return hashlib.sha256(b"playgraph-psn-flag:" + value.encode()).hexdigest()[:16]


async def auth_blocked(store) -> bool:
    if store is None:
        return False
    try:
        flag = await store.get(AUTH_FLAG_KEY)
    except (RedisError, OSError):
        return False
    if isinstance(flag, bytes):
        flag = flag.decode()
    return flag == npsso_fingerprint()


async def block_auth(store) -> None:
    if store is None:
        return
    try:
        await store.set(AUTH_FLAG_KEY, npsso_fingerprint(), ex=AUTH_FLAG_SECONDS)
    except (RedisError, OSError):
        pass


async def write_status(store, user_id: int, status: dict) -> None:
    if store is None:
        return
    try:
        await store.set(status_key(user_id), json.dumps(status, separators=(",", ":")), ex=STATUS_SECONDS)
    except (RedisError, OSError):
        pass


async def read_status(store, user_id: int) -> dict | None:
    if store is None:
        return None
    try:
        raw = await store.get(status_key(user_id))
        return json.loads(raw) if raw else None
    except (RedisError, OSError, ValueError):
        return None
