"""Copy a PlayGraph SQLite database into a new, empty Postgres database.

Stop the API and the worker first. The SQLite file is opened read only and
never changed. The Postgres database gets the current schema from the
migrations, then every row with its original ID, in one transaction that
only commits once every table matches the source. If anything fails, the
Postgres database is left empty.

    python -m app.copy_to_postgres playgraph.db postgresql+psycopg://user@host/playgraph

Add --rehearse to copy and verify everything, then roll it all back.
"""
import argparse
from datetime import timezone
from pathlib import Path

from alembic import command
from sqlalchemy import DateTime, create_engine, func, inspect, select, text

from app import models  # noqa: F401  (registers every table on Base.metadata)
from app.database import Base
from app.migrations import migration_config, require_current_schema

BATCH = 1000


def as_utc(value):
    # SQLite hands times back without a zone, stored as UTC. Postgres would
    # read a zoneless time in its own session zone, so the zone is made explicit.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def timestamp_columns(table) -> set[str]:
    return {column.name for column in table.columns if isinstance(column.type, DateTime)}


def normalized(row, stamps: set[str]):
    return tuple(as_utc(value) if name in stamps and value is not None else value
                 for name, value in row._mapping.items())


def copy_table(source, target, table):
    stamps = timestamp_columns(table)
    texts = [column.name for column in table.columns if column.type.python_type is str]
    result = source.execute(select(table).order_by(*table.primary_key.columns))
    while batch := result.fetchmany(BATCH):
        rows = []
        for row in batch:
            values = dict(row._mapping)
            for name in stamps:
                if values[name] is not None:
                    values[name] = as_utc(values[name])
            for name in texts:
                if isinstance(values[name], str) and "\x00" in values[name]:
                    # Postgres cannot store NUL in text. Say where, rather than fail mid-copy.
                    raise RuntimeError(f"{table.name} row {values.get('id')} has a NUL character in {name}. "
                                       "Fix that row in SQLite, then run this again.")
            rows.append(values)
        target.execute(table.insert(), rows)


def highest_ids(source):
    # A table created with AUTOINCREMENT remembers IDs of deleted rows, so
    # they are never handed out again. Postgres has to start past those too.
    # SQLAlchemy's table listing leaves out SQLite's own tables, so ask directly.
    if not source.scalar(text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sqlite_sequence'")):
        return {}
    return dict(source.execute(text("SELECT name, seq FROM sqlite_sequence")).all())


def reset_sequences(source, target):
    remembered = highest_ids(source)
    for table in Base.metadata.sorted_tables:
        sequence = target.scalar(text("SELECT pg_get_serial_sequence(:table, 'id')"), {"table": table.name})
        if not sequence:
            continue
        highest = max(target.scalar(select(func.max(table.c.id))) or 0, remembered.get(table.name) or 0)
        if highest:
            target.execute(text("SELECT setval(:sequence, :value, true)"), {"sequence": sequence, "value": highest})


def verify(source, target):
    counts = {}
    for table in Base.metadata.sorted_tables:
        query = select(table).order_by(*table.primary_key.columns)
        stamps = timestamp_columns(table)  # once per table, not once per row
        expected, actual = source.execute(query), target.execute(query)
        count = 0
        while True:
            left, right = expected.fetchmany(BATCH), actual.fetchmany(BATCH)
            if [normalized(row, stamps) for row in left] != [normalized(row, stamps) for row in right]:
                raise RuntimeError(f"{table.name} does not match the source after copying. Nothing was committed.")
            if not left:
                break
            count += len(left)
        counts[table.name] = count
    return counts


def copy_database(sqlite_file, postgres_url, *, rehearse=False):
    """Copy, verify and commit (or roll back when rehearsing). Returns row counts per table."""
    path = Path(sqlite_file).resolve()
    if not path.is_file():
        raise RuntimeError(f"No SQLite database at {path}")
    source_engine = create_engine(f"sqlite:///file:{path.as_posix()}?mode=ro&uri=true")
    target_engine = create_engine(postgres_url, hide_parameters=True)
    try:
        if target_engine.dialect.name != "postgresql":
            raise RuntimeError("The target must be a Postgres URL, such as postgresql+psycopg://...")
        try:
            require_current_schema(source_engine)
        except RuntimeError:
            raise RuntimeError("Upgrade the SQLite database first: python -m app.migrations upgrade") from None
        with source_engine.connect() as source, target_engine.connect() as target:
            transaction = target.begin()
            try:
                if inspect(target).get_table_names():
                    raise RuntimeError(f"{target_engine.url.database} already has tables. Copy into an empty database.")
                command.upgrade(migration_config(target), "head")
                for table in Base.metadata.sorted_tables:
                    copy_table(source, target, table)
                reset_sequences(source, target)
                counts = verify(source, target)
            except BaseException:
                transaction.rollback()
                raise
            if rehearse:
                transaction.rollback()
            else:
                transaction.commit()
        return counts
    finally:
        source_engine.dispose()
        target_engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sqlite_file")
    parser.add_argument("postgres_url")
    parser.add_argument("--rehearse", action="store_true", help="copy and verify, then roll back")
    args = parser.parse_args()
    try:
        counts = copy_database(args.sqlite_file, args.postgres_url, rehearse=args.rehearse)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None
    for name, count in counts.items():
        print(f"{name:>20}  {count}")
    print("Rehearsal passed. Nothing was kept." if args.rehearse else "Copied and verified.")


if __name__ == "__main__":
    main()
