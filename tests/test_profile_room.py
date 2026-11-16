"""Public profile shelves reuse statuses and keep the profile access boundary."""
from app.models import Game, GameStatus
from tests.test_social import ALICE, BOB, api, block, follow, store, who  # noqa: F401


def test_room_uses_existing_statuses_without_exposing_the_imported_library(api, db):
    db.add_all([GameStatus(user_id=ALICE, game_id=1, status="playing"),
                GameStatus(user_id=ALICE, game_id=2, status="want_to_play")])
    db.commit()
    shown = api.get("/users/1").json()
    assert [game["id"] for game in shown["room"]["playing"]] == [1]
    assert [game["id"] for game in shown["room"]["want_to_play"]] == [2]
    assert shown["room"]["completed"] == []
    assert "library" not in shown and "playtime_snapshots" not in shown


def test_room_is_bounded_and_excludes_software(api, db):
    for i in range(20, 34):
        db.add(Game(id=i, steam_appid=i + 100, name=f"Game {i}"))
    db.add(Game(id=35, steam_appid=431960, name="Wallpaper Engine", genres="Utilities"))
    db.flush()
    for i in [*range(20, 34), 35]:
        db.add(GameStatus(user_id=ALICE, game_id=i, status="completed"))
    db.commit()
    shelf = api.get("/users/1").json()["room"]["completed"]
    assert len(shelf) == 12 and all(game["content_kind"] == "game" for game in shelf)


def test_room_is_hidden_on_limited_private_and_blocked_profiles(api):
    headers = who(api, ALICE)
    api.patch("/me/profile", headers=headers, json={"profile_visibility": "followers"})
    assert "room" not in api.get("/users/1").json()
    assert "room" in api.get("/users/1", headers=headers).json()
    api.patch("/me/profile", headers=headers, json={"profile_visibility": "private"})
    assert api.get("/users/1", headers=who(api, BOB)).status_code == 404
    api.patch("/me/profile", headers=headers, json={"profile_visibility": "public"})
    block(api, BOB, ALICE)
    stub = api.get("/users/1", headers=who(api, BOB)).json()
    assert "room" not in stub and "avatar_url" not in stub and "cover_url" not in stub
