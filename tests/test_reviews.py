from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
import jwt

from app.config import settings
from app.database import get_db
from app.models import Comment, Game, PlaytimeSnapshot, Review, User, utcnow
from app.routers.reviews import router
from tests.security_helpers import MemoryRedis, auth_headers


@pytest.fixture
def api(db, monkeypatch):
    app = FastAPI()
    app.state.arq_pool = MemoryRedis()
    monkeypatch.setattr("tests.test_reviews.auth", lambda user_id=1: auth_headers(app.state.arq_pool, user_id))
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    db.add_all([User(id=1, display_name="Author"), User(id=2, display_name="Reader"),
                Game(id=1, steam_appid=10, name="Game"),
                Game(id=2, steam_appid=20, name="Other game")])
    db.commit()
    with TestClient(app) as client:
        yield client


def auth(user_id=1):
    raise AssertionError("The api fixture supplies authenticated sessions")


def post_review(api, **values):
    return api.post("/games/1/reviews", json={"rating": 4.5, **values}, headers=auth())


def test_owner_edit_preserves_identity_date_and_verified_stats(api, db):
    db.add(PlaytimeSnapshot(user_id=1, game_id=1, playtime_minutes=120,
                           achievements_unlocked=2, achievements_total=10))
    db.commit()
    original = post_review(api, body="First thoughts").json()
    db.add(PlaytimeSnapshot(user_id=1, game_id=1, playtime_minutes=9999))
    db.commit()
    response = api.patch(f"/reviews/{original['id']}", headers=auth(), json={
        "rating": 3.5, "body": "After reflection", "user_id": 2, "game_id": 2,
        "verified_playtime_minutes": 99999, "verified_achievement_pct": 100,
        "created_at": "2099-01-01T00:00:00Z",
    })
    assert response.status_code == 200
    expected = {**original, "rating": 3.5, "body": "After reflection"}
    assert response.json() == expected
    assert api.get("/games/1/reviews").json() == [expected]
    assert api.get("/me/games/1/review", headers=auth()).json() == expected


@pytest.mark.parametrize("method", ["patch", "delete"])
def test_review_mutations_require_owner_and_live_auth(api, db, method):
    row = post_review(api, body="Keep this").json()
    path = f"/reviews/{row['id']}"
    options = {"json": {"rating": 1, "body": "Other account"}} if method == "patch" else {}
    request = getattr(api, method)
    assert request(path, **options).status_code == 401
    assert request(path, headers=auth(2), **options).status_code == 404
    assert request("/reviews/99999", headers=auth(), **options).status_code == 404
    assert request("/reviews/9999999999999999999", headers=auth(), **options).status_code == 422
    assert api.get("/games/1/reviews").json() == [row]


@pytest.mark.parametrize("payload", [{"rating": 0}, {"rating": 5.5}, {"rating": 2.3},
                                      {"rating": 4, "body": "x" * 10001}, {}])
def test_invalid_review_edit_leaves_original_intact(api, payload):
    original = post_review(api, body="Original").json()
    assert api.patch(f"/reviews/{original['id']}", json=payload, headers=auth()).status_code == 422
    assert api.get("/games/1/reviews").json() == [original]


def test_owner_delete_removes_only_its_thread_and_allows_new_review(api, db):
    own = post_review(api).json()
    other = api.post("/games/2/reviews", json={"rating": 5}, headers=auth(2)).json()
    for user_id in (1, 2):
        assert api.post(f"/reviews/{own['id']}/comments", json={"body": "Discussion"}, headers=auth(user_id)).status_code == 201
    assert api.post(f"/reviews/{other['id']}/comments", json={"body": "Retain"}, headers=auth()).status_code == 201
    result = api.delete(f"/reviews/{own['id']}", headers=auth())
    assert result.status_code == 204 and not result.content
    assert db.query(Comment).filter_by(review_id=own["id"]).count() == 0
    assert db.query(Comment).filter_by(review_id=other["id"]).count() == 1
    assert api.get(f"/reviews/{own['id']}/comments").status_code == 404
    assert api.post(f"/reviews/{own['id']}/comments", json={"body": "Too late"}, headers=auth(2)).status_code == 404
    assert api.get("/me/games/1/review", headers=auth()).json() is None
    assert post_review(api, body="A new review").status_code == 201


def test_review_delete_rolls_back_comments_when_parent_delete_fails(api, db):
    from sqlalchemy import event

    own = post_review(api).json()
    api.post(f"/reviews/{own['id']}/comments", json={"body": "Keep this too"}, headers=auth(2))

    def fail_parent_delete(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("DELETE FROM reviews"):
            raise RuntimeError("Simulated parent delete failure")

    event.listen(db.bind, "before_cursor_execute", fail_parent_delete)
    try:
        with pytest.raises(RuntimeError, match="Simulated parent"):
            api.delete(f"/reviews/{own['id']}", headers=auth())
    finally:
        event.remove(db.bind, "before_cursor_execute", fail_parent_delete)
    assert api.get("/games/1/reviews").json() == [own]
    assert db.query(Comment).filter_by(review_id=own["id"]).count() == 1


def test_deleting_last_review_does_not_recycle_its_shared_link(api):
    old = post_review(api).json()
    assert api.delete(f"/reviews/{old['id']}", headers=auth()).status_code == 204
    new = post_review(api).json()
    assert new["id"] > old["id"]
    assert api.get(f"/reviews/{old['id']}/comments").status_code == 404


def test_verification_is_latest_scoped_and_frozen(api, db):
    now = utcnow()
    db.add_all([
        PlaytimeSnapshot(user_id=1, game_id=1, playtime_minutes=500,
                         captured_at=now - timedelta(days=1)),
        PlaytimeSnapshot(user_id=1, game_id=1, playtime_minutes=100,
                         achievements_unlocked=2, achievements_total=8, captured_at=now),
        PlaytimeSnapshot(user_id=2, game_id=1, playtime_minutes=9000,
                         captured_at=now + timedelta(seconds=1)),
        PlaytimeSnapshot(user_id=1, game_id=2, playtime_minutes=8000,
                         captured_at=now + timedelta(seconds=1)),
    ])
    db.commit()
    response = post_review(api)
    assert response.status_code == 201
    data = response.json()
    assert data["user_id"] == 1
    assert data["verified_playtime_minutes"] == 100
    assert data["verified_achievement_pct"] == 25
    db.add(PlaytimeSnapshot(user_id=1, game_id=1, playtime_minutes=1000,
                           captured_at=now + timedelta(days=1)))
    db.commit()
    assert api.get("/games/1/reviews").json() == [data]


def test_same_timestamp_uses_newest_id(api, db):
    now = utcnow()
    for minutes in (10, 20):
        db.add(PlaytimeSnapshot(user_id=1, game_id=1, playtime_minutes=minutes, captured_at=now))
        db.commit()
    assert post_review(api).json()["verified_playtime_minutes"] == 20


def test_missing_snapshot_is_unverified_and_cannot_be_forged(api):
    response = post_review(api, user_id=2, verified_playtime_minutes=10000)
    assert response.status_code == 201
    assert response.json()["user_id"] == 1
    assert response.json()["verified_playtime_minutes"] is None
    assert response.json()["verified_achievement_pct"] is None


@pytest.mark.parametrize("unlocked,total,expected", [(None, None, None), (0, 0, None),
                                                        (None, 10, None), (0, 10, 0)])
def test_zero_playtime_and_unknown_achievements(api, db, unlocked, total, expected):
    db.add(PlaytimeSnapshot(user_id=1, game_id=1, playtime_minutes=0,
                           achievements_unlocked=unlocked, achievements_total=total))
    db.commit()
    data = post_review(api).json()
    assert data["verified_playtime_minutes"] == 0
    assert data["verified_achievement_pct"] == expected


def test_duplicate_is_conflict(api, db):
    assert post_review(api).status_code == 201
    assert post_review(api).status_code == 409
    assert db.query(Review).count() == 1


def test_own_review_ignores_public_pagination_and_scopes_to_session(api, db):
    own = post_review(api, body="My review below the first page").json()
    for user_id in range(3, 24):
        db.add(User(id=user_id, display_name=f"Player {user_id}"))
        db.add(Review(user_id=user_id, game_id=1, rating=4, verified_playtime_minutes=100))
    db.commit()
    assert own["id"] not in [row["id"] for row in api.get("/games/1/reviews").json()]
    assert api.get("/me/games/1/review", headers=auth()).json() == own
    assert api.get("/me/games/1/review?user_id=1", headers=auth(2)).json() is None
    assert api.get("/me/games/2/review", headers=auth()).json() is None
    assert api.get("/me/games/999/review", headers=auth()).status_code == 404
    assert api.get("/me/games/1/review").status_code == 401


@pytest.mark.parametrize("resource_id", [0, -1, 9223372036854775808])
def test_resource_ids_are_bounded_before_database_lookup(api, resource_id):
    for path in [f"/games/{resource_id}/reviews", f"/reviews/{resource_id}/comments",
                 f"/me/games/{resource_id}/review"]:
        assert api.get(path, headers=auth()).status_code == 422
    assert api.post(f"/games/{resource_id}/reviews", headers=auth(), json={"rating": 4}).status_code == 422
    assert api.post(f"/reviews/{resource_id}/comments", headers=auth(), json={"body": "hello"}).status_code == 422


@pytest.mark.parametrize("rating", [0, 5.5, 4.2, "NaN", "Infinity"])
def test_invalid_rating(api, rating):
    assert post_review(api, rating=rating).status_code == 422


def test_reviews_order_pagination_and_game_scope(api, db):
    db.add(User(id=3, display_name="Third"))
    db.flush()
    db.add_all([
        Review(user_id=1, game_id=1, rating=4, verified_playtime_minutes=None),
        Review(user_id=2, game_id=1, rating=4, verified_playtime_minutes=0),
        Review(user_id=3, game_id=1, rating=4, verified_playtime_minutes=100),
        Review(user_id=1, game_id=2, rating=4, verified_playtime_minutes=900),
    ])
    db.commit()
    assert [r["user_id"] for r in api.get("/games/1/reviews").json()] == [3, 2, 1]
    assert api.get("/games/1/reviews?limit=1&offset=1").json()[0]["user_id"] == 2
    assert api.get("/games/1/reviews?limit=101").status_code == 422
    assert api.get("/games/1/reviews?offset=-1").status_code == 422


def test_comments_authorship_listing_and_validation(api):
    review_id = post_review(api).json()["id"]
    path = f"/reviews/{review_id}/comments"
    assert api.get(path).json() == []
    response = api.post(path, json={"body": "  Good point  ", "user_id": 1}, headers=auth(2))
    assert response.status_code == 201
    first = response.json()
    assert first["user_id"] == 2
    assert first["body"] == "Good point"
    second = api.post(path, json={"body": "Thanks"}, headers=auth()).json()
    assert api.get(path).json() == [first, second]
    assert api.get(path + "?limit=1&offset=1").json() == [second]
    for body in ("", " \n ", "x" * 5001):
        assert api.post(path, json={"body": body}, headers=auth()).status_code == 422
    other = api.post("/games/2/reviews", json={"rating": 3}, headers=auth()).json()["id"]
    assert api.get(f"/reviews/{other}/comments").json() == []


@pytest.mark.parametrize("path,payload", [("/games/1/reviews", {"rating": 4}),
                                           ("/reviews/1/comments", {"body": "Hi"})])
def test_writes_require_authentication(api, path, payload):
    assert api.post(path, json=payload).status_code in (401, 403)
    assert api.post(path, json=payload, headers={"Authorization": "Bearer invalid"}).status_code == 401


def test_missing_resources(api):
    assert api.get("/games/999/reviews").status_code == 404
    assert api.post("/games/999/reviews", json={"rating": 4}, headers=auth()).status_code == 404
    assert api.get("/reviews/999/comments").status_code == 404
    assert api.post("/reviews/999/comments", json={"body": "Hi"}, headers=auth()).status_code == 404
    assert api.get("/games/1/reviews").json() == []
