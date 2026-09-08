"""CI uses an isolated localhost Redis. Never point this at a hosted database."""

import asyncio
import os
import secrets
from urllib.parse import urlsplit

import pytest
from arq import create_pool
from arq.connections import RedisSettings
from arq.worker import Worker
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from app.queue_codec import deserialize, serialize
from app.security import rate_limit, state_key


@pytest.mark.asyncio
@pytest.mark.skipif(not os.environ.get("TEST_REDIS_URL"), reason="Needs isolated local Redis; runs in CI")
async def test_atomic_security_state_and_json_worker():
    url = os.environ["TEST_REDIS_URL"]
    parsed = urlsplit(url)
    assert parsed.scheme == "redis" and parsed.hostname in {"localhost", "127.0.0.1"}
    identity = secrets.token_urlsafe(16)
    queue = "playgraph:test:queue:" + identity
    job_id = "playgraph-test-" + identity
    redis = await create_pool(RedisSettings.from_dsn(url), job_serializer=serialize,
                              job_deserializer=deserialize, default_queue_name=queue)
    app = FastAPI()
    app.state.arq_pool = redis
    request = Request({"type": "http", "app": app, "headers": []})
    login_key = state_key("test-login", identity)
    rate_key = state_key("rate:test", identity)
    worker = None
    try:
        await redis.set(login_key, "binding", ex=60)
        consumed = await asyncio.gather(redis.getdel(login_key), redis.getdel(login_key))
        assert consumed.count(b"binding") == 1 and consumed.count(None) == 1
        await rate_limit(request, "test", identity, 1, 60)
        with pytest.raises(HTTPException) as error:
            await rate_limit(request, "test", identity, 1, 60)
        assert error.value.status_code == 429
        assert 0 < await redis.ttl(rate_key) <= 60

        async def echo(ctx, value):
            return {"value": value}

        job = await redis.enqueue_job("echo", 7, _job_id=job_id)
        worker = Worker([echo], redis_pool=redis, queue_name=queue, burst=True,
                         job_serializer=serialize, job_deserializer=deserialize,
                         handle_signals=False)
        await worker.async_run()
        assert await job.result(timeout=1) == {"value": 7}
    finally:
        await redis.delete(login_key, rate_key, queue, queue + ":health-check",
                           "arq:job:" + job_id, "arq:result:" + job_id,
                           "arq:retry:" + job_id, "arq:in-progress:" + job_id)
        if worker:
            await worker.close()
        else:
            await redis.aclose()
