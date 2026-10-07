import os

# app.config validates required settings at import time, so these have to be
# in the environment before anything under app/ gets imported. They are dummy
# values - no test in this suite talks to Steam, Postgres, or Redis.
# Override inherited settings so running tests never opens the developer's database.
os.environ.update(STEAM_API_KEY="test-key", JWT_SECRET="test-only-signing-key-at-least-32-bytes",
                  DATABASE_URL="sqlite://", REDIS_URL="redis://localhost:6379/0",
                  APP_BASE_URL="http://localhost:8000", ENVIRONMENT="development",
                  CLERK_ENABLED="false", CLERK_PUBLISHABLE_KEY="", CLERK_SECRET_KEY="",
                  PSN_NPSSO="", PSN_MIN_REQUEST_INTERVAL="3.0")

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base

# Opt in to running the suite on Postgres by pointing this at an empty,
# throwaway database whose name contains "test". Its tables get dropped.
# The migration tests stay on SQLite, since the wrapper they cover is SQLite only.
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")


@pytest.fixture(scope="session")
def postgres():
    engine = create_engine(TEST_DATABASE_URL, hide_parameters=True)
    if "test" not in (engine.url.database or ""):
        pytest.exit("TEST_DATABASE_URL must name a throwaway database with 'test' in its name", returncode=2)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    # Tests plant rows with small explicit IDs. SQLite carries on from the
    # highest ID in a table, but a Postgres sequence does not know about them,
    # so start every sequence past anything a test plants.
    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            sequence = connection.scalar(text("SELECT pg_get_serial_sequence(:table, 'id')"), {"table": table.name})
            if sequence:
                connection.exec_driver_sql(f"ALTER SEQUENCE {sequence} START WITH 1000 RESTART")
    yield engine
    engine.dispose()


@pytest.fixture
def db(request):
    """A throwaway in-memory database, fresh per test.

    StaticPool + a shared connection is needed because each SQLite in-memory
    connection would otherwise get its OWN empty database, so the tables
    created here would be invisible to the session under test.
    """
    if TEST_DATABASE_URL:
        engine = request.getfixturevalue("postgres")
        session = sessionmaker(bind=engine)()
        try:
            yield session
        finally:
            session.close()
            # Empty every table and restart the ID sequences, so each test
            # starts as blank as a fresh SQLite database would.
            tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
            with engine.begin() as connection:
                connection.exec_driver_sql(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")
        return
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
