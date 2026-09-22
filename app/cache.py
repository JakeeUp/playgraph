"""A short-lived shared cache for public GET responses.

Only responses that are identical for every caller belong here: nothing under
/me, nothing that depends on who is asking. Security state in Redis fails
closed; this cache fails open, because a cache outage should cost speed, never
availability or correctness.

Invalidation is generational. Each scope keeps a counter that is part of every
key in it. A write bumps the counter after its commit, which retires every
older entry at once; they are simply never read again and expire on their TTL.
There are no key scans, and a reader that computed from pre-write data can only
ever store it under the retired generation.

Concurrent misses on one entry are coalesced. When a popular entry is cold or
has just expired, every caller would otherwise run the same query at once, a
cache stampede that starves the worker threads and stalls the whole process.
The first caller computes; the rest await its result.

Values are JSON, never pickled objects, for the same reason as the job queue.
"""

import asyncio
import hashlib
import json
from collections.abc import Callable
from typing import Any

import anyio
from fastapi import Request
from fastapi.encoders import jsonable_encoder
from redis.exceptions import RedisError
from starlette.concurrency import run_in_threadpool

PREFIX = "playgraph:cache"
CATALOG = "catalog"  # games and their metadata; changes when a library sync adds or enriches games
REVIEWS = "reviews"  # reviews, comments and the public feed; changes on every review or comment write

# One computation in flight per cache entry in this process.
_inflight: dict[str, asyncio.Future] = {}


def _store(request: Request):
    return getattr(request.app.state, "arq_pool", None)


def _entry(scope: str, generation: int, parts: tuple) -> str:
    digest = hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()
    return f"{PREFIX}:{scope}:{generation}:{digest}"


async def cached(request: Request, scope: str, parts: tuple, ttl: int, compute: Callable[[], Any]) -> Any:
    """Serve a public response from the cache, or compute it and store it.

    compute runs on the thread pool because it does blocking database work.
    parts must hold every input that changes the response. A hit and a miss
    return the same JSON-shaped data, so callers never see a difference.
    """
    store = _store(request)
    entry = None
    if store is not None:
        try:
            generation = int(await store.get(f"{PREFIX}:gen:{scope}") or 0)
            entry = _entry(scope, generation, parts)
            hit = await store.get(entry)
            if hit is not None:
                return json.loads(hit)
        except (RedisError, OSError, ValueError):
            entry = None
    if entry is None:
        return jsonable_encoder(await run_in_threadpool(compute))
    while (pending := _inflight.get(entry)) is not None:
        try:
            return await asyncio.shield(pending)
        except asyncio.CancelledError:
            if not pending.cancelled():
                raise  # this caller went away
            # The computing caller went away; loop and take over from it.
    future = asyncio.get_running_loop().create_future()
    _inflight[entry] = future
    try:
        value = jsonable_encoder(await run_in_threadpool(compute))
    except asyncio.CancelledError:
        future.cancel()
        raise
    except BaseException as error:
        # Same inputs, same failure (a 404, say), so waiters share it.
        future.set_exception(error)
        future.exception()  # marked as seen, so a failure nobody waited on is not logged twice
        raise
    else:
        future.set_result(value)
    finally:
        if _inflight.get(entry) is future:
            del _inflight[entry]
    try:
        await store.set(entry, json.dumps(value, separators=(",", ":")), ex=ttl)
    except (RedisError, OSError):
        pass
    return value


async def invalidate(store, scope: str) -> None:
    """Retire every cached entry in a scope. Best effort: TTLs bound staleness if Redis is down."""
    if store is None:
        return
    try:
        await store.incr(f"{PREFIX}:gen:{scope}")
    except (RedisError, OSError):
        pass


def invalidate_from_route(request: Request, scope: str) -> None:
    """Invalidate from a sync route, which FastAPI runs on a worker thread.

    Called after the commit and before the response, so the client that just
    wrote never reads its own write back from a stale entry.
    """
    anyio.from_thread.run(invalidate, _store(request), scope)
