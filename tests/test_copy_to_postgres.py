from datetime import datetime, timedelta, timezone
import os

from alembic import command
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.copy_to_postgres import as_utc, copy_database, copy_table, highest_ids
from app.migrations import migration_config
from app.models import Comment, Game, Review, User

CLOCK_CHANGE_DAY = datetime(2026, 11, 1, 8, 30)


def sqlite_source(tmp_path):
    """A migrated SQLite database with one live review and one deleted one."""
    engine = create_engine("sqlite:///" + (tmp_path / "source.db").as_posix())
    with engine.begin() as connection:
        command.upgrade(migration_config(connection), "head")
        connection.execute(User.__table__.insert(), [{"id": 1, "display_name": "Author", "created_at": CLOCK_CHANGE_DAY}])
        connection.execute(Game.__table__.insert(), [{"id": 1, "steam_appid": 10, "name": "Game"},
                                                     {"id": 2, "steam_appid": 20, "name": "Other"}])
        connection.execute(Review.__table__.insert(), [
            {"id": 1, "user_id": 1, "game_id": 1, "rating": 4.5, "body": "Keep", "created_at": CLOCK_CHANGE_DAY},
            {"id": 2, "user_id": 1, "game_id": 2, "rating": 2.0, "body": "Gone", "created_at": CLOCK_CHANGE_DAY}])
        connection.execute(Review.__table__.delete().where(Review.__table__.c.id == 2))
    return engine


def test_ids_of_deleted_reviews_are_remembered_for_the_copy(tmp_path):
    engine = sqlite_source(tmp_path)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT max(id) FROM reviews")) == 1
        assert highest_ids(connection)["reviews"] == 2
    engine.dispose()


def test_times_keep_their_instant():
    assert as_utc(datetime(2026, 1, 1)) == datetime(2026, 1, 1, tzinfo=timezone.utc)
    eastern = datetime(2026, 11, 1, 3, 30, tzinfo=timezone(timedelta(hours=-5)))
    assert as_utc(eastern) == datetime(2026, 11, 1, 8, 30, tzinfo=timezone.utc)


def test_text_postgres_cannot_store_stops_the_copy_before_writing(tmp_path):
    engine = sqlite_source(tmp_path)
    with engine.begin() as connection:
        connection.execute(Comment.__table__.insert(), [{"id": 1, "review_id": 1, "user_id": 1, "body": "a\x00b"}])
    # The check runs before anything is written, so there is no target to give.
    with engine.connect() as connection, pytest.raises(RuntimeError, match="comments row 1 has a NUL"):
        copy_table(connection, None, Comment.__table__)
    engine.dispose()


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="set TEST_DATABASE_URL to run against Postgres")
def test_copy_into_postgres_keeps_ids_and_times_and_only_fills_an_empty_database(tmp_path):
    url = make_url(os.environ["TEST_DATABASE_URL"])
    target = url.set(database=url.database + "_copy")
    target_url = target.render_as_string(hide_password=False)
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{target.database}"')
        connection.exec_driver_sql(f'CREATE DATABASE "{target.database}"')
    check = create_engine(target)
    try:
        sqlite_source(tmp_path).dispose()
        source = tmp_path / "source.db"
        assert copy_database(source, target_url, rehearse=True)["reviews"] == 1
        with check.connect() as connection:
            assert inspect(connection).get_table_names() == []

        counts = copy_database(source, target_url)
        assert counts == {"users": 1, "auth_identities": 0, "linked_accounts": 0, "games": 2,
                          "playtime_snapshots": 0, "reviews": 1, "comments": 0}
        with check.connect() as connection:
            # Review 2 was deleted in SQLite, so its ID is never handed out again.
            assert connection.scalar(text("SELECT nextval('reviews_id_seq')")) == 3
            assert connection.scalar(text("SELECT nextval('games_id_seq')")) == 3
            stored = connection.scalar(text("SELECT created_at FROM users WHERE id = 1"))
            assert stored == CLOCK_CHANGE_DAY.replace(tzinfo=timezone.utc)
        with pytest.raises(RuntimeError, match="already has tables"):
            copy_database(source, target_url)
    finally:
        check.dispose()
        with admin.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{target.database}"')
        admin.dispose()
