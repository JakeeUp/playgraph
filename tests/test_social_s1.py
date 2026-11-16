"""@handles, likes, favorites and the richer profile, with the synthetic accounts
from test_social: Alice (1), Bob (2) and Cara (3) have posted; Dan (4) has not.
Reviews: 11 Alice, 12 Bob, 13 Cara on game 1; 14 Bob on game 2.
"""
import pytest

from app import handles
from app.config import settings
from app.models import Favorite, Game, Review, ReviewLike, User
from app.routers import social
from tests.test_social import ALICE, BOB, CARA, DAN, api, block, cookie_session, store, who  # noqa: F401


def like(api, user_id, review_id):
    return api.post(f"/reviews/{review_id}/like", headers=who(api, user_id))


# ---------------------------------------------------------------- handles

@pytest.mark.parametrize("raw,expected", [("@Alice_99", "alice_99"), ("  bob  ", "bob"), ("x1y", "x1y")])
def test_handles_are_normalized(raw, expected):
    assert handles.normalize(raw) == expected and handles.problem(expected) is None


@pytest.mark.parametrize("bad", ["ab", "a" * 16, "123456", "has space", "dash-ed", "émile", "аlice",
                                 "admin", "playgraph", "the_admin", "playgraph_help", "steam", "me"])
def test_bad_or_reserved_handles_are_refused(bad):
    assert handles.problem(handles.normalize(bad)) is not None


@pytest.mark.parametrize("name,base", [("Alice Smith", "alice_smith"), ("Émile", "emile"), ("!!", "player"),
                                       ("12345", "player"), ("Admin", "player"), ("A Very Long Display Name", "a_very_long_dis")])
def test_generated_handles_start_from_the_name(name, base):
    assert handles.base_for(name) == base


def test_generated_handles_avoid_taken_ones(db):
    db.add(User(id=50, display_name="Alice", handle="alice"))
    db.commit()
    fresh = handles.available(db, "Alice")
    assert fresh != "alice" and fresh.startswith("alice") and handles.problem(fresh) is None


def test_set_handle_and_find_the_profile_by_it(api):
    response = api.patch("/me/profile", json={"handle": "@Alice_Plays"}, headers=who(api, ALICE))
    assert response.status_code == 200 and response.json()["handle"] == "alice_plays"
    found = api.get("/users/by-handle/ALICE_PLAYS")
    assert found.status_code == 200 and found.json()["id"] == ALICE
    assert api.get("/users/by-handle/nobody_here").status_code == 404


def test_handles_are_unique_and_validated(api):
    assert api.patch("/me/profile", json={"handle": "same_name"}, headers=who(api, ALICE)).status_code == 200
    assert api.patch("/me/profile", json={"handle": "Same_Name"}, headers=who(api, BOB)).status_code == 409
    for bad in ("admin", "no", "x" * 20, None):
        assert api.patch("/me/profile", json={"handle": bad}, headers=who(api, BOB)).status_code == 422
    # Keeping your own handle is not a clash.
    assert api.patch("/me/profile", json={"handle": "same_name"}, headers=who(api, ALICE)).status_code == 200


def test_handle_lookup_keeps_the_findability_rules(api):
    api.patch("/me/profile", json={"handle": "dan_quiet"}, headers=who(api, DAN))
    # Dan has never posted or followed anyone, so guessing his handle finds nothing.
    assert api.get("/users/by-handle/dan_quiet").status_code == 404


def test_reviews_carry_the_author_handle(api):
    api.patch("/me/profile", json={"handle": "bobby"}, headers=who(api, BOB))
    reviews = api.get("/games/1/reviews").json()
    assert {r["user_id"]: r["author_handle"] for r in reviews}[BOB] == "bobby"


# ---------------------------------------------------------------- likes

def test_like_unlike_and_counts(api):
    first = like(api, ALICE, 13)
    assert first.status_code == 200 and first.json() == {"review_id": 13, "liked": True, "like_count": 1}
    # Liking twice is still one like.
    assert like(api, ALICE, 13).json()["like_count"] == 1
    assert like(api, BOB, 13).json()["like_count"] == 2
    counts = {r["id"]: r["like_count"] for r in api.get("/games/1/reviews").json()}
    assert counts[13] == 2 and counts[11] == 0
    gone = api.delete("/reviews/13/like", headers=who(api, ALICE))
    assert gone.json() == {"review_id": 13, "liked": False, "like_count": 1}
    assert api.delete("/reviews/13/like", headers=who(api, ALICE)).json()["like_count"] == 1


def test_my_likes_lists_only_my_own(api):
    like(api, ALICE, 13)
    like(api, ALICE, 14)
    like(api, BOB, 12)
    mine = api.get("/me/likes?review_ids=11,12,13,14,999", headers=who(api, ALICE))
    assert mine.json() == {"liked": [13, 14]}
    assert api.get("/me/likes?review_ids=", headers=who(api, ALICE)).json() == {"liked": []}
    assert api.get("/me/likes?review_ids=1,x", headers=who(api, ALICE)).status_code == 422
    too_many = ",".join(str(n) for n in range(1, 102))
    assert api.get(f"/me/likes?review_ids={too_many}", headers=who(api, ALICE)).status_code == 422
    assert api.get("/me/likes?review_ids=13").status_code == 401


def test_feed_items_carry_like_counts(api):
    like(api, BOB, 13)
    items = api.get("/feed").json()["items"]
    assert {item["review"]["id"]: item["review"]["like_count"] for item in items}[13] == 1


def test_profile_counts_likes_received_and_verified_hours(api, db):
    db.get(Review, 13).verified_playtime_minutes = 600
    db.commit()
    like(api, ALICE, 13)
    like(api, BOB, 13)
    profile = api.get(f"/users/{CARA}").json()
    assert profile["counts"]["likes_received"] == 2 and profile["counts"]["verified_minutes"] == 600
    assert profile["banner_game"]["id"] == 1


def test_blocks_stop_likes_both_ways(api):
    block(api, CARA, ALICE)
    assert like(api, ALICE, 13).status_code == 404
    assert like(api, CARA, 11).status_code == 404
    assert like(api, BOB, 13).status_code == 200


def test_unknown_review_and_signed_out(api):
    assert like(api, ALICE, 999).status_code == 404
    assert api.post("/reviews/13/like").status_code == 401
    assert api.delete("/reviews/13/like").status_code == 401


def test_deleting_a_review_removes_its_likes(api, db):
    like(api, ALICE, 14)
    assert api.delete("/reviews/14", headers=who(api, BOB)).status_code == 204
    assert db.query(ReviewLike).filter_by(review_id=14).count() == 0


def test_browser_likes_need_origin_and_csrf(api):
    csrf = cookie_session(api, ALICE)
    for headers in ({}, {"Origin": settings.app_base_url}, {"X-CSRF-Token": csrf},
                    {"Origin": "https://attacker.example", "X-CSRF-Token": csrf}):
        assert api.post("/reviews/13/like", headers=headers).status_code == 403
    good = {"Origin": settings.app_base_url, "X-CSRF-Token": csrf}
    assert api.post("/reviews/13/like", headers=good).status_code == 200
    api.cookies.clear()


# ---------------------------------------------------------------- favorites

def test_pin_reorder_and_clear_favorites(api):
    saved = api.put("/me/favorites", json={"game_ids": [2, 1]}, headers=who(api, ALICE))
    assert saved.status_code == 200 and [g["id"] for g in saved.json()["favorites"]] == [2, 1]
    assert [g["id"] for g in api.get(f"/users/{ALICE}").json()["favorites"]] == [2, 1]
    assert [g["id"] for g in api.put("/me/favorites", json={"game_ids": [1, 2]}, headers=who(api, ALICE)).json()["favorites"]] == [1, 2]
    assert api.put("/me/favorites", json={"game_ids": []}, headers=who(api, ALICE)).json()["favorites"] == []


@pytest.mark.parametrize("body,code", [({"game_ids": [1, 1]}, 422), ({"game_ids": [1, 2, 3, 4, 5]}, 422),
                                       ({"game_ids": [999]}, 404), ({"game_ids": [0]}, 422), ({}, 422),
                                       ({"game_ids": [1], "crown": 1}, 422)])
def test_bad_favorites_are_refused(api, body, code):
    assert api.put("/me/favorites", json=body, headers=who(api, ALICE)).status_code == code


def test_favorites_follow_profile_visibility(api, db):
    api.put("/me/favorites", json={"game_ids": [1]}, headers=who(api, ALICE))
    db.get(User, ALICE).profile_visibility = "followers"
    db.commit()
    assert "favorites" not in api.get(f"/users/{ALICE}", headers=who(api, BOB)).json()
    assert [g["id"] for g in api.get(f"/users/{ALICE}", headers=who(api, ALICE)).json()["favorites"]] == [1]


def test_a_failed_save_keeps_the_old_favorites(api, db):
    api.put("/me/favorites", json={"game_ids": [1, 2]}, headers=who(api, ALICE))
    assert api.put("/me/favorites", json={"game_ids": [2, 999]}, headers=who(api, ALICE)).status_code == 404
    assert [row.game_id for row in db.query(Favorite).filter_by(user_id=ALICE).order_by(Favorite.position)] == [1, 2]
