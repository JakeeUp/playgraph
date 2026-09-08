from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import settings
from app.database import Base, engine
from app.routers import auth, library, reviews
from app.middleware import SecurityMiddleware
from app.queue_codec import QUEUE_NAME, deserialize, serialize
from app.security_logging import configure_access_logging

configure_access_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # One shared arq/Redis connection pool for the app's lifetime, used to
    # enqueue jobs (like the Steam sync) from request handlers. Created here
    # instead of per-request so we're not opening a new Redis connection on
    # every single API call.
    app.state.arq_pool = await create_pool(
        RedisSettings.from_dsn(settings.redis_url), job_serializer=serialize,
        job_deserializer=deserialize, default_queue_name=QUEUE_NAME,
    )
    try:
        yield
    finally:
        await app.state.arq_pool.aclose()


app = FastAPI(
    title="PlayGraph",
    description="Letterboxd for video games - linked-account playtime, genre "
    "breakdowns, and reviews backed by verified play data.",
    lifespan=lifespan,
    docs_url="/docs" if settings.environment == "development" else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.environment == "development" else None,
    # Developer tooling must not persist bearer tokens in browser storage.
    swagger_ui_parameters={"persistAuthorization": False},
)

app.add_middleware(TrustedHostMiddleware, allowed_hosts=[urlsplit(settings.app_base_url).hostname],
                   www_redirect=False)
app.add_middleware(SecurityMiddleware)

# TODO: switch to Alembic migrations before this has real user data - fine
# for local dev to just create tables from the models on startup for now
Base.metadata.create_all(bind=engine)

app.include_router(auth.router)
app.include_router(library.router)
app.include_router(reviews.router)

if settings.environment == "development":
    # Hand-testing UI at /app. Never mounted in production, so the CSP
    # relaxation it needs cannot apply there either.
    from app.routers import devui

    app.include_router(devui.router)


@app.get("/health")
def health():
    return {"status": "ok"}
