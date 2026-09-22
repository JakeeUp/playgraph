import os

# app.config validates required settings at import time, so these have to be
# in the environment before anything under app/ gets imported. They are dummy
# values - no test in this suite talks to Steam, Postgres, or Redis.
# Override inherited settings so running tests never opens the developer's database.
os.environ.update(STEAM_API_KEY="test-key", JWT_SECRET="test-only-signing-key-at-least-32-bytes",
                  DATABASE_URL="sqlite://", REDIS_URL="redis://localhost:6379/0",
                  APP_BASE_URL="http://localhost:8000", ENVIRONMENT="development",
                  CLERK_ENABLED="false", CLERK_PUBLISHABLE_KEY="", CLERK_SECRET_KEY="")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base


@pytest.fixture
def db():
    """A throwaway in-memory database, fresh per test.

    StaticPool + a shared connection is needed because each SQLite in-memory
    connection would otherwise get its OWN empty database, so the tables
    created here would be invisible to the session under test.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
