"""Play statuses: set by hand, private to the profile rules, never touched by a sync.

Alice (1) and Bob (2) are synthetic players. Game 1 is in Alice's Steam
library; game 2 is a catalog game she doesn't own.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import worker
from app.config import settings
from app.database import get_db
from app.models import Game, GameStatus, LinkedAccount, Platform, Review, User
from app.routers import library, social
from tests.security_helpers import MemoryRedis, auth_headers
from tests.test_social import cookie_session
from tests.test_steam_sync import STEAM_ID, FakeSteam, setup  # noqa: F401 (setup is a fixture)

ALICE, BOB = 1, 2


@pytest.fixture
def api(db):
    store = MemoryRedis()
    store.now = 3600 * 1000 + 10
    app = FastAPI()
    for router in (library.router, social.router):
        app.include_router(router)
    app.state.arq_pool = store
    app.dependency_overrides[get_db] = lambda: db
    db.add_all([User(id=ALICE, display_name="Alice"), User(id=BOB, display_name="Bob"),
                Game(id=1, steam_appid=10, name="Cold Harbor"), Game(id=2, steam_appid=20, name="Dune Run")])
    db.flush()
    # Alice has posted, so her profile can be found.
    db.add(Review(id=11, user_id=ALICE, game_id=1, rating=4))
    db.commit()
    with TestClient(app, base_url="http://localhost:8000") as client:
        client.store = store
        yield client


def who(api, user_id):
    return auth_headers(api.store, user_id)


def put(api, user_id, game_id, status):
    return api.put(f"/me/games/{game_id}/status", json={"status": status}, headers=who(api, user_id))


def test_set_change_and_clear_a_status(api):
    first = put(api, ALICE, 1, "playing")
    assert first.status_code == 200
    assert first.json()["status"] == "playing" and first.json()["game"]["name"] == "Cold Harbor"
    assert first.json()["completed_at"] is None

    assert put(api, ALICE, 1, "completed").json()["completed_at"] is not None
    rows = api.get("/me/statuses", headers=who(api, ALICE)).json()
    assert [(row["game"]["id"], row["status"]) for row in rows] == [(1, "completed")]

    assert api.delete("/me/games/1/status", headers=who(api, ALICE)).status_code == 204
    assert api.get("/me/statuses", headers=who(api, ALICE)).json() == []
    # Clearing twice is fine.
    assert api.delete("/me/games/1/status", headers=who(api, ALICE)).status_code == 204


def test_completed_date_is_kept_until_the_status_moves_away(api, db):
    stamped = put(api, ALICE, 1, "completed").json()["completed_at"]
    assert put(api, ALICE, 1, "completed").json()["completed_at"] == stamped
    assert put(api, ALICE, 1, "paused").json()["completed_at"] is None


def test_games_you_dont_own_can_go_on_the_backlog(api):
    assert put(api, ALICE, 2, "want_to_play").status_code == 200
    backlog = api.get("/me/statuses?status=want_to_play", headers=who(api, ALICE)).json()
    assert [row["game"]["name"] for row in backlog] == ["Dune Run"]
    assert api.get("/me/statuses?status=playing", headers=who(api, ALICE)).json() == []


@pytest.mark.parametrize("body", [{"status": "beaten"}, {"status": ""}, {}, {"status": "playing", "extra": 1}])
def test_only_the_five_statuses_are_accepted(api, body):
    response = api.put("/me/games/1/status", json=body, headers=who(api, ALICE))
    assert response.status_code == 422


def test_unknown_game_and_bad_ids(api):
    assert put(api, ALICE, 999, "playing").status_code == 404
    assert put(api, ALICE, 0, "playing").status_code == 422


def test_signed_out_requests_are_refused(api):
    assert api.get("/me/statuses").status_code == 401
    assert api.put("/me/games/1/status", json={"status": "playing"}).status_code == 401
    assert api.delete("/me/games/1/status").status_code == 401


def test_statuses_are_per_player(api):
    put(api, ALICE, 1, "dropped")
    put(api, BOB, 1, "playing")
    assert [r["status"] for r in api.get("/me/statuses", headers=who(api, ALICE)).json()] == ["dropped"]
    assert [r["status"] for r in api.get("/me/statuses", headers=who(api, BOB)).json()] == ["playing"]
    # Bob clearing his own status leaves Alice's alone.
    api.delete("/me/games/1/status", headers=who(api, BOB))
    assert [r["status"] for r in api.get("/me/statuses", headers=who(api, ALICE)).json()] == ["dropped"]


def test_browser_writes_need_origin_and_csrf(api):
    csrf = cookie_session(api, ALICE)
    for headers in ({}, {"Origin": settings.app_base_url}, {"X-CSRF-Token": csrf},
                    {"Origin": "https://attacker.example", "X-CSRF-Token": csrf}):
        assert api.put("/me/games/1/status", json={"status": "playing"}, headers=headers).status_code == 403
        assert api.delete("/me/games/1/status", headers=headers).status_code == 403
    good = {"Origin": settings.app_base_url, "X-CSRF-Token": csrf}
    assert api.put("/me/games/1/status", json={"status": "playing"}, headers=good).status_code == 200
    api.cookies.clear()


def test_hourly_write_budget(api, monkeypatch):
    monkeypatch.setattr(library, "STATUS_WRITES_PER_HOUR", 2)
    assert put(api, ALICE, 1, "playing").status_code == 200
    assert put(api, ALICE, 1, "paused").status_code == 200
    assert put(api, ALICE, 1, "dropped").status_code == 429


def test_status_cap(api, monkeypatch):
    monkeypatch.setattr(library, "MAX_STATUSES", 1)
    assert put(api, ALICE, 1, "playing").status_code == 200
    # Changing an existing status is still allowed at the cap; adding a new game is not.
    assert put(api, ALICE, 1, "paused").status_code == 200
    assert put(api, ALICE, 2, "want_to_play").status_code == 409


def test_profile_shows_counts_only_when_the_full_profile_is_visible(api, db):
    put(api, ALICE, 1, "completed")
    put(api, ALICE, 2, "want_to_play")
    counts = api.get(f"/users/{ALICE}", headers=who(api, BOB)).json()["status_counts"]
    assert counts == {"want_to_play": 1, "playing": 0, "completed": 1, "paused": 0, "dropped": 0}

    db.get(User, ALICE).profile_visibility = "followers"
    db.commit()
    assert "status_counts" not in api.get(f"/users/{ALICE}", headers=who(api, BOB)).json()
    assert api.get(f"/users/{ALICE}", headers=who(api, ALICE)).json()["status_counts"]["completed"] == 1

    db.get(User, ALICE).profile_visibility = "private"
    db.commit()
    assert api.get(f"/users/{ALICE}", headers=who(api, BOB)).status_code == 404


def test_blocked_player_sees_no_counts(api, db):
    put(api, ALICE, 1, "completed")
    # Bob has to have posted for Alice to find him and block him.
    db.add(Review(id=12, user_id=BOB, game_id=1, rating=3))
    db.commit()
    assert api.post(f"/users/{BOB}/block", headers=who(api, ALICE)).status_code == 204
    response = api.get(f"/users/{ALICE}", headers=who(api, BOB))
    assert response.status_code == 404 or "status_counts" not in response.json()


@pytest.mark.asyncio
async def test_a_steam_sync_leaves_statuses_alone(setup):  # noqa: F811
    engine, Session, install = setup
    install(FakeSteam({10: 120, 20: 0}))
    await worker.sync_steam_library({}, 1)
    with Session() as db:
        games = {game.steam_appid: game.id for game in db.query(Game).all()}
        db.add_all([GameStatus(user_id=1, game_id=games[10], status="completed"),
                    GameStatus(user_id=1, game_id=games[20], status="want_to_play")])
        db.commit()
        before = sorted((row.game_id, row.status, row.completed_at) for row in db.query(GameStatus).all())

    install(FakeSteam({10: 300, 20: 45}))
    await worker.sync_steam_library({}, 1)
    with Session() as db:
        after = sorted((row.game_id, row.status, row.completed_at) for row in db.query(GameStatus).all())
        assert db.query(LinkedAccount).filter_by(platform=Platform.steam, platform_user_id=STEAM_ID).count() == 1
    assert after == before
