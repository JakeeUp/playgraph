"""Explicit, backed-up SQLite migration entry point for local development."""
import argparse
from pathlib import Path
import sqlite3
import tempfile

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

ROOT = Path(__file__).resolve().parent.parent
BASELINE = "0001_baseline"


def migration_config(connection=None):
    config = Config(str(ROOT / "alembic.ini"))
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def require_current_schema(engine):
    expected = set(ScriptDirectory.from_config(migration_config()).get_heads())
    with engine.connect() as connection:
        actual = set(MigrationContext.configure(connection).get_current_heads())
    if actual != expected:
        raise RuntimeError("Database upgrade required. Stop API/worker, then run: python -m app.migrations upgrade")


def sqlite_backup(engine, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    # mkstemp avoids overwriting an earlier backup and restricts file access on POSIX.
    import os
    descriptor, name = tempfile.mkstemp(prefix="playgraph-before-upgrade-", suffix=".db", dir=directory)
    os.close(descriptor)
    raw = engine.raw_connection()
    try:
        with sqlite3.connect(name) as destination:
            raw.driver_connection.backup(destination)
            if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Backup integrity check failed; migration was not started.")
    finally:
        raw.close()
    return Path(name)


def upgrade_database(engine, backup_directory):
    if engine.dialect.name != "sqlite" or not engine.url.database or engine.url.database == ":memory:":
        raise RuntimeError("This backup wrapper requires file-backed SQLite. Rehearse PostgreSQL migrations separately.")
    with engine.connect() as connection:
        # Block competing SQLite writers until backup, validation and migration finish.
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        config = migration_config(connection)
        scripts = ScriptDirectory.from_config(config)
        heads = set(MigrationContext.configure(connection).get_current_heads())
        if heads == set(scripts.get_heads()):
            connection.rollback()
            return None
        tables = set(inspect(connection).get_table_names()) - {"alembic_version"}
        backup = sqlite_backup(engine, backup_directory) if tables else None
        if not heads and tables:
            baseline = scripts.get_revision(BASELINE).module.schema()
            differences = compare_metadata(MigrationContext.configure(connection), baseline)
            if differences or tables != set(baseline.tables):
                raise RuntimeError("Unversioned schema differs from the known baseline; no adoption performed.")
            command.stamp(config, BASELINE)
        command.upgrade(config, "head")
        connection.commit()
        return backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["check", "upgrade"])
    args = parser.parse_args()
    from app.database import engine
    if args.action == "check":
        require_current_schema(engine)
        print("Database schema is current.")
    else:
        backup = upgrade_database(engine, ROOT / ".backups")
        print("Database schema is current. Existing record IDs are preserved.")
        if backup:
            print("A verified pre-upgrade backup was saved under .backups/.")


if __name__ == "__main__":
    main()
