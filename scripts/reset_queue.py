"""Remove this app's JSON sync jobs, only after its worker is stopped."""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arq import create_pool
from arq.connections import RedisSettings

from app.config import settings
from app.queue_codec import QUEUE_NAME, deserialize, serialize


async def main():
    redis = await create_pool(RedisSettings.from_dsn(settings.redis_url),
                              job_serializer=serialize, job_deserializer=deserialize,
                              default_queue_name=QUEUE_NAME)
    try:
        deleted = 0
        for prefix in ("arq:job:", "arq:result:", "arq:retry:", "arq:in-progress:"):
            async for key in redis.scan_iter(match=prefix + "sync-json-user-*"):
                deleted += await redis.delete(key)
        await redis.delete(QUEUE_NAME)
        print(f"Removed {deleted} PlayGraph sync keys and its JSON queue")
    finally:
        await redis.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="Confirm the worker is stopped and jobs may be deleted")
    if not parser.parse_args().yes:
        parser.error("Stop the worker, then pass --yes to discard its queued jobs")
    asyncio.run(main())
