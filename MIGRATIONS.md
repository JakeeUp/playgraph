# Database migrations

Phase 2A replaces schema creation at API import time with explicit Alembic
revisions. API and worker startup check the revision without modifying tables.
The first revision freezes the existing six-table schema; it adds no accounts,
passwords or MFA settings. Existing internal IDs remain the owners of their data.

## Local SQLite upgrade

1. Let any Steam sync finish, then stop the API and worker.
2. In the project directory, run `python -m app.migrations upgrade`.
3. Run `python -m app.migrations check`, then start `dev.bat`.

The wrapper locks competing SQLite writers, makes a backup using SQLite's backup
API, verifies that backup, and runs the migration. It does not use a raw filesystem
copy of a live WAL database. Backups are stored under `.backups/`, excluded from Git.
They contain private user data and need the same access controls as the database.

An existing database without an Alembic revision is adopted only if its tables,
columns, types, foreign keys, indexes and unique constraints match the frozen
baseline. A different schema fails for review instead of being silently stamped.
Empty installations are created from the revision. Repeating an already-current
upgrade is a no-op. Database URLs and credentials are not put in alembic.ini.

Do not use `alembic stamp head` to bypass a mismatch. The baseline is deliberately
not droppable through downgrade. PostgreSQL upgrade/backup procedures need a
separate rehearsal before deployment; the local backup wrapper accepts SQLite only.

## Recovery and future revisions

Stop all database writers before recovery. Preserve the failed database and any
WAL/SHM files for diagnosis. Restore a selected, verified backup to a separate file,
check its integrity and record counts, and point DATABASE_URL at that recovered file
before restarting. An unversioned backup must pass the same adoption checks again.
Never overwrite the only backup or copy a `.db` over a running SQLite connection.

Future schema changes need a new reviewed revision and tests against the previous
schema. Keep baseline definitions frozen instead of importing evolving ORM models
inside historical revisions. Run empty-install, populated-upgrade and failure-path
tests before applying changes to the owner's database.

The implementation follows Alembic's documented connection-sharing and schema
versioning interfaces: [Alembic cookbook](https://alembic.sqlalchemy.org/en/latest/cookbook.html#sharing-a-connection-across-one-or-more-programmatic-migration-commands).
