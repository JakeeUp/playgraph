"""Verified badges: computed from verified hours on game reviews, never stored.

Same synthetic accounts as test_social. Reviews: 11 Alice, 12 Bob, 13 Cara on
game 1; 14 Bob on game 2. Nobody has a linked account or verified hours until a
test gives them one.
"""
import pytest
from sqlalchemy import inspect

from app.models import BADGE_HOURS, Game, LinkedAccount, Platform, Review, User, badge_for
from tests.test_social import ALICE, BOB, CARA, api, block, follow, store, who  # noqa: F401
from tests.test_social_s2 import repost

HOUR = 60


def link(db, user_id, platform=Platform.steam):
    db.add(LinkedAccount(user_id=user_id, platform=platform, platform_user_id=f"{platform.value}-{user_id}"))


def verify(db, review_id, hours):
    db.get(Review, review_id).verified_playtime_minutes = hours * HOUR


def profile(api, user_id, viewer=None):
    return api.get(f"/users/{user_id}", headers=who(api, viewer) if viewer else {}).json()


# ---------------------------------------------------------------- the tiers

@pytest.mark.parametrize("minutes,badge", [
    (None, None), (0, None), (100 * HOUR - 1, None), (100 * HOUR, 100), (999 * HOUR + 59, 100),
    (1000 * HOUR, 1000), (5000 * HOUR - 1, 1000), (5000 * HOUR, 5000), (90000 * HOUR, 5000),
])
def test_tiers_are_100_1000_and_5000_hours(minutes, badge):
    assert BADGE_HOURS == (100, 1000, 5000)
    assert badge_for(minutes) == badge


def test_the_badge_is_not_a_stored_column():
    assert "badge" not in inspect(User).columns.keys() and "badge_minutes" not in User.__table__.columns


def test_no_badge_without_enough_verified_hours(api, db):
    link(db, CARA)
    verify(db, 13, 99)
    db.commit()
    assert profile(api, CARA)["badge"] is None


def test_hours_add_up_across_reviews(api, db):
    link(db, BOB)
    verify(db, 12, 60)
    verify(db, 14, 40)
    db.commit()
    assert profile(api, BOB)["badge"] == 100
    verify(db, 14, 940)
    db.commit()
    assert profile(api, BOB)["badge"] == 1000


def test_verified_hours_without_a_linked_account_earn_nothing(api, db):
    verify(db, 13, 6000)
    db.commit()
    assert profile(api, CARA)["badge"] is None
    link(db, CARA, Platform.psn)
    db.commit()
    assert profile(api, CARA)["badge"] == 5000


def test_someone_elses_linked_account_does_not_count(api, db):
    link(db, ALICE)
    verify(db, 13, 6000)
    db.commit()
    assert profile(api, CARA)["badge"] is None and profile(api, ALICE)["badge"] is None


def test_unlinking_removes_badge_and_relinking_restores_it(api, db):
    link(db, CARA)
    verify(db, 13, 100)
    db.commit()
    assert profile(api, CARA)["badge"] == 100
    db.query(LinkedAccount).filter_by(user_id=CARA).delete(synchronize_session=False)
    db.commit()
    assert profile(api, CARA)["badge"] is None
    link(db, CARA, Platform.psn)
    db.commit()
    assert profile(api, CARA)["badge"] == 100


def test_blocks_hide_badge_in_both_directions(api, db):
    link(db, BOB)
    verify(db, 12, 1000)
    db.commit()
    block(api, ALICE, BOB)
    assert profile(api, BOB, viewer=ALICE)["badge"] is None
    assert api.get(f"/users/{ALICE}", headers=who(api, BOB)).status_code == 404
    assert all(item["review"]["user_id"] != BOB for item in api.get("/feed", headers=who(api, ALICE)).json()["items"])


def test_software_reviews_do_not_count(api, db):
    db.add(Game(id=3, steam_appid=431960, name="Wallpaper Engine", genres="Utilities", content_kind="software"))
    db.flush()
    db.add(Review(id=15, user_id=CARA, game_id=3, rating=5, verified_playtime_minutes=9000 * HOUR))
    link(db, CARA)
    verify(db, 13, 120)
    db.commit()
    shown = profile(api, CARA)
    # The stat in the header and the badge beside the name are the same hours.
    assert shown["badge"] == 100 and shown["counts"]["verified_minutes"] == 120 * HOUR


def test_a_deleted_review_takes_its_hours_with_it(api, db):
    link(db, BOB)
    verify(db, 12, 60)
    verify(db, 14, 60)
    db.commit()
    assert profile(api, BOB)["badge"] == 100
    assert api.delete("/reviews/14", headers=who(api, BOB)).status_code == 204
    assert profile(api, BOB)["badge"] is None


# ---------------------------------------------------------------- where it shows

def test_reviews_and_feed_posts_carry_the_author_badge(api, db):
    link(db, CARA)
    verify(db, 13, 1500)
    db.commit()
    reviews = {r["id"]: r["author_badge"] for r in api.get("/games/1/reviews").json()}
    assert reviews == {11: None, 12: None, 13: 1000}
    feed = {item["review"]["id"]: item["review"]["author_badge"] for item in api.get("/feed").json()["items"]}
    assert feed[13] == 1000 and feed[11] is None
    assert api.get("/reviews/13").json()["review"]["author_badge"] == 1000


def test_profiles_show_the_badge_to_guests_and_on_limited_profiles(api, db):
    link(db, CARA)
    verify(db, 13, 100)
    db.get(User, CARA).profile_visibility = "followers"
    db.commit()
    limited = profile(api, CARA)
    assert limited["limited"] is True and limited["badge"] == 100
    assert profile(api, CARA, viewer=CARA)["badge"] == 100


def test_follow_lists_carry_badges(api, db):
    link(db, ALICE)
    verify(db, 11, 250)
    db.commit()
    assert follow(api, ALICE, BOB).status_code == 200
    page = api.get(f"/users/{BOB}/followers", headers=who(api, CARA)).json()
    assert page["items"] == [{"id": ALICE, "display_name": "Alice", "handle": None, "badge": 100, "avatar_url": None}]


def test_who_to_follow_carries_badges(api, db):
    link(db, CARA)
    verify(db, 13, 5200)
    db.commit()
    items = {item["id"]: item["badge"] for item in api.get("/explore/suggestions", headers=who(api, ALICE)).json()["items"]}
    assert items[CARA] == 5000 and items[BOB] is None


def test_reposts_and_quotes_carry_the_reposter_badge(api, db):
    link(db, BOB)
    verify(db, 12, 1000)
    db.commit()
    assert follow(api, ALICE, BOB).status_code == 200
    assert repost(api, BOB, 13, "Agree with every word").status_code in (200, 201)
    items = api.get("/me/following-feed", headers=who(api, ALICE)).json()["items"]
    quote = next(item for item in items if item.get("repost"))
    assert quote["repost"]["author_badge"] == 1000
    assert quote["review"]["author_badge"] is None


def test_trending_reviews_carry_badges(api, db):
    link(db, CARA)
    verify(db, 13, 100)
    db.commit()
    trending = {item["review"]["id"]: item["review"]["author_badge"] for item in api.get("/explore").json()["reviews"]}
    assert trending[13] == 100
