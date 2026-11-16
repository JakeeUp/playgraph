"""Explore trends and who to follow. The window is 7 days."""

from datetime import timedelta

import pytest

from app.models import PlaytimeSnapshot, Review, ReviewLike, utcnow
from app.trending import event_weight
from tests.test_social import ALICE, BOB, CARA, api, block, follow, store, who  # noqa: F401


def test_weight_halves_over_two_days_and_stops_after_a_week():
    now = utcnow()
    assert event_weight(now, now) == 1
    assert event_weight(now - timedelta(hours=48), now) == pytest.approx(0.5)
    assert event_weight(now - timedelta(days=8), now) == 0


def test_trending_uses_this_week_and_keeps_hours_on_new_reviews(api, db):
    for review in db.query(Review).filter(Review.game_id == 1):
        review.verified_playtime_minutes = 180
    old = db.get(Review, 14)
    old.created_at = utcnow() - timedelta(days=8)
    db.commit()
    body = api.get("/explore").json()
    harbor = body["games"][0]
    assert harbor["game"]["name"] == "Cold Harbor"
    assert harbor["genre"] == "Action"
    assert harbor["hours"] == 9
    assert all(row["game"]["id"] != 2 for row in body["games"])
    assert 14 not in [item["review"]["id"] for item in body["reviews"]]
    assert any(item["review"]["id"] == 13 for item in body["reviews"])


def test_a_new_like_trends_an_old_review_without_adding_hours(api, db):
    review = db.get(Review, 14)
    review.created_at = utcnow() - timedelta(days=30)
    review.verified_playtime_minutes = 600
    db.add(ReviewLike(user_id=ALICE, review_id=14))
    db.commit()
    body = api.get("/explore").json()
    assert 14 in [item["review"]["id"] for item in body["reviews"]]
    dune = next(row for row in body["games"] if row["game"]["id"] == 2)
    assert dune["hours"] == 0


def test_cached_blob_is_what_the_next_reader_gets(api):
    assert api.get("/explore").status_code == 200
    api.store.data["playgraph:trending"] = b'{"games":[],"reviews":[]}'
    assert api.get("/explore").json() == {"games": [], "reviews": []}


def test_a_broken_blob_is_recomputed(api):
    api.store.data["playgraph:trending"] = b"not-json"
    assert api.get("/explore").json()["games"]


def test_who_to_follow_ranks_shared_games_ahead_of_likes(api, db):
    db.add_all([
        PlaytimeSnapshot(user_id=ALICE, game_id=1, playtime_minutes=60),
        PlaytimeSnapshot(user_id=ALICE, game_id=2, playtime_minutes=30),
        ReviewLike(user_id=ALICE, review_id=13),
        ReviewLike(user_id=BOB, review_id=13),
    ])
    db.commit()
    items = api.get("/explore/suggestions", headers=who(api, ALICE)).json()["items"]
    ids = [item["id"] for item in items]
    assert ids[0] == BOB
    assert CARA in ids and ALICE not in ids
    bob = next(item for item in items if item["id"] == BOB)
    cara = next(item for item in items if item["id"] == CARA)
    assert bob["shared_games"] == 2 and cara["shared_games"] == 1
    assert cara["likes_received"] == 2


def test_followed_blocked_and_private_players_are_not_suggested(api, db):
    follow(api, ALICE, BOB)
    assert api.patch("/me/profile", headers=who(api, CARA), json={"profile_visibility": "private"}).status_code == 200
    items = api.get("/explore/suggestions", headers=who(api, ALICE)).json()["items"]
    assert [item["id"] for item in items] == []
    # Cara's review is still public, so it can still trend.
    assert any(item["review"]["user_id"] == CARA for item in api.get("/explore").json()["reviews"])
    block(api, ALICE, CARA)
    hidden = api.get("/explore", headers=who(api, ALICE)).json()
    assert all(item["review"]["user_id"] != CARA for item in hidden["reviews"])
    # The shared blob did not learn Alice's block.
    assert any(item["review"]["user_id"] == CARA for item in api.get("/explore").json()["reviews"])
