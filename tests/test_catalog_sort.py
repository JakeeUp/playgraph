"""Catalog orders: A to Z, newest release first, and most reviewed. All public data."""
from datetime import datetime, timezone

from app.models import Game, Review, User
from tests.test_web_app import web  # noqa: F401  (fixture)


def names(web, **params):  # noqa: F811
    return [game["name"] for game in web.get("/games", params=params).json()["games"]]


def seed(db):
    db.add_all([User(id=1, display_name="One"), User(id=2, display_name="Two"),
                Game(id=1, steam_appid=1, name="Alpha", first_release_date=datetime(2015, 1, 1, tzinfo=timezone.utc)),
                Game(id=2, steam_appid=2, name="Bravo", first_release_date=datetime(2025, 6, 1, tzinfo=timezone.utc)),
                Game(id=3, steam_appid=3, name="Charlie"),
                Game(id=4, steam_appid=4, name="Delta", first_release_date=datetime(2020, 3, 1, tzinfo=timezone.utc))])
    db.flush()
    db.add_all([Review(user_id=1, game_id=4, rating=4), Review(user_id=2, game_id=4, rating=3),
                Review(user_id=1, game_id=3, rating=5)])
    db.commit()


def test_default_is_still_a_to_z(web, db):  # noqa: F811
    seed(db)
    assert names(web) == ["Alpha", "Bravo", "Charlie", "Delta"]


def test_newest_first_puts_undated_games_last(web, db):  # noqa: F811
    seed(db)
    assert names(web, sort="newest") == ["Bravo", "Delta", "Alpha", "Charlie"]


def test_most_reviewed_then_name(web, db):  # noqa: F811
    seed(db)
    assert names(web, sort="reviewed") == ["Delta", "Charlie", "Alpha", "Bravo"]


def test_unknown_sort_is_rejected(web):  # noqa: F811
    assert web.get("/games", params={"sort": "playtime"}).status_code == 422
