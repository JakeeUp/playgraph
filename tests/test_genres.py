"""Tests for the genre breakdown that feeds the profile graph.

The behaviour being pinned down here is the multi-genre decision: a game's
full playtime counts toward every genre it carries, so the totals do not sum
to total playtime. That is deliberate, and it is exactly the kind of thing
someone would "fix" later without realising it was a choice, so it gets a
test that fails loudly if anyone changes it.
"""

from datetime import datetime, timedelta, timezone

from app.models import Game, PlaytimeSnapshot, User
from app.routers.library import get_genre_breakdown

NOW = datetime(2026, 9, 7, 12, 0, 0, tzinfo=timezone.utc)


def _user(db):
    user = User(display_name="Tester")
    db.add(user)
    db.flush()
    return user


def _game(db, appid, name, genres):
    game = Game(steam_appid=appid, name=name, genres=genres)
    db.add(game)
    db.flush()
    return game


def _snap(db, user, game, minutes, captured_at=NOW):
    db.add(
        PlaytimeSnapshot(
            user_id=user.id,
            game_id=game.id,
            playtime_minutes=minutes,
            captured_at=captured_at,
        )
    )
    db.flush()


def test_playtime_counts_fully_toward_every_genre(db):
    user = _user(db)
    game = _game(db, 1, "Hades", "Action,Indie,RPG")
    _snap(db, user, game, 600)

    rows = {r.genre: r for r in get_genre_breakdown(user=user, db=db)}

    # Full credit, not 200 each. This is the decision under test.
    assert rows["Action"].total_minutes == 600
    assert rows["Indie"].total_minutes == 600
    assert rows["RPG"].total_minutes == 600

    # And the sum across genres exceeds real playtime, on purpose.
    assert sum(r.total_minutes for r in rows.values()) == 1800


def test_totals_accumulate_across_games_sharing_a_genre(db):
    user = _user(db)
    a = _game(db, 1, "Hades", "Action,Indie")
    b = _game(db, 2, "Celeste", "Indie,Platformer")
    _snap(db, user, a, 600)
    _snap(db, user, b, 400)

    rows = {r.genre: r for r in get_genre_breakdown(user=user, db=db)}

    assert rows["Indie"].total_minutes == 1000
    assert rows["Indie"].game_count == 2
    assert rows["Action"].total_minutes == 600
    assert rows["Action"].game_count == 1


def test_only_the_latest_snapshot_per_game_is_counted(db):
    """Snapshots are append-only, so a re-sync adds a row rather than
    replacing one. Counting both would double-count that game."""
    user = _user(db)
    game = _game(db, 1, "Hades", "Action")
    _snap(db, user, game, 300, captured_at=NOW - timedelta(days=7))
    _snap(db, user, game, 600, captured_at=NOW)

    rows = get_genre_breakdown(user=user, db=db)

    assert len(rows) == 1
    assert rows[0].total_minutes == 600  # newest wins, not 900 and not 300
    assert rows[0].game_count == 1


def test_games_with_no_genre_data_are_skipped(db):
    """Empty string means the store was asked and had nothing (delisted app,
    tool, playtest). None means it has not been looked up yet. Neither should
    produce a phantom bucket."""
    user = _user(db)
    checked_and_empty = _game(db, 1, "Some Tool", "")
    never_checked = _game(db, 2, "Unknown App", None)
    real = _game(db, 3, "Hades", "Action")
    _snap(db, user, checked_and_empty, 100)
    _snap(db, user, never_checked, 100)
    _snap(db, user, real, 50)

    rows = get_genre_breakdown(user=user, db=db)

    assert [r.genre for r in rows] == ["Action"]
    assert rows[0].total_minutes == 50


def test_results_are_sorted_by_playtime_descending(db):
    user = _user(db)
    _snap(db, user, _game(db, 1, "A", "Puzzle"), 100)
    _snap(db, user, _game(db, 2, "B", "Shooter"), 900)
    _snap(db, user, _game(db, 3, "C", "Racing"), 500)

    rows = get_genre_breakdown(user=user, db=db)

    assert [r.genre for r in rows] == ["Shooter", "Racing", "Puzzle"]


def test_one_users_playtime_does_not_leak_into_another(db):
    user = _user(db)
    other = User(display_name="Someone Else")
    db.add(other)
    db.flush()

    game = _game(db, 1, "Hades", "Action")
    _snap(db, user, game, 600)
    _snap(db, other, game, 9999)

    rows = get_genre_breakdown(user=user, db=db)

    assert len(rows) == 1
    assert rows[0].total_minutes == 600
