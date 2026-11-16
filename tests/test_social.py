"""Profiles, follows, blocks and reports, with synthetic accounts.

Alice (1), Bob (2) and Cara (3) have all posted. Dan (4) has an account but
has never posted or followed anyone, so nobody should be able to find him.
"""
import secrets
from datetime import timedelta

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.database import get_db
from app.demo import DemoGuard
from app.models import Block, Comment, Follow, Game, Report, Review, User, utcnow
from app.routers import explore, feed, reviews, social
from app.security import AUDIENCE, ISSUER, csrf_token, session_cookie_name, state_key
from tests.security_helpers import MemoryRedis, auth_headers

ALICE, BOB, CARA, DAN = 1, 2, 3, 4
MISSING = 999


@pytest.fixture
def store():
    redis = MemoryRedis()
    # Start just after an hour boundary so the hourly budgets never straddle two windows.
    redis.now = 3600 * 1000 + 10
    return redis


@pytest.fixture
def api(db, store):
    app = FastAPI()
    for router in (feed.router, reviews.router, social.router, explore.router):
        app.include_router(router)
    app.state.arq_pool = store
    app.dependency_overrides[get_db] = lambda: db
    db.add_all([User(id=ALICE, display_name="Alice"), User(id=BOB, display_name="Bob"),
                User(id=CARA, display_name="Cara"), User(id=DAN, display_name="Dan"),
                Game(id=1, steam_appid=10, name="Cold Harbor", genres="Action"),
                Game(id=2, steam_appid=20, name="Dune Run", genres="Racing")])
    db.flush()
    db.add_all([Review(id=11, user_id=ALICE, game_id=1, rating=4, body="Alice on Cold Harbor"),
                Review(id=12, user_id=BOB, game_id=1, rating=2, body="Bob on Cold Harbor"),
                Review(id=13, user_id=CARA, game_id=1, rating=5, body="Cara on Cold Harbor"),
                Review(id=14, user_id=BOB, game_id=2, rating=3, body="Bob on Dune Run")])
    db.flush()
    db.add_all([Comment(id=21, review_id=13, user_id=BOB, body="Bob replies to Cara"),
                Comment(id=22, review_id=13, user_id=ALICE, body="Alice replies to Cara")])
    db.commit()
    with TestClient(app, base_url="http://localhost:8000") as client:
        client.store = store
        yield client


def who(api, user_id):
    return auth_headers(api.store, user_id)


def authors(response):
    return sorted({item["review"]["user_id"] for item in response.json()["items"]})


def block(api, blocker, blocked):
    assert api.post(f"/users/{blocked}/block", headers=who(api, blocker)).status_code == 204


def follow(api, follower, followee):
    return api.post(f"/users/{followee}/follow", headers=who(api, follower))


# ---------------------------------------------------------------- profiles

def test_public_profile_counts_reviews_and_viewer_state(api):
    assert follow(api, ALICE, BOB).status_code == 200
    guest = api.get(f"/users/{BOB}").json()
    assert guest["display_name"] == "Bob" and guest["limited"] is False
    assert guest["counts"] == {"followers": 1, "following": 0, "reviews": 2, "likes_received": 0, "verified_minutes": 0}
    assert [item["review"]["id"] for item in guest["recent_reviews"]] == [14, 12]
    assert guest["viewer"] == {"signed_in": False, "is_self": False, "following": False,
                               "requested": False, "follows_you": False, "blocked": False}
    alice = api.get(f"/users/{BOB}", headers=who(api, ALICE)).json()
    assert alice["viewer"]["following"] is True
    bob = api.get(f"/users/{ALICE}", headers=who(api, BOB)).json()
    assert bob["viewer"]["follows_you"] is True and bob["viewer"]["following"] is False
    assert api.get(f"/users/{BOB}", headers=who(api, BOB)).json()["viewer"]["is_self"] is True


def test_a_bad_session_on_a_public_page_reads_as_a_guest(api):
    api.cookies.set(session_cookie_name(), "not-a-session")
    assert api.get(f"/users/{BOB}").status_code == 200
    assert api.get("/feed").status_code == 200
    api.cookies.clear()
    assert api.get("/feed", headers={"Authorization": "Bearer nonsense"}).status_code == 200


def test_people_who_never_posted_cannot_be_found_by_guessing_ids(api):
    nothing = api.get(f"/users/{MISSING}")
    for path in (f"/users/{DAN}", f"/users/{DAN}/followers", f"/users/{DAN}/following"):
        response = api.get(path, headers=who(api, ALICE))
        assert response.status_code == 404 and response.json() == nothing.json()
    assert follow(api, ALICE, DAN).json() == nothing.json()
    assert api.post(f"/users/{DAN}/block", headers=who(api, ALICE)).json() == nothing.json()
    assert api.post("/reports", headers=who(api, ALICE), json={
        "target_type": "user", "target_id": DAN, "reason": "spam"}).json() == nothing.json()
    # Dan can still see himself, and once he posts he is as public as his post.
    assert api.get(f"/users/{DAN}", headers=who(api, DAN)).status_code == 200
    assert api.post("/reviews/11/comments", json={"body": "Hello"}, headers=who(api, DAN)).status_code == 201
    assert api.get(f"/users/{DAN}").status_code == 200


def test_unfollow_and_unblock_never_reveal_whether_a_user_exists(api):
    for user_id in (DAN, MISSING):
        assert api.delete(f"/users/{user_id}/follow", headers=who(api, ALICE)).status_code == 204
        assert api.delete(f"/users/{user_id}/block", headers=who(api, ALICE)).status_code == 204


def test_private_profile_is_missing_to_everyone_but_its_owner(api, db):
    assert follow(api, ALICE, BOB).status_code == 200
    assert api.patch("/me/profile", json={"profile_visibility": "private"}, headers=who(api, BOB)).status_code == 200
    nothing = api.get(f"/users/{MISSING}").json()
    for headers in ({}, who(api, ALICE), who(api, CARA)):
        response = api.get(f"/users/{BOB}", headers=headers)
        assert response.status_code == 404 and response.json() == nothing
    for headers in (who(api, ALICE), who(api, CARA)):
        for path in (f"/users/{BOB}/followers", f"/users/{BOB}/following"):
            response = api.get(path, headers=headers)
            assert response.status_code == 404 and response.json() == nothing
    assert follow(api, CARA, BOB).json() == nothing
    own = api.get(f"/users/{BOB}", headers=who(api, BOB)).json()
    assert own["profile_visibility"] == "private" and own["counts"]["followers"] == 1
    # Bob can still follow public people, and private players stay out of
    # other people's follow lists except in their own view.
    assert follow(api, BOB, CARA).status_code == 200
    assert api.get(f"/users/{CARA}/followers", headers=who(api, ALICE)).json() == {"items": [], "total": 0}
    assert [row["id"] for row in api.get(f"/users/{CARA}/followers", headers=who(api, BOB)).json()["items"]] == [BOB]


def test_followers_only_profile_shows_basics_to_everyone_else(api):
    assert api.patch("/me/profile", json={"bio": "Racing games, mostly", "profile_visibility": "followers"},
                     headers=who(api, BOB)).status_code == 200
    for headers in ({}, who(api, CARA)):
        basic = api.get(f"/users/{BOB}", headers=headers).json()
        assert basic["limited"] is True and basic["display_name"] == "Bob"
        assert "bio" not in basic and "recent_reviews" not in basic
    assert api.get(f"/users/{BOB}/following", headers=who(api, CARA)).status_code == 404


def test_following_a_followers_only_profile_is_a_request_until_accepted(api, db):
    assert api.patch("/me/profile", json={"bio": "Racing games, mostly", "profile_visibility": "followers"},
                     headers=who(api, BOB)).status_code == 200
    asked = follow(api, ALICE, BOB).json()
    assert asked["following"] is False and asked["requested"] is True and asked["counts"]["followers"] == 0
    assert follow(api, ALICE, BOB).json()["requested"] is True  # asking twice changes nothing
    # A pending request grants nothing: profile, lists, counts and the following feed all stay closed.
    pending = api.get(f"/users/{BOB}", headers=who(api, ALICE)).json()
    assert pending["limited"] is True and "bio" not in pending
    assert pending["viewer"]["requested"] is True and pending["viewer"]["following"] is False
    assert api.get(f"/users/{BOB}/followers", headers=who(api, ALICE)).status_code == 404
    assert api.get("/me/following-feed", headers=who(api, ALICE)).json()["total"] == 0
    assert api.get(f"/users/{ALICE}/following", headers=who(api, CARA)).json()["total"] == 0
    # Only Bob sees the request, and only Bob can answer it.
    assert api.get("/me/follow-requests", headers=who(api, ALICE)).json()["total"] == 0
    requests = api.get("/me/follow-requests", headers=who(api, BOB)).json()
    assert requests["total"] == 1 and requests["items"][0]["user"] == {"id": ALICE, "display_name": "Alice", "handle": None, "badge": None, "avatar_url": None}
    request_id = requests["items"][0]["id"]
    assert api.post(f"/me/follow-requests/{request_id}/accept", headers=who(api, ALICE)).status_code == 404
    assert api.post(f"/me/follow-requests/{request_id}/accept", headers=who(api, CARA)).status_code == 404
    accepted = api.post(f"/me/follow-requests/{request_id}/accept", headers=who(api, BOB))
    assert accepted.status_code == 200 and accepted.json()["counts"]["followers"] == 1
    assert api.post(f"/me/follow-requests/{request_id}/accept", headers=who(api, BOB)).status_code == 404
    full = api.get(f"/users/{BOB}", headers=who(api, ALICE)).json()
    assert full["limited"] is False and full["bio"] == "Racing games, mostly" and len(full["recent_reviews"]) == 2
    assert api.get(f"/users/{BOB}/followers", headers=who(api, ALICE)).json()["total"] == 1
    assert api.get("/me/following-feed", headers=who(api, ALICE)).json()["total"] == 2


def test_declined_and_cancelled_requests_leave_nothing_behind(api, db):
    assert api.patch("/me/profile", json={"profile_visibility": "followers"}, headers=who(api, BOB)).status_code == 200
    assert follow(api, ALICE, BOB).json()["requested"] is True
    request_id = api.get("/me/follow-requests", headers=who(api, BOB)).json()["items"][0]["id"]
    assert api.post(f"/me/follow-requests/{request_id}/decline", headers=who(api, BOB)).status_code == 204
    assert db.query(Follow).count() == 0
    assert follow(api, CARA, BOB).json()["requested"] is True
    assert api.delete(f"/users/{BOB}/follow", headers=who(api, CARA)).status_code == 204  # cancel
    assert api.get("/me/follow-requests", headers=who(api, BOB)).json()["total"] == 0
    # A request that is blocked away is gone, and does not come back on unblock.
    assert follow(api, ALICE, BOB).json()["requested"] is True
    block(api, BOB, ALICE)
    assert db.query(Follow).count() == 0
    assert api.delete(f"/users/{ALICE}/block", headers=who(api, BOB)).status_code == 204
    assert api.get("/me/follow-requests", headers=who(api, BOB)).json()["total"] == 0
    # Going public lets waiting requests through.
    assert follow(api, CARA, BOB).json()["requested"] is True
    assert api.patch("/me/profile", json={"profile_visibility": "public"}, headers=who(api, BOB)).status_code == 200
    assert api.get(f"/users/{BOB}", headers=who(api, CARA)).json()["viewer"]["following"] is True


def test_a_request_from_someone_you_blocked_is_not_shown(api, db):
    assert api.patch("/me/profile", json={"profile_visibility": "followers"}, headers=who(api, BOB)).status_code == 200
    db.add(Block(blocker_id=BOB, blocked_id=CARA))
    db.add(Follow(follower_id=CARA, followee_id=BOB, status="pending"))  # as if it raced the block
    db.commit()
    assert api.get("/me/follow-requests", headers=who(api, BOB)).json()["total"] == 0


def test_follow_lists_need_a_session_and_a_read_budget(api):
    assert follow(api, ALICE, BOB).status_code == 200
    for path in (f"/users/{BOB}/followers", f"/users/{BOB}/following"):
        assert api.get(path).status_code == 401
    page = api.get(f"/users/{BOB}/followers", headers=who(api, CARA)).json()
    # Names only, no follow timestamps.
    assert page == {"items": [{"id": ALICE, "display_name": "Alice", "handle": None, "badge": None, "avatar_url": None}], "total": 1}
    headers = who(api, CARA)
    for _ in range(29):  # 30 a minute, one already spent above
        assert api.get(f"/users/{BOB}/followers", headers=headers).status_code == 200
    assert api.get(f"/users/{BOB}/followers", headers=headers).status_code == 429
    assert api.get(f"/users/{BOB}/followers", headers=who(api, ALICE)).status_code == 200


# ---------------------------------------------------------------- following

def test_follow_rules(api, db):
    assert follow(api, ALICE, ALICE).status_code == 422
    assert follow(api, ALICE, MISSING).status_code == 404
    first = follow(api, ALICE, BOB)
    assert first.status_code == 200 and first.json()["counts"]["followers"] == 1
    assert follow(api, ALICE, BOB).json()["counts"]["followers"] == 1  # idempotent
    assert db.query(Follow).count() == 1
    assert api.delete(f"/users/{BOB}/follow", headers=who(api, ALICE)).status_code == 204
    assert db.query(Follow).count() == 0
    assert api.post(f"/users/{BOB}/follow").status_code == 401


def test_follow_lists_are_bounded_and_paginated(api):
    for follower in (ALICE, CARA):
        assert follow(api, follower, BOB).status_code == 200
    viewer = who(api, DAN)
    page = api.get(f"/users/{BOB}/followers?limit=1", headers=viewer).json()
    assert page["total"] == 2 and [row["id"] for row in page["items"]] == [CARA]
    assert [row["id"] for row in api.get(f"/users/{BOB}/followers?limit=1&offset=1", headers=viewer).json()["items"]] == [ALICE]
    assert [row["id"] for row in api.get(f"/users/{ALICE}/following", headers=viewer).json()["items"]] == [BOB]
    for query in ("limit=51", "limit=0", "offset=-1", "offset=10001"):
        assert api.get(f"/users/{BOB}/followers?{query}", headers=viewer).status_code == 422
    assert api.get("/users/0").status_code == 422
    assert api.get("/users/9223372036854775808").status_code == 422


def test_following_feed_shows_only_people_you_follow_and_stays_anchored(api, db):
    assert api.get("/me/following-feed", headers=who(api, ALICE)).json()["items"] == []
    assert follow(api, ALICE, BOB).status_code == 200
    first = api.get("/me/following-feed?limit=1", headers=who(api, ALICE)).json()
    assert first["total"] == 2 and first["mode"] == "following"
    assert first["items"][0]["reason"] == "From someone you follow"
    # A new post from Bob after the first page must not shift the second one.
    db.add_all([Game(id=3, steam_appid=30, name="Late Arrival"),
                Review(id=15, user_id=BOB, game_id=3, rating=1, body="Too new for this page"),
                Review(id=16, user_id=CARA, game_id=2, rating=1, body="Not followed")])
    db.commit()
    second = api.get(f"/me/following-feed?limit=1&offset=1&before={first['before']}", headers=who(api, ALICE)).json()
    assert second["total"] == 2
    assert {first["items"][0]["review"]["id"], second["items"][0]["review"]["id"]} == {12, 14}
    fresh = api.get("/me/following-feed", headers=who(api, ALICE)).json()
    assert [item["review"]["id"] for item in fresh["items"]][0] == 15 and fresh["total"] == 3
    assert api.get("/me/following-feed").status_code == 401
    assert api.get("/me/following-feed?limit=51", headers=who(api, ALICE)).status_code == 422


# ---------------------------------------------------------------- blocking

def test_blocking_removes_follows_both_ways_and_stops_new_ones(api, db):
    assert follow(api, ALICE, BOB).status_code == 200
    assert follow(api, BOB, ALICE).status_code == 200
    block(api, ALICE, BOB)
    assert db.query(Follow).count() == 0
    nothing = api.get(f"/users/{MISSING}").json()
    assert follow(api, ALICE, BOB).json() == nothing
    assert follow(api, BOB, ALICE).json() == nothing
    assert db.query(Follow).count() == 0
    block(api, ALICE, BOB)  # idempotent
    assert db.query(Block).count() == 1
    assert api.post(f"/users/{ALICE}/block", headers=who(api, ALICE)).status_code == 422
    blocks = api.get("/me/blocks", headers=who(api, ALICE)).json()
    assert blocks["total"] == 1 and blocks["items"][0]["id"] == BOB
    assert api.get("/me/blocks", headers=who(api, BOB)).json()["total"] == 0
    assert api.delete(f"/users/{BOB}/block", headers=who(api, ALICE)).status_code == 204
    assert follow(api, ALICE, BOB).status_code == 200


def test_profile_of_someone_who_blocked_you_is_indistinguishable_from_a_missing_user(api):
    block(api, BOB, ALICE)
    nothing = api.get(f"/users/{MISSING}", headers=who(api, ALICE))
    for path in (f"/users/{BOB}", f"/users/{BOB}/followers", f"/users/{BOB}/following"):
        response = api.get(path, headers=who(api, ALICE))
        assert (response.status_code, response.json()) == (nothing.status_code, nothing.json())
    # The blocker gets a stub with no content, just enough to unblock.
    stub = api.get(f"/users/{ALICE}", headers=who(api, BOB)).json()
    assert stub["viewer"]["blocked"] is True and stub["limited"] is True
    assert "recent_reviews" not in stub and "bio" not in stub and "counts" not in stub
    # Cara is unaffected and sees both.
    assert api.get(f"/users/{BOB}", headers=who(api, CARA)).status_code == 200


@pytest.mark.parametrize("blocker,blocked", [(ALICE, BOB), (BOB, ALICE)])
def test_blocked_reviews_disappear_everywhere_for_both_people(api, blocker, blocked):
    assert follow(api, ALICE, BOB).status_code == 200
    # Warm the shared cache first, so a stale shared copy would show up here.
    assert BOB in authors(api.get("/feed")) and ALICE in authors(api.get("/feed"))
    block(api, blocker, blocked)
    viewer = who(api, blocker)
    other = blocked
    for path in ("/feed", "/me/feed", "/me/following-feed"):
        assert other not in authors(api.get(path, headers=viewer)), path
    assert other not in [row["user_id"] for row in api.get("/games/1/reviews", headers=viewer).json()]
    hidden_review = 12 if other == BOB else 11
    nothing = api.get("/reviews/99999", headers=viewer)
    thread = api.get(f"/reviews/{hidden_review}", headers=viewer)
    assert (thread.status_code, thread.json()) == (nothing.status_code, nothing.json())
    assert api.get(f"/reviews/{hidden_review}/comments", headers=viewer).status_code == 404
    # The other side sees nothing of the blocker either.
    back = who(api, other)
    assert blocker not in authors(api.get("/feed", headers=back))
    assert blocker not in [row["user_id"] for row in api.get("/games/1/reviews", headers=back).json()]
    # Guests and Cara still get everyone, from a cache the blocks never touched.
    for headers in ({}, who(api, CARA)):
        assert {ALICE, BOB} <= set(authors(api.get("/feed", headers=headers)))
        assert {ALICE, BOB} <= {row["user_id"] for row in api.get("/games/1/reviews", headers=headers).json()}


def test_blocked_comments_disappear_and_counts_follow(api):
    block(api, ALICE, BOB)
    comments = api.get("/reviews/13/comments", headers=who(api, ALICE)).json()
    assert [row["user_id"] for row in comments] == [ALICE]
    thread = api.get("/reviews/13", headers=who(api, ALICE)).json()
    assert thread["comment_count"] == 1
    cara = next(item for item in api.get("/feed", headers=who(api, ALICE)).json()["items"] if item["review"]["id"] == 13)
    assert cara["comment_count"] == 1
    assert [row["user_id"] for row in api.get("/reviews/13/comments", headers=who(api, BOB)).json()] == [BOB]
    assert len(api.get("/reviews/13/comments").json()) == 2
    assert api.get("/reviews/13", headers=who(api, CARA)).json()["comment_count"] == 2


def test_nobody_can_comment_on_a_review_by_someone_who_blocked_them(api, db):
    block(api, ALICE, BOB)
    nothing = api.post("/reviews/99999/comments", json={"body": "Hi"}, headers=who(api, BOB))
    response = api.post("/reviews/11/comments", json={"body": "Hi"}, headers=who(api, BOB))
    assert (response.status_code, response.json()) == (nothing.status_code, nothing.json())
    # Nor the other way around: the blocker cannot reply to someone they blocked.
    assert api.post("/reviews/12/comments", json={"body": "Hi"}, headers=who(api, ALICE)).status_code == 404
    assert db.query(Comment).filter(Comment.review_id.in_([11, 12])).count() == 0
    assert api.post("/reviews/11/comments", json={"body": "Hi"}, headers=who(api, CARA)).status_code == 201


def test_three_people_two_blocks(api):
    # Alice blocks Bob, Bob blocks Cara. Alice and Cara still see each other.
    block(api, ALICE, BOB)
    block(api, BOB, CARA)
    assert authors(api.get("/feed", headers=who(api, ALICE))) == [ALICE, CARA]
    assert authors(api.get("/feed", headers=who(api, BOB))) == [BOB]
    assert authors(api.get("/feed", headers=who(api, CARA))) == [ALICE, CARA]
    assert api.get(f"/users/{CARA}", headers=who(api, ALICE)).status_code == 200
    assert api.get(f"/users/{BOB}", headers=who(api, CARA)).status_code == 404
    assert api.get(f"/users/{CARA}", headers=who(api, BOB)).json()["viewer"]["blocked"] is True


def test_follow_lists_hide_blocked_people(api):
    for follower in (ALICE, CARA):
        assert follow(api, follower, BOB).status_code == 200
    block(api, ALICE, CARA)
    assert [row["id"] for row in api.get(f"/users/{BOB}/followers", headers=who(api, ALICE)).json()["items"]] == [ALICE]
    assert [row["id"] for row in api.get(f"/users/{BOB}/followers", headers=who(api, CARA)).json()["items"]] == [CARA]
    assert api.get(f"/users/{BOB}/followers", headers=who(api, BOB)).json()["total"] == 2


# ---------------------------------------------------------------- reports

def report(api, user_id, **payload):
    return api.post("/reports", headers=who(api, user_id), json={"reason": "spam", **payload})


def test_reports_are_validated_and_one_open_per_target(api, db):
    assert report(api, ALICE, target_type="review", target_id=12).status_code == 201
    assert report(api, ALICE, target_type="review", target_id=12).status_code == 409
    assert report(api, CARA, target_type="review", target_id=12).status_code == 201
    assert report(api, ALICE, target_type="comment", target_id=21, note="  rude  ").status_code == 201
    assert db.query(Report).filter_by(target_type="comment").one().note == "rude"
    assert report(api, ALICE, target_type="user", target_id=BOB).status_code == 201
    assert report(api, ALICE, target_type="review", target_id=11).status_code == 422
    assert report(api, ALICE, target_type="user", target_id=ALICE).status_code == 422
    assert report(api, ALICE, target_type="review", target_id=MISSING).status_code == 404
    assert report(api, ALICE, target_type="game", target_id=1).status_code == 422
    assert report(api, ALICE, target_type="review", target_id=13, reason="because").status_code == 422
    assert report(api, ALICE, target_type="review", target_id=13, note="x" * 501).status_code == 422
    assert report(api, ALICE, target_type="review", target_id=13, status="actioned").status_code == 422
    assert api.post("/reports", json={"target_type": "review", "target_id": 12, "reason": "spam"}).status_code == 401


def test_reporting_works_the_same_whether_or_not_you_were_blocked(api):
    block(api, BOB, ALICE)
    assert report(api, ALICE, target_type="review", target_id=12).status_code == 201
    assert report(api, ALICE, target_type="user", target_id=BOB).status_code == 201


def test_report_queue_is_owner_only(api, monkeypatch):
    assert report(api, ALICE, target_type="review", target_id=12, note="Spoils the ending").status_code == 201
    nothing = {"detail": "Not Found"}
    monkeypatch.setattr(settings, "owner_user_ids", "")
    for user_id in (ALICE, CARA):
        response = api.get("/admin/reports", headers=who(api, user_id))
        assert response.status_code == 404 and response.json() == nothing
    monkeypatch.setattr(settings, "owner_user_ids", f" {CARA} , x")
    assert api.get("/admin/reports", headers=who(api, ALICE)).status_code == 404
    assert api.post("/admin/reports/1", json={"status": "dismissed"}, headers=who(api, ALICE)).status_code == 404
    assert api.get("/admin/reports").status_code == 401
    queue = api.get("/admin/reports", headers=who(api, CARA)).json()
    assert queue["total"] == 1
    item = queue["items"][0]
    assert item["reporter"] == {"id": ALICE, "display_name": "Alice", "handle": None, "badge": None, "avatar_url": None}
    assert item["target"]["author"]["id"] == BOB and item["target"]["body"] == "Bob on Cold Harbor"
    assert api.post(f"/admin/reports/{item['id']}", json={"status": "open"}, headers=who(api, CARA)).status_code == 422
    closed = api.post(f"/admin/reports/{item['id']}", json={"status": "actioned"}, headers=who(api, CARA))
    assert closed.status_code == 200 and closed.json()["status"] == "actioned"
    assert api.get("/admin/reports", headers=who(api, CARA)).json()["total"] == 0
    assert api.get("/admin/reports?status=actioned", headers=who(api, CARA)).json()["total"] == 1
    assert api.post("/admin/reports/999", json={"status": "dismissed"}, headers=who(api, CARA)).status_code == 404
    # Once closed, the same person can report it again.
    assert report(api, ALICE, target_type="review", target_id=12).status_code == 201


def test_closing_a_report_needs_a_recent_sign_in_and_is_logged(api, monkeypatch, caplog):
    import logging
    assert report(api, ALICE, target_type="review", target_id=12, note="Secret note text").status_code == 201
    monkeypatch.setattr(settings, "owner_user_ids", str(CARA))
    report_id = api.get("/admin/reports", headers=who(api, CARA)).json()["items"][0]["id"]
    now = utcnow()
    stale = auth_headers(api.store, CARA, iat=now - timedelta(minutes=16), nbf=now - timedelta(minutes=16),
                         exp=now + timedelta(minutes=10))
    with caplog.at_level(logging.INFO, logger="playgraph.moderation"):
        refused = api.post(f"/admin/reports/{report_id}", json={"status": "dismissed"}, headers=stale)
        assert refused.status_code == 403
        assert api.get("/admin/reports", headers=who(api, CARA)).json()["items"][0]["status"] == "open"
        assert api.post(f"/admin/reports/{report_id}", json={"status": "dismissed"},
                        headers=who(api, CARA)).status_code == 200
    lines = [record.getMessage() for record in caplog.records if record.name == "playgraph.moderation"]
    assert f"moderation action=dismissed moderator={CARA} report={report_id}" in lines
    assert any("refused_stale_session" in line for line in lines)
    assert not any("Secret note" in line or "Bob on Cold Harbor" in line for line in lines)


def test_owner_list_is_parsed_strictly():
    from app.config import Settings
    assert Settings(owner_user_ids="").owners == frozenset()
    assert Settings(owner_user_ids="1, 2,abc,,-3").owners == frozenset({1, 2})


# ---------------------------------------------------------------- profile edits

def test_profile_edit_cleans_the_bio_and_changes_only_what_was_sent(api, db):
    response = api.patch("/me/profile", headers=who(api, ALICE),
                         json={"bio": "  Plays‮ slowly\n\n and\x00 well​  "})
    assert response.status_code == 200
    assert response.json()["bio"] == "Plays slowly and well"
    assert db.get(User, ALICE).profile_visibility == "public"
    assert api.patch("/me/profile", headers=who(api, ALICE), json={"profile_visibility": "followers"}).json()["bio"] \
        == "Plays slowly and well"
    assert api.patch("/me/profile", headers=who(api, ALICE), json={"bio": "x" * 300}).json()["bio"] == "x" * 280
    assert api.patch("/me/profile", headers=who(api, ALICE), json={"bio": "   "}).json()["bio"] is None
    for bad in ({"bio": "x" * 561}, {"profile_visibility": "friends"}, {"profile_visibility": None},
                {"display_name": "Mallory"}, {"user_id": BOB, "bio": "hi"}):
        assert api.patch("/me/profile", headers=who(api, ALICE), json=bad).status_code == 422, bad
    assert db.get(User, BOB).bio is None and db.get(User, ALICE).display_name == "Alice"
    assert api.patch("/me/profile", json={"bio": "hi"}).status_code == 401


# ---------------------------------------------------------------- rate limits

@pytest.mark.parametrize("bucket,limit,call", [
    ("follow", 60, lambda api, i: api.post(f"/users/{BOB if i % 2 else CARA}/follow", headers=who(api, ALICE))
     if i % 4 < 2 else api.delete(f"/users/{BOB}/follow", headers=who(api, ALICE))),
    ("block", 30, lambda api, i: api.post(f"/users/{BOB}/block", headers=who(api, ALICE))
     if i % 2 else api.delete(f"/users/{BOB}/block", headers=who(api, ALICE))),
    ("report", 20, lambda api, i: report(api, ALICE, target_type="review", target_id=12)),
    ("profile", 20, lambda api, i: api.patch("/me/profile", headers=who(api, ALICE), json={"bio": str(i)})),
])
def test_each_social_write_has_an_hourly_budget(api, bucket, limit, call):
    for i in range(limit):
        if i and i % 20 == 0:
            api.store.now += 121  # stay under the general 30-a-minute write budget
        assert call(api, i).status_code != 429, i
    api.store.now += 121
    refused = call(api, limit)
    assert refused.status_code == 429 and "retry-after" in refused.headers
    assert api.store.spent(state_key(f"rate:{bucket}", str(ALICE))) == limit
    # The budget is per account.
    assert api.patch("/me/profile", headers=who(api, CARA), json={"bio": "fine"}).status_code == 200


# ---------------------------------------------------------------- browser checks and demo

def cookie_session(api, user_id):
    session_id = secrets.token_urlsafe(32)
    now = utcnow()
    token = jwt.encode({"sub": str(user_id), "iat": now, "nbf": now, "exp": now + timedelta(minutes=settings.session_minutes),
                        "iss": ISSUER, "aud": AUDIENCE, "jti": session_id},
                       settings.jwt_secret.get_secret_value(), algorithm="HS256")
    api.store.data[state_key("session", session_id)] = str(user_id).encode()
    api.cookies.set(session_cookie_name(), token)
    return csrf_token(session_id)


WRITES = [("post", f"/users/{BOB}/follow", None), ("delete", f"/users/{BOB}/follow", None),
          ("post", f"/users/{BOB}/block", None), ("delete", f"/users/{BOB}/block", None),
          ("post", "/reports", {"target_type": "review", "target_id": 12, "reason": "spam"}),
          ("patch", "/me/profile", {"bio": "From the browser"}),
          ("post", "/admin/reports/1", {"status": "dismissed"})]


@pytest.mark.parametrize("method,path,body", WRITES)
def test_browser_writes_need_origin_and_csrf(api, db, method, path, body):
    csrf = cookie_session(api, ALICE)
    options = {"json": body} if body is not None else {}
    for headers in ({}, {"Origin": settings.app_base_url}, {"X-CSRF-Token": csrf},
                    {"Origin": "https://attacker.example", "X-CSRF-Token": csrf},
                    {"Origin": settings.app_base_url, "X-CSRF-Token": "x" * 64}):
        assert api.request(method.upper(), path, headers=headers, **options).status_code == 403
    assert db.query(Follow).count() == db.query(Block).count() == db.query(Report).count() == 0
    assert db.get(User, ALICE).bio is None
    good = api.request(method.upper(), path, headers={"Origin": settings.app_base_url, "X-CSRF-Token": csrf}, **options)
    assert good.status_code in (200, 201, 204) or path.startswith("/admin")
    api.cookies.clear()
    assert api.request(method.upper(), path, **options).status_code == 401


@pytest.mark.parametrize("method,path,body", WRITES)
def test_demo_refuses_every_social_write(db, store, monkeypatch, method, path, body):
    monkeypatch.setattr(settings, "demo_mode", True)
    app = FastAPI()
    app.include_router(social.router)
    app.add_middleware(DemoGuard)
    app.state.arq_pool = store
    app.dependency_overrides[get_db] = lambda: db
    db.add_all([User(id=ALICE, display_name="Alice"), User(id=BOB, display_name="Bob")])
    db.commit()
    with TestClient(app, base_url="http://localhost:8000") as client:
        response = client.request(method.upper(), path, headers=auth_headers(store, ALICE),
                                  **({"json": body} if body is not None else {}))
    assert response.status_code == 403 and response.json()["detail"] == "The demo is read-only."
    assert db.query(Follow).count() == db.query(Block).count() == db.query(Report).count() == 0
