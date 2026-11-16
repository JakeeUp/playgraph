"""Use disposable SQLite files to rehearse migration and backup behavior."""
import sqlite3

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

from app.database import Base
from app.migrations import require_current_schema, upgrade_database
from app.models import Comment, Game, LinkedAccount, Notification, Platform, PlaytimeSnapshot, Review, User


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
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM auth_identities").scalar() == 0


def test_profile_image_migration_keeps_existing_data_and_limits_image_slots(database, tmp_path):
    from alembic import command
    from sqlalchemy.exc import IntegrityError
    from app.migrations import migration_config

    with database.begin() as connection:
        command.upgrade(migration_config(connection), "0014_notifications")
        connection.exec_driver_sql("INSERT INTO users (id, display_name) VALUES (1, 'Existing')")
    before = records(database)
    assert upgrade_database(database, tmp_path / "backups").is_file()
    assert records(database) == before
    require_current_schema(database)
    with database.begin() as connection:
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM profile_images").scalar() == 0
        connection.exec_driver_sql("INSERT INTO profile_images (user_id, kind, key, data) VALUES (1, 'avatar', 'key', X'00')")
    for kind in ("avatar", "other"):
        with pytest.raises(IntegrityError), database.begin() as connection:
            connection.execute(text(
                "INSERT INTO profile_images (user_id, kind, key, data) VALUES (1, :kind, 'key', X'00')"), {"kind": kind})
    with database.begin() as connection:
        command.downgrade(migration_config(connection), "0014_notifications")
    assert records(database) == before


def test_notification_migration_preserves_data_and_enforces_targets(database, tmp_path):
    from alembic import command
    from sqlalchemy.exc import IntegrityError
    from app.migrations import migration_config

    with database.begin() as connection:
        command.upgrade(migration_config(connection), "0013_reposts_bookmarks")
        connection.exec_driver_sql("INSERT INTO users (id, display_name) VALUES (1, 'First'), (2, 'Second')")
        connection.exec_driver_sql("INSERT INTO games (id, name) VALUES (1, 'Test game')")
        connection.exec_driver_sql("INSERT INTO reviews (id, user_id, game_id, rating) VALUES (1, 1, 1, 4)")
    before = records(database)
    assert upgrade_database(database, tmp_path / "backups").is_file()
    assert records(database) == before
    require_current_schema(database)
    with database.begin() as connection:
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM notifications").scalar() == 0
        connection.exec_driver_sql("INSERT INTO notifications (recipient_id, actor_id, kind, review_id, created_at) "
                                   "VALUES (1, 2, 'like', 1, '2026-11-15')")
    for actor, kind, review in ((1, "like", 1), (2, "unknown", 1), (2, "like", None), (2, "follow", 1), (2, "comment", 1)):
        with pytest.raises(IntegrityError), database.begin() as connection:
            connection.execute(Notification.__table__.insert().values(
                recipient_id=1, actor_id=actor, kind=kind, review_id=review))
    with database.begin() as connection:
        command.downgrade(migration_config(connection), "0013_reposts_bookmarks")
    assert "notifications" not in inspect(database).get_table_names()
    assert records(database) == before


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


def test_two_steam_links_for_one_user_stop_the_upgrade_and_change_nothing(database, tmp_path):
    from sqlalchemy.exc import IntegrityError

    seed_legacy(database)
    with database.begin() as connection:
        connection.execute(baseline().tables["linked_accounts"].insert(),
                           [{"id": 32, "user_id": 17, "platform": "steam", "platform_user_id": "second"}])
    before = records(database)
    with pytest.raises(IntegrityError):
        upgrade_database(database, tmp_path / "backups")
    assert records(database) == before
    assert "alembic_version" not in inspect(database).get_table_names()


def test_playstation_migration_keeps_steam_data_and_labels_it(database, tmp_path):
    from sqlalchemy.exc import IntegrityError

    seed_legacy(database)
    with database.begin() as connection:
        # A second user with a snapshot but no Steam link keeps a NULL link.
        connection.execute(baseline().tables["users"].insert(), [{"id": 18, "display_name": "Unlinked"}])
        connection.execute(baseline().tables["playtime_snapshots"].insert(),
                           [{"id": 42, "user_id": 18, "game_id": 29, "playtime_minutes": 5}])
    before = records(database)
    upgrade_database(database, tmp_path / "backups")
    require_current_schema(database)
    assert records(database) == before
    with database.connect() as connection:
        snapshots = connection.exec_driver_sql(
            "SELECT id, source, linked_account_id, playtime_minutes FROM playtime_snapshots ORDER BY id").all()
        assert snapshots == [(41, "steam", 31, 120), (42, "steam", None, 5)]
        assert connection.exec_driver_sql("SELECT verified_source FROM reviews WHERE id = 53").scalar() == "steam"
        assert connection.exec_driver_sql(
            "SELECT display_handle, verified_at, verification_method FROM linked_accounts").one() == (None, None, None)
        inspector = inspect(connection)
        assert "ix_games_steam_appid" in {i["name"] for i in inspector.get_indexes("games")}
        assert "ix_playtime_snapshots_user_game_captured" in {
            i["name"] for i in inspector.get_indexes("playtime_snapshots")}
        assert connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE name = 'reviews'").scalar().count("AUTOINCREMENT") == 1
    with Session(database) as db:
        # Games without a Steam app ID, a PSN link and store-ID mappings now fit.
        first, second = Game(name="PS game one"), Game(name="PS game two")
        db.add_all([first, second])
        db.add(LinkedAccount(user_id=17, platform=Platform.psn, platform_user_id="123456789"))
        db.flush()
        from app.models import GameExternalId
        db.add(GameExternalId(game_id=first.id, provider="psn_concept", external_id="100"))
        db.add(PlaytimeSnapshot(user_id=17, game_id=first.id, source="psn", playtime_minutes=None))
        db.commit()
        assert first.content_kind == "game" and first.steam_appid is None
        assert db.query(PlaytimeSnapshot).filter_by(source="psn").one().playtime_minutes is None
        db.add(GameExternalId(game_id=second.id, provider="psn_concept", external_id="100"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        db.add(Game(name="Duplicate Steam app", steam_appid=123))
        with pytest.raises(IntegrityError):
            db.commit()  # steam_appid is still unique


def test_upgrade_from_the_previous_revision_preserves_rows_written_by_it(database, tmp_path):
    from alembic import command
    from app.migrations import migration_config

    with database.begin() as connection:
        command.upgrade(migration_config(connection), "0006_snapshot_lookup_index")
    seed = baseline().tables
    with database.begin() as connection:
        connection.execute(seed["users"].insert(), [{"id": 5, "display_name": "Existing"}])
        connection.execute(seed["games"].insert(), [{"id": 7, "steam_appid": 620, "name": "Portal 2"},
                                                    {"id": 8, "steam_appid": 431960, "name": "Wallpaper Engine"}])
        connection.execute(seed["linked_accounts"].insert(),
                           [{"id": 9, "user_id": 5, "platform": "steam", "platform_user_id": "765"}])
        connection.execute(seed["playtime_snapshots"].insert(),
                           [{"id": 11, "user_id": 5, "game_id": 7, "playtime_minutes": 0},
                            {"id": 12, "user_id": 5, "game_id": 8, "playtime_minutes": 33}])
        connection.exec_driver_sql("UPDATE games SET content_kind = 'software' WHERE id = 8")
    before = records(database)
    backup = upgrade_database(database, tmp_path / "backups")
    assert backup is not None and backup.is_file()
    assert records(database) == before
    with database.connect() as connection:
        assert connection.exec_driver_sql("SELECT id, content_kind FROM games ORDER BY id").all() == [
            (7, "game"), (8, "software")]
        assert connection.exec_driver_sql(
            "SELECT id, linked_account_id, source FROM playtime_snapshots ORDER BY id").all() == [
            (11, 9, "steam"), (12, 9, "steam")]
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []


def test_social_migration_keeps_users_public_and_enforces_pairs(database, tmp_path):
    from alembic import command
    from sqlalchemy.exc import IntegrityError
    from app.migrations import migration_config

    with database.begin() as connection:
        command.upgrade(migration_config(connection), "0009_igdb_hero_art")
    seed = baseline().tables
    with database.begin() as connection:
        connection.execute(seed["users"].insert(), [{"id": 5, "display_name": "Existing"},
                                                    {"id": 6, "display_name": "Second"}])
        connection.execute(seed["games"].insert(), [{"id": 7, "steam_appid": 620, "name": "Portal 2"}])
        connection.execute(seed["reviews"].insert(), [{"id": 9, "user_id": 5, "game_id": 7, "rating": 4}])
    before = records(database)
    backup = upgrade_database(database, tmp_path / "backups")
    assert backup is not None and backup.is_file()
    assert records(database) == before
    require_current_schema(database)
    with database.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT id, bio, profile_visibility FROM users ORDER BY id").all() == [(5, None, "public"), (6, None, "public")]
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    statements = [
        "INSERT INTO follows (follower_id, followee_id) VALUES (5, 5)",
        "INSERT INTO blocks (blocker_id, blocked_id) VALUES (6, 6)",
        "INSERT INTO follows (follower_id, followee_id, status) VALUES (6, 5, 'maybe')",
        "INSERT INTO reports (reporter_id, target_type, target_id, reason) VALUES (5, 'game', 7, 'spam')",
        "INSERT INTO reports (reporter_id, target_type, target_id, reason) VALUES (5, 'review', 9, 'boring')",
    ]
    for statement in statements:
        with pytest.raises(IntegrityError), database.begin() as connection:
            connection.exec_driver_sql(statement)
    with database.begin() as connection:
        connection.exec_driver_sql("INSERT INTO follows (follower_id, followee_id) VALUES (5, 6)")
        connection.exec_driver_sql("INSERT INTO follows (follower_id, followee_id, status) VALUES (6, 5, 'pending')")
        assert connection.exec_driver_sql("SELECT status FROM follows ORDER BY follower_id").scalars().all() == [
            "accepted", "pending"]
        connection.exec_driver_sql("INSERT INTO reports (reporter_id, target_type, target_id, reason) VALUES (6, 'review', 9, 'spam')")
    with pytest.raises(IntegrityError), database.begin() as connection:
        connection.exec_driver_sql("INSERT INTO follows (follower_id, followee_id) VALUES (5, 6)")
    with pytest.raises(IntegrityError), database.begin() as connection:
        connection.exec_driver_sql("INSERT INTO reports (reporter_id, target_type, target_id, reason) VALUES (6, 'review', 9, 'hate')")
    with database.begin() as connection:
        # A closed report no longer blocks a new one from the same person.
        connection.exec_driver_sql("UPDATE reports SET status = 'dismissed'")
        connection.exec_driver_sql("INSERT INTO reports (reporter_id, target_type, target_id, reason) VALUES (6, 'review', 9, 'hate')")


def test_status_migration_adds_an_empty_table_and_enforces_one_status_per_game(database, tmp_path):
    from alembic import command
    from sqlalchemy.exc import IntegrityError
    from app.migrations import migration_config

    with database.begin() as connection:
        command.upgrade(migration_config(connection), "0010_social")
    seed = baseline().tables
    with database.begin() as connection:
        connection.execute(seed["users"].insert(), [{"id": 5, "display_name": "Existing"}])
        connection.execute(seed["games"].insert(), [{"id": 7, "steam_appid": 620, "name": "Portal 2"}])
    before = records(database)
    backup = upgrade_database(database, tmp_path / "backups")
    assert backup is not None and backup.is_file()
    assert records(database) == before
    require_current_schema(database)
    with database.connect() as connection:
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM game_statuses").scalar() == 0
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    insert = "INSERT INTO game_statuses (user_id, game_id, status, updated_at) VALUES (5, 7, '{}', '2026-11-15')"
    with database.begin() as connection:
        connection.exec_driver_sql(insert.format("playing"))
    for statement in (insert.format("paused"), insert.format("beaten")):
        with pytest.raises(IntegrityError), database.begin() as connection:
            connection.exec_driver_sql(statement)


def test_handle_migration_gives_every_player_a_unique_valid_handle(database, tmp_path):
    from alembic import command
    from app import handles
    from app.migrations import migration_config

    with database.begin() as connection:
        command.upgrade(migration_config(connection), "0011_game_statuses")
    seed = baseline().tables
    names = ["Alice", "alice", "Émile Zola", "!!", "12345", "Admin", "Player", "A Very Long Display Name Here"]
    with database.begin() as connection:
        connection.execute(seed["users"].insert(), [{"id": 10 + i, "display_name": n} for i, n in enumerate(names)])
    before = records(database)
    assert upgrade_database(database, tmp_path / "backups") is not None
    assert records(database) == before
    require_current_schema(database)
    with database.connect() as connection:
        rows = dict(connection.exec_driver_sql("SELECT id, handle FROM users ORDER BY id").all())
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    assert rows[10] == "alice" and rows[11] == "alice11" and rows[12] == "emile_zola"
    assert rows[13] == "player" and rows[14] == "player14" and rows[15] == "player15" and rows[16] == "player16"
    assert len(set(rows.values())) == len(rows)
    assert all(handles.problem(handle) is None for handle in rows.values())


def test_repost_migration_keeps_rows_and_widens_report_targets(database, tmp_path):
    from alembic import command
    from sqlalchemy.exc import IntegrityError
    from app.migrations import migration_config

    with database.begin() as connection:
        command.upgrade(migration_config(connection), "0012_handles_likes_favorites")
    seed = baseline().tables
    with database.begin() as connection:
        connection.execute(seed["users"].insert(), [{"id": 5, "display_name": "Existing"},
                                                    {"id": 6, "display_name": "Second"}])
        connection.execute(seed["games"].insert(), [{"id": 7, "steam_appid": 620, "name": "Portal 2"}])
        connection.execute(seed["reviews"].insert(), [{"id": 9, "user_id": 5, "game_id": 7, "rating": 4}])
        connection.exec_driver_sql(
            "INSERT INTO reports (reporter_id, target_type, target_id, reason, status) VALUES (6, 'review', 9, 'spam', 'open')")
    before = records(database)
    assert upgrade_database(database, tmp_path / "backups") is not None
    assert records(database) == before
    require_current_schema(database)
    with database.connect() as connection:
        assert connection.exec_driver_sql("SELECT target_type, target_id FROM reports").all() == [("review", 9)]
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    with database.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO reports (reporter_id, target_type, target_id, reason) VALUES (5, 'repost', 1, 'spam')")
        connection.exec_driver_sql("INSERT INTO reposts (user_id, review_id, quote) VALUES (6, 9, 'Worth playing')")
        connection.exec_driver_sql("INSERT INTO reposts (user_id, review_id, quote) VALUES (6, 9, 'Still worth it')")
        connection.exec_driver_sql("INSERT INTO reposts (user_id, review_id) VALUES (6, 9)")
        connection.exec_driver_sql("INSERT INTO bookmarks (user_id, review_id) VALUES (6, 9)")
    for statement in (
        "INSERT INTO reports (reporter_id, target_type, target_id, reason) VALUES (5, 'game', 7, 'spam')",
        "INSERT INTO reposts (user_id, review_id) VALUES (6, 9)",
        "INSERT INTO reposts (user_id, review_id, quote) VALUES (6, 9, '')",
        "INSERT INTO bookmarks (user_id, review_id) VALUES (6, 9)",
    ):
        with pytest.raises(IntegrityError), database.begin() as connection:
            connection.exec_driver_sql(statement)
