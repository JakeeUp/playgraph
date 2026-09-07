"""Dev utility: clear every arq job from Redis.

Useful when the queue has stale or retrying jobs left over from an earlier
run - arq retries failed jobs on a delay, so a job enqueued minutes ago can
suddenly wake up and start competing with a current one.

    python scripts/reset_queue.py

Only touches arq's own keys, so it is safe to run against a Redis instance
shared with something else.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arq import create_pool
from arq.connections import RedisSettings

from app.config import settings

ARQ_KEY_PATTERNS = ["arq:job:*", "arq:result:*", "arq:retry:*", "arq:in-progress:*"]


async def main() -> None:
    redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))

    queued = await redis.zcard("arq:queue")
    print(f"jobs currently in arq:queue: {queued}")

    deleted = 0
    for pattern in ARQ_KEY_PATTERNS:
        async for key in redis.scan_iter(match=pattern):
            await redis.delete(key)
            deleted += 1

    await redis.delete("arq:queue")
    print(f"deleted {deleted} arq key(s) and cleared the queue")
    await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
