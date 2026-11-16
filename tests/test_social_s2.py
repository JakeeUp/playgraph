"""Reposts, quote reviews and private bookmarks.

Alice (1), Bob (2) and Cara (3) have posted. Reviews: 11 Alice, 12 Bob, 13 Cara
on game 1; 14 Bob on game 2. Dan (4) has not posted.
"""
from app.config import settings
from app.models import Bookmark, Report, Repost, User
from tests.test_social import ALICE, BOB, CARA, DAN, api, block, cookie_session, follow, report, store, who  # noqa: F401


def repost(api, user_id, review_id, quote=None):
    body = {} if quote is None else {"quote": quote}
    return api.post(f"/reviews/{review_id}/repost", json=body, headers=who(api, user_id))


def save(api, user_id, review_id):
    return api.post(f"/reviews/{review_id}/bookmark", headers=who(api, user_id))


# ---------------------------------------------------------------- reposts

def test_plain_repost_is_one_per_player_and_counted(api):
    first = repost(api, ALICE, 13)
    assert first.status_code == 200
    assert first.json() == {"review_id": 13, "reposted": True, "repost_count": 1}
    assert repost(api, ALICE, 13).json()["repost_count"] == 1
    assert repost(api, BOB, 13).json()["repost_count"] == 2
    counts = {row["id"]: row["repost_count"] for row in api.get("/games/1/reviews").json()}
    assert counts[13] == 2 and counts[11] == 0
    assert "bookmarked" not in api.get("/games/1/reviews").json()[0]
    gone = api.delete("/reviews/13/repost", headers=who(api, ALICE))
    assert gone.json() == {"review_id": 13, "reposted": False, "repost_count": 1}
    mine = api.get("/me/reposts?review_ids=11,13", headers=who(api, BOB))
    assert mine.json() == {"reposted": [13]}


def test_you_cannot_repost_your_own_review(api):
    assert repost(api, ALICE, 11).status_code == 422
    assert repost(api, ALICE, 11, quote="Me again").status_code == 422
    assert repost(api, ALICE, 999).status_code == 404
    assert api.post("/reviews/13/repost", json={}).status_code == 401


def test_a_quote_is_its_own_row_and_does_not_replace_the_plain_repost(api, db):
    assert repost(api, ALICE, 13).status_code == 200
    quoted = repost(api, ALICE, 13, quote="  The hours check out.\nReally.  ")
    assert quoted.status_code == 200
    body = quoted.json()
    assert body["reposted"] is True and body["repost_count"] == 2 and body["quote_id"]
    assert db.get(Repost, body["quote_id"]).quote == "The hours check out. Really."
    again = repost(api, ALICE, 13, quote="Second thought")
    assert again.json()["repost_count"] == 3
    removed = api.delete(f"/reposts/{body['quote_id']}", headers=who(api, ALICE))
    assert removed.json()["repost_count"] == 2 and removed.json()["reposted"] is True
    # Bob cannot delete Alice's quote, and a plain repost is not deleted this way.
    plain = db.query(Repost).filter_by(user_id=ALICE, quote=None).one()
    assert api.delete(f"/reposts/{plain.id}", headers=who(api, ALICE)).status_code == 404
    assert api.delete(f"/reposts/{again.json()['quote_id']}", headers=who(api, BOB)).status_code == 404


def test_quotes_are_reportable(api, db):
    quote_id = repost(api, BOB, 13, quote="Skip the ending").json()["quote_id"]
    assert report(api, ALICE, target_type="repost", target_id=quote_id).status_code == 201
    assert report(api, ALICE, target_type="repost", target_id=quote_id).status_code == 409
    assert report(api, BOB, target_type="repost", target_id=quote_id).status_code == 422
    assert report(api, ALICE, target_type="repost", target_id=999).status_code == 404
    assert db.query(Report).filter_by(target_type="repost", target_id=quote_id).count() == 1


def test_blocks_hide_reposts_both_ways(api):
    block(api, CARA, ALICE)
    assert repost(api, ALICE, 13).status_code == 404
    assert repost(api, CARA, 11).status_code == 404
    assert repost(api, BOB, 13).status_code == 200


def test_deleting_a_review_removes_reposts_and_bookmarks(api, db):
    repost(api, ALICE, 14)
    repost(api, ALICE, 14, quote="Gone soon")
    save(api, CARA, 14)
    assert api.delete("/reviews/14", headers=who(api, BOB)).status_code == 204
    assert db.query(Repost).filter_by(review_id=14).count() == 0
    assert db.query(Bookmark).filter_by(review_id=14).count() == 0


def test_browser_reposts_need_origin_and_csrf(api):
    csrf = cookie_session(api, ALICE)
    for headers in ({}, {"Origin": settings.app_base_url}, {"X-CSRF-Token": csrf},
                    {"Origin": "https://attacker.example", "X-CSRF-Token": csrf}):
        assert api.post("/reviews/13/repost", json={}, headers=headers).status_code == 403
    good = {"Origin": settings.app_base_url, "X-CSRF-Token": csrf}
    assert api.post("/reviews/13/repost", json={}, headers=good).status_code == 200
    api.cookies.clear()


# ---------------------------------------------------------------- feeds

def test_following_feed_shows_reposts_and_quotes_from_people_you_follow(api):
    follow(api, ALICE, BOB)
    follow(api, ALICE, CARA)
    repost(api, BOB, 13)
    repost(api, CARA, 12, quote="Bob undersold this")
    # Dan is not followed, so his repost stays out.
    repost(api, DAN, 13)
    feed = api.get("/me/following-feed", headers=who(api, ALICE)).json()
    reasons = [item["reason"] for item in feed["items"]]
    assert "Bob reposted" in reasons and "Cara quoted" in reasons
    quoted = next(item for item in feed["items"] if item.get("repost", {}).get("quote"))
    assert quoted["repost"]["quote"] == "Bob undersold this"
    assert quoted["review"]["id"] == 12 and quoted["repost"]["author_name"] == "Cara"
    assert all(item.get("repost", {}).get("user_id") != DAN for item in feed["items"])
    # A new repost after the first page must not shift the next page.
    first = api.get("/me/following-feed?limit=1", headers=who(api, ALICE)).json()
    repost(api, BOB, 11, quote="Too new for this page")
    second = api.get("/me/following-feed", params={"limit": 1, "offset": 1, "before": first["before"],
                     "repost_before": first["repost_before"]}, headers=who(api, ALICE)).json()
    assert second["total"] == first["total"]


def test_a_private_profile_repost_does_not_spread(api):
    follow(api, ALICE, BOB)
    repost(api, BOB, 13, quote="Only for people who can see me")
    assert api.patch("/me/profile", json={"profile_visibility": "private"}, headers=who(api, BOB)).status_code == 200
    reasons = [item["reason"] for item in api.get("/me/following-feed", headers=who(api, ALICE)).json()["items"]]
    assert "Bob quoted" not in reasons
    # His own reviews still show: that rule did not change.
    assert any(item["review"]["user_id"] == BOB and "repost" not in item for item in
               api.get("/me/following-feed", headers=who(api, ALICE)).json()["items"])


def test_followers_only_reposts_reach_accepted_followers(api):
    assert api.patch("/me/profile", json={"profile_visibility": "followers"}, headers=who(api, BOB)).status_code == 200
    follow(api, ALICE, BOB)
    request_id = api.get("/me/follow-requests", headers=who(api, BOB)).json()["items"][0]["id"]
    # Still pending: Bob can quote, but Alice cannot see it yet.
    repost(api, BOB, 13, quote="After you are in")
    pending = api.get("/me/following-feed", headers=who(api, ALICE)).json()
    assert pending["total"] == 0
    assert api.post(f"/me/follow-requests/{request_id}/accept", headers=who(api, BOB)).status_code == 200
    reasons = [item["reason"] for item in api.get("/me/following-feed", headers=who(api, ALICE)).json()["items"]]
    assert "Bob quoted" in reasons
    # Cara does not follow Bob, so For You does not hand her the quote.
    cara = [item.get("repost", {}).get("user_id") for item in api.get("/me/feed", headers=who(api, CARA)).json()["items"]]
    assert BOB not in cara


def test_a_block_hides_a_repost_of_the_blocked_author(api):
    follow(api, ALICE, BOB)
    repost(api, BOB, 13)
    block(api, ALICE, CARA)
    items = api.get("/me/following-feed", headers=who(api, ALICE)).json()["items"]
    assert all(item["review"]["user_id"] != CARA for item in items)


def test_for_you_includes_a_quote_and_a_popular_repost(api):
    repost(api, BOB, 11, quote="Play this")
    repost(api, ALICE, 14)
    # One plain repost is not popular yet. A quote is.
    alone = api.get("/me/feed", headers=who(api, CARA)).json()["items"]
    assert any(item.get("repost", {}).get("quote") == "Play this" for item in alone)
    assert all(item.get("repost", {}).get("quote") for item in alone if item.get("repost"))
    repost(api, CARA, 14)
    # Two plain reposts make Bob's review popular. Alice does not see her own.
    items = api.get("/me/feed", headers=who(api, ALICE)).json()["items"]
    assert any(item.get("reason") == "Cara reposted" for item in items)
    assert all(item.get("repost", {}).get("user_id") != ALICE for item in items if item.get("repost"))


# ---------------------------------------------------------------- bookmarks

def test_bookmarks_are_private_to_the_owner(api, db):
    assert save(api, ALICE, 13).json() == {"review_id": 13, "bookmarked": True}
    assert save(api, ALICE, 13).json()["bookmarked"] is True
    assert save(api, ALICE, 12).status_code == 200
    listed = api.get("/me/bookmarks", headers=who(api, ALICE)).json()
    assert listed["total"] == 2
    assert [item["review"]["id"] for item in listed["items"]] == [12, 13]
    assert api.get("/me/bookmarks?review_ids=12,13,14", headers=who(api, ALICE)).json() == {"bookmarked": [12, 13]}
    # Bob's list is empty, and nothing on a public profile mentions Alice's saves.
    assert api.get("/me/bookmarks", headers=who(api, BOB)).json() == {"items": [], "total": 0}
    profile = api.get(f"/users/{ALICE}", headers=who(api, BOB)).json()
    assert "bookmarks" not in profile
    assert db.query(Bookmark).count() == 2
    assert api.delete("/reviews/13/bookmark", headers=who(api, ALICE)).json()["bookmarked"] is False
    assert api.get("/me/bookmarks", headers=who(api, ALICE)).json()["total"] == 1
    assert api.get("/me/bookmarks").status_code == 401
    assert save(api, ALICE, 999).status_code == 404


def test_a_block_hides_a_bookmark_of_that_author(api):
    save(api, ALICE, 13)
    block(api, ALICE, CARA)
    assert api.get("/me/bookmarks", headers=who(api, ALICE)).json()["total"] == 0
    assert save(api, ALICE, 13).status_code == 404


def test_repost_and_bookmark_budgets(api):
    for i in range(60):
        if i and i % 20 == 0:
            api.store.now += 121
        assert repost(api, ALICE, 13, quote=f"note {i}").status_code == 200
    api.store.now += 121
    assert repost(api, ALICE, 13, quote="over").status_code == 429
    # Bob still has his own budget. Bookmarks have theirs too.
    assert repost(api, BOB, 13).status_code == 200
    for i in range(120):
        if i and i % 20 == 0:
            api.store.now += 121
        assert api.post("/reviews/12/bookmark", headers=who(api, CARA)).status_code != 429
    api.store.now += 121
    assert api.post("/reviews/12/bookmark", headers=who(api, CARA)).status_code == 429
