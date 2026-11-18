from app.models import Game, GamePlatform
from tests.test_web_app import web  # noqa: F401


def test_cursor_pages_past_100k_without_lifting_offset_guard(web, db):  # noqa: F811
    db.execute(Game.__table__.insert(), [{"id": key, "name": f"Game {key:06}"}
                                       for key in range(1, 100006)])
    db.commit()
    result = web.get("/games", params={"after": 100000, "limit": 3}).json()
    assert result["total"] == 100005
    assert [game["id"] for game in result["games"]] == [100001, 100002, 100003]
    assert result["next_after"] == 100003
    last = web.get("/games", params={"after": result["next_after"], "limit": 3}).json()
    assert [game["id"] for game in last["games"]] == [100004, 100005]
    assert last["next_after"] is None
    assert web.get("/games?offset=100001").status_code == 422
    search = web.get("/games/search?q=Game%20100005").json()
    assert [game["id"] for game in search["local"]] == [100005]
    assert search["external"] == []


def test_cursor_order_uses_title_then_id_and_keeps_filters(web, db):  # noqa: F811
    db.add_all([Game(id=1, name="Bravo"), Game(id=2, name="Alpha"),
                Game(id=3, name="Bravo"), Game(id=4, name="Charlie"), Game(id=5, name="Bravo other")])
    db.flush()
    db.add_all([GamePlatform(game_id=key, slug="win", name="PC") for key in [1, 2, 3, 4]])
    db.commit()
    first = web.get("/games?limit=2&platform=win").json()
    assert [game["id"] for game in first["games"]] == [2, 1]
    second = web.get("/games?after=1&limit=2&platform=win").json()
    assert [game["id"] for game in second["games"]] == [3, 4]
    assert second["total"] == 4 and second["next_after"] is None
    filtered = web.get("/games?after=1&q=Bravo&platform=win").json()
    assert [game["id"] for game in filtered["games"]] == [3]


def test_invalid_cursor_combinations_and_missing_anchor_are_clear(web):  # noqa: F811
    assert web.get("/games?after=1&sort=newest").status_code == 422
    assert web.get("/games?after=1&offset=1").status_code == 422
    assert web.get("/games?after=0").status_code == 422
    assert web.get("/games?after=2147483648").status_code == 422
    assert web.get("/games?after=123").status_code == 400
