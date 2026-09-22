from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

_database_url = settings.database_url.get_secret_value()
_is_sqlite = _database_url.startswith("sqlite")

# SQLite needs check_same_thread disabled because FastAPI serves requests
# from a thread pool, and SQLite otherwise refuses to reuse a connection
# across threads. Postgres neither needs nor accepts this argument, so it is
# only passed for SQLite. Local dev can therefore run against a plain file
# with no database server, while production points at Postgres, with no code
# change either way.
connect_args = {"check_same_thread": False} if _is_sqlite else {}

engine = create_engine(_database_url, connect_args=connect_args, hide_parameters=True)


if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):
        """Make SQLite survive the API and the worker writing at once.

        Out of the box SQLite uses a rollback journal, where a writer locks
        the whole database and any other writer fails IMMEDIATELY with
        "database is locked" rather than waiting. With the API process and
        the arq worker both writing, that is easy to hit.

        WAL (write-ahead logging) lets readers keep reading while a write is
        in progress, and busy_timeout tells SQLite to wait and retry for up
        to 10 seconds for a write lock instead of giving up instantly.

        Neither pragma is needed on Postgres, which handles concurrent
        writers properly. This is a local-dev accommodation, not a design.
        """
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """FastAPI dependency - yields a DB session per-request and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
