from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import settings
from app.database import engine
from app.migrations import require_current_schema
from app.routers import auth, catalog, feed, library, reviews
from app.middleware import SecurityMiddleware
from app.queue_codec import QUEUE_NAME, deserialize, serialize
from app.security_logging import configure_access_logging

configure_access_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    require_current_schema(engine)
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


@app.exception_handler(HTTPException)
async def browser_login_error(request: Request, exc: HTTPException):
    if request.url.path == "/auth/steam/callback" and request.query_params.get("ui") == "1":
        return RedirectResponse("/app?login_error=1", status_code=303)
    return await http_exception_handler(request, exc)

app.include_router(auth.router)
app.include_router(library.router)
app.include_router(reviews.router)
app.include_router(catalog.router)
app.include_router(feed.router)

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")


@app.get("/", include_in_schema=False)
def home():
    return RedirectResponse("/app", status_code=307)


@app.get("/app", include_in_schema=False)
def web_app():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health():
    return {"status": "ok"}
