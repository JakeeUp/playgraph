"""Alembic uses the configured engine without writing its URL to configuration or logs."""
from alembic import context
from app.database import Base, engine
from app import models  # noqa: F401 - register model metadata


def migrate(connection):
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("Offline migrations are not supported; use a reviewed database connection.")
elif context.config.attributes.get("connection") is not None:
    migrate(context.config.attributes["connection"])
else:
    with engine.begin() as connection:
        migrate(connection)
