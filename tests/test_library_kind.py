"""The library and genre graph filter by content kind in SQL, on the latest snapshot."""
from datetime import datetime, timezone

from app.models import Game, PlaytimeSnapshot, User
from app.routers.library import get_genre_breakdown, get_library

EARLY = datetime(2026, 1, 1, tzinfo=timezone.utc)
LATE = datetime(2026, 2, 1, tzinfo=timezone.utc)


def test_kind_filter_keeps_only_matching_games_and_their_newest_snapshot(db):
    user = User(display_name="Tester")
    game = Game(steam_appid=10, name="Hades", genres="Action,RPG")
    tool = Game(steam_appid=431960, name="Wallpaper Engine", genres="Utilities")
    db.add_all([user, game, tool])
    db.flush()
    db.add_all([
        PlaytimeSnapshot(user_id=user.id, game_id=game.id, playtime_minutes=100, captured_at=EARLY),
        PlaytimeSnapshot(user_id=user.id, game_id=game.id, playtime_minutes=300, captured_at=LATE),
        PlaytimeSnapshot(user_id=user.id, game_id=tool.id, playtime_minutes=900, captured_at=LATE),
    ])
    db.flush()

    assert [(e.game.name, e.playtime_minutes) for e in get_library(user=user, db=db, kind="all")] == [
        ("Wallpaper Engine", 900), ("Hades", 300)]
    assert [e.game.name for e in get_library(user=user, db=db, kind="game")] == ["Hades"]
    assert [e.game.name for e in get_library(user=user, db=db, kind="software")] == ["Wallpaper Engine"]
    genres = {g.genre: g.total_minutes for g in get_genre_breakdown(user=user, db=db, kind="game")}
    assert genres == {"Action": 300, "RPG": 300}
