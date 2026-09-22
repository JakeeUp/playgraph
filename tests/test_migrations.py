"""Use disposable SQLite files to rehearse migration and backup behavior."""
import sqlite3

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from app.database import Base
from app.migrations import require_current_schema, upgrade_database
from app.models import Comment, Game, LinkedAccount, Platform, PlaytimeSnapshot, Review, User


@pytest.fixture
def database(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "test.db").as_posix())
    yield engine
    engine.dispose()


def baseline():
    from app.migrations import BASELINE, migration_config
    from alembic.script import ScriptDirectory
    return ScriptDirectory.from_config(migration_config()).get_revision(BASELINE).module.schema()


def seed_legacy(engine):
    # A legacy fixture must use the frozen schema, not today's evolving model,
    # for both the tables and the rows: the models gain columns over time.
    frozen = baseline()
    frozen.create_all(engine)
    tables = frozen.tables
    with engine.begin() as connection:
        for name, row in [
            ("users", {"id": 17, "display_name": "Synthetic player"}),
            ("games", {"id": 29, "steam_appid": 123, "name": "Synthetic game"}),
            ("linked_accounts", {"id": 31, "user_id": 17, "platform": "steam", "platform_user_id": "synthetic"}),
            ("playtime_snapshots", {"id": 41, "user_id": 17, "game_id": 29, "playtime_minutes": 120}),
            ("reviews", {"id": 53, "user_id": 17, "game_id": 29, "rating": 4.5, "body": "Keep this review",
                         "verified_playtime_minutes": 120}),
            ("comments", {"id": 67, "review_id": 53, "user_id": 17, "body": "Keep this comment"}),
        ]:
            connection.execute(tables[name].insert(), [row])


def records(engine):
    """Every baseline column of every row: what any migration must preserve."""
    with engine.connect() as connection:
        return {table.name: connection.execute(select(table).order_by(table.c.id)).all()
                for table in baseline().sorted_tables}


def test_empty_install_matches_application_schema(database, tmp_path):
    assert upgrade_database(database, tmp_path / "backups") is None
    require_current_schema(database)
    with database.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []


def test_review_id_migration_preserves_rows_and_never_reuses_deleted_link(database, tmp_path):
    from alembic import command
    from app.migrations import BASELINE, migration_config

    seed_legacy(database)
    with database.begin() as connection:
        command.stamp(migration_config(connection), BASELINE)
    before = records(database)
    backup = upgrade_database(database, tmp_path / "backups")
    assert backup.is_file()
    assert records(database) == before
    require_current_schema(database)
    with Session(database) as db:
        db.query(Comment).delete()
        db.query(Review).delete()
        db.commit()
        row = Review(user_id=17, game_id=29, rating=5)
        db.add(row)
        db.commit()
        assert row.id > 53


def test_content_kind_is_backfilled_from_the_frozen_classifier(database, tmp_path):
    seed_legacy(database)
    with database.begin() as connection:
        connection.execute(baseline().tables["games"].insert(), [
            {"id": 30, "steam_appid": 431960, "name": "Wallpaper Engine", "genres": "Casual,Indie"},
            {"id": 31, "steam_appid": 555, "name": "A tool", "genres": "Utilities, Design & Illustration"},
            {"id": 32, "steam_appid": 556, "name": "A game", "genres": "Action,Indie"},
        ])
    before = records(database)
    upgrade_database(database, tmp_path / "backups")
    assert records(database) == before
    with database.connect() as connection:
        kinds = dict(connection.exec_driver_sql("SELECT id, content_kind FROM games ORDER BY id").all())
        indexes = {index["name"] for index in inspect(connection).get_indexes("games")}
    assert kinds == {29: "game", 30: "software", 31: "software", 32: "game"}
    assert "ix_games_content_kind" in indexes


def test_legacy_adoption_preserves_all_records_and_restorable_backup(database, tmp_path):
    seed_legacy(database)
    before = records(database)
    backup = upgrade_database(database, tmp_path / "backups")
    require_current_schema(database)
    assert records(database) == before
    restored = create_engine("sqlite:///" + backup.as_posix())
    try:
        assert records(restored) == before
        assert "alembic_version" not in inspect(restored).get_table_names()
    finally:
        restored.dispose()
    with sqlite3.connect(backup) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert upgrade_database(database, tmp_path / "backups") is None
    assert len(list((tmp_path / "backups").glob("*.db"))) == 1


def test_unknown_legacy_schema_is_not_silently_stamped(database, tmp_path):
    with database.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE users (id INTEGER PRIMARY KEY, unexpected TEXT)")
        connection.exec_driver_sql("INSERT INTO users VALUES (1, 'keep')")
    with pytest.raises(RuntimeError, match="differs"):
        upgrade_database(database, tmp_path / "backups")
    assert inspect(database).get_table_names() == ["users"]
    with database.connect() as connection:
        assert connection.exec_driver_sql("SELECT unexpected FROM users").scalar() == "keep"


def test_schema_guard_never_creates_or_stamps_tables(database):
    with pytest.raises(RuntimeError, match="upgrade required"):
        require_current_schema(database)
    assert inspect(database).get_table_names() == []
    seed_legacy(database)
    with pytest.raises(RuntimeError, match="upgrade required"):
        require_current_schema(database)
    assert "alembic_version" not in inspect(database).get_table_names()


def test_failed_migration_rolls_back_adoption_and_preserves_data(database, tmp_path, monkeypatch):
    seed_legacy(database)
    before = records(database)
    def fail(*args, **kwargs):
        raise RuntimeError("Synthetic migration failure")
    monkeypatch.setattr("app.migrations.command.upgrade", fail)
    with pytest.raises(RuntimeError, match="Synthetic"):
        upgrade_database(database, tmp_path / "backups")
    assert records(database) == before
    assert "alembic_version" not in inspect(database).get_table_names()
    assert len(list((tmp_path / "backups").glob("*.db"))) == 1


def test_backup_failure_prevents_adoption(database, tmp_path, monkeypatch):
    seed_legacy(database)
    def fail(*args, **kwargs):
        raise OSError("Synthetic backup failure")
    monkeypatch.setattr("app.migrations.sqlite_backup", fail)
    with pytest.raises(OSError, match="backup failure"):
        upgrade_database(database, tmp_path / "backups")
    assert "alembic_version" not in inspect(database).get_table_names()
