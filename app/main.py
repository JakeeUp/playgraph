from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI

from app.config import settings
from app.database import Base, engine
from app.routers import auth, library, reviews


@asynccontextmanager
async def lifespan(app: FastAPI):
    # One shared arq/Redis connection pool for the app's lifetime, used to
    # enqueue jobs (like the Steam sync) from request handlers. Created here
    # instead of per-request so we're not opening a new Redis connection on
    # every single API call.
    app.state.arq_pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    yield
    await app.state.arq_pool.close()


app = FastAPI(
    title="PlayGraph",
    description="Letterboxd for video games - linked-account playtime, genre "
    "breakdowns, and reviews backed by verified play data.",
    lifespan=lifespan,
    # Keep the bearer token in the docs UI across page reloads. Without this,
    # every --reload restart logs you out of /docs and the token has to be
    # pasted again, which during development is most of the reason anyone
    # gets a surprise 401.
    swagger_ui_parameters={"persistAuthorization": True},
)

# TODO: switch to Alembic migrations before this has real user data - fine
# for local dev to just create tables from the models on startup for now
Base.metadata.create_all(bind=engine)

app.include_router(auth.router)
app.include_router(library.router)
app.include_router(reviews.router)


@app.get("/health")
def health():
    return {"status": "ok"}
