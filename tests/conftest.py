import os

# app.config validates required settings at import time, so these have to be
# in the environment before anything under app/ gets imported. They are dummy
# values - no test in this suite talks to Steam, Postgres, or Redis.
os.environ.setdefault("STEAM_API_KEY", "test-key")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

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
