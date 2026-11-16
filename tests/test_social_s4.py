"""Owner-only, grouped notifications and their lifecycle."""
from datetime import timedelta

import pytest

from app.config import settings
from app.models import Notification, User, utcnow
from app.notifications import prune
from tests.test_social import ALICE, BOB, CARA, api, block, cookie_session, follow, store, who  # noqa: F401


def inbox(api, user=ALICE, query=""):
    response = api.get("/me/notifications" + query, headers=who(api, user))
    assert response.status_code == 200
    return response.json()


def like(api, actor=BOB, review=11):
    assert api.post(f"/reviews/{review}/like", headers=who(api, actor)).status_code == 200


def read(api, through, user=ALICE):
    return api.post(f"/me/notifications/read?through_id={through}", headers=who(api, user))


def test_like_is_once_grouped_and_only_the_recipient_can_see_names(api):
    like(api); like(api); like(api, CARA); like(api, ALICE)
    body = inbox(api)
    assert body["total"] == 1 and body["unread_count"] == 2
    item = body["items"][0]
    assert item["kind"] == "like" and item["review_id"] == 11
    assert item["actor_count"] == 2 and item["unread_count"] == 2
    assert {actor["id"] for actor in item["actors"]} == {BOB, CARA}
    assert inbox(api, BOB)["items"] == []
    assert "actors" not in api.get("/reviews/11").json()["review"]
    assert api.get("/me/notifications/unread", headers=who(api, ALICE)).json() == {"unread_count": 2}


def test_unlike_removes_unread_but_keeps_read_history(api, db):
    like(api)
    assert api.delete("/reviews/11/like", headers=who(api, BOB)).status_code == 200
    assert inbox(api)["items"] == []
    like(api)
    assert read(api, inbox(api)["latest_id"]).json() == {"unread_count": 0}
    assert api.delete("/reviews/11/like", headers=who(api, BOB)).status_code == 200
    assert inbox(api)["items"][0]["unread_count"] == 0
    assert db.query(Notification).count() == 1


def test_repost_quote_and_comment_emit_after_success_not_on_self(api):
    headers = who(api, BOB)
    assert api.post("/reviews/11/repost", headers=headers).status_code == 200
    assert api.post("/reviews/11/repost", headers=headers).status_code == 200
    assert api.post("/reviews/11/repost", headers=headers, json={"quote": "Agreed"}).status_code == 200
    reply = api.post("/reviews/11/comments", headers=headers, json={"body": "Great review"})
    assert reply.status_code == 201
    assert api.post("/reviews/11/comments", headers=who(api, ALICE), json={"body": "Thanks"}).status_code == 201
    assert api.post("/reviews/999/repost", headers=headers).status_code == 404
    body = inbox(api)
    assert {row["kind"] for row in body["items"]} == {"repost", "quote", "comment"}
    assert body["unread_count"] == 3
    assert next(row for row in body["items"] if row["kind"] == "comment")["comment_id"] == reply.json()["id"]
    assert api.delete("/reviews/11/repost", headers=headers).status_code == 200
    assert inbox(api)["unread_count"] == 2


def test_follow_and_acceptance_notify_each_side_once(api):
    assert follow(api, BOB, ALICE).status_code == 200
    assert follow(api, BOB, ALICE).status_code == 200
    assert inbox(api)["items"][0]["kind"] == "follow"
    assert inbox(api)["unread_count"] == 1
    assert api.patch("/me/profile", headers=who(api, CARA), json={"profile_visibility": "followers"}).status_code == 200
    assert follow(api, ALICE, CARA).json()["requested"] is True
    assert inbox(api, CARA)["items"] == []
    request_id = api.get("/me/follow-requests", headers=who(api, CARA)).json()["items"][0]["id"]
    assert api.post(f"/me/follow-requests/{request_id}/accept", headers=who(api, CARA)).status_code == 200
    assert api.post(f"/me/follow-requests/{request_id}/accept", headers=who(api, CARA)).status_code == 404
    assert {item["kind"] for item in inbox(api)["items"]} == {"follow", "follow_accepted"}
    assert inbox(api, CARA)["items"][0]["kind"] == "follow"


def test_becoming_public_notifies_auto_accepted_requests(api):
    headers = who(api, BOB)
    api.patch("/me/profile", headers=headers, json={"profile_visibility": "followers"})
    follow(api, ALICE, BOB)
    api.patch("/me/profile", headers=headers, json={"profile_visibility": "public"})
    api.patch("/me/profile", headers=headers, json={"bio": "Still public"})
    assert inbox(api)["unread_count"] == 1
    assert inbox(api)["items"][0]["kind"] == "follow_accepted"


@pytest.mark.parametrize("blocker,blocked", [(ALICE, BOB), (BOB, ALICE)])
def test_block_removes_both_directions_and_stops_new_events(api, db, blocker, blocked):
    like(api); like(api, ALICE, 12)
    block(api, blocker, blocked)
    assert db.query(Notification).count() == 0
    assert inbox(api)["unread_count"] == inbox(api, BOB)["unread_count"] == 0
    assert api.post("/reviews/11/like", headers=who(api, BOB)).status_code == 404
    assert api.post("/reviews/11/comments", headers=who(api, BOB), json={"body": "No"}).status_code == 404
    assert api.delete(f"/users/{blocked}/block", headers=who(api, blocker)).status_code == 204
    assert inbox(api)["items"] == []


def test_existing_block_filters_count_even_if_a_racing_event_was_inserted(api, db):
    block(api, ALICE, BOB)
    db.add(Notification(recipient_id=ALICE, actor_id=BOB, kind="like", review_id=11))
    db.commit()
    assert inbox(api)["items"] == []
    assert api.get("/me/notifications/unread", headers=who(api, ALICE)).json()["unread_count"] == 0


def test_quotes_obey_profile_visibility_and_accepted_followers(api):
    headers = who(api, BOB)
    api.post("/reviews/11/repost", headers=headers, json={"quote": "A quote"})
    api.patch("/me/profile", headers=headers, json={"profile_visibility": "followers"})
    assert inbox(api)["items"] == []
    follow(api, ALICE, BOB)
    request_id = api.get("/me/follow-requests", headers=headers).json()["items"][0]["id"]
    api.post(f"/me/follow-requests/{request_id}/accept", headers=headers)
    assert "quote" in {item["kind"] for item in inbox(api)["items"]}
    api.patch("/me/profile", headers=headers, json={"profile_visibility": "private"})
    assert "quote" not in {item["kind"] for item in inbox(api)["items"]}


def test_read_is_recipient_scoped_idempotent_and_keeps_new_arrivals_unread(api):
    like(api); like(api, ALICE, 12)
    snapshot = inbox(api)["latest_id"]
    like(api, CARA)
    assert read(api, snapshot).json() == {"unread_count": 1}
    assert read(api, snapshot).json() == {"unread_count": 1}
    assert inbox(api, BOB)["unread_count"] == 1
    assert read(api, inbox(api)["latest_id"]).json() == {"unread_count": 0}


def test_groups_separate_reviews_comments_and_utc_days_and_page_after_grouping(api, db):
    yesterday = utcnow() - timedelta(days=1)
    db.add(Notification(recipient_id=ALICE, actor_id=BOB, kind="like", review_id=11, created_at=yesterday))
    db.commit()
    like(api, CARA)
    assert inbox(api)["total"] == 2
    first = inbox(api, query="?limit=1")
    second = inbox(api, query="?limit=1&offset=1")
    assert first["total"] == second["total"] == 2
    assert first["items"][0]["id"] != second["items"][0]["id"]


def test_large_group_returns_only_three_names_and_counts_distinct_people(api, db):
    for person in range(20, 30):
        db.add(User(id=person, display_name=f"Player {person}"))
    db.flush()
    for person in range(20, 30):
        db.add(Notification(recipient_id=ALICE, actor_id=person, kind="like", review_id=11))
    db.add(Notification(recipient_id=ALICE, actor_id=20, kind="like", review_id=11))
    db.commit()
    group = inbox(api)["items"][0]
    assert group["actor_count"] == 10 and len(group["actors"]) == 3 and group["unread_count"] == 11


def test_old_events_are_hidden_and_pruned_for_every_recipient(api, db):
    db.add_all([Notification(recipient_id=ALICE, actor_id=BOB, kind="follow", created_at=utcnow() - timedelta(days=91)),
                Notification(recipient_id=BOB, actor_id=CARA, kind="follow", created_at=utcnow() - timedelta(days=91))])
    db.commit()
    like(api)
    assert inbox(api)["unread_count"] == 1
    assert prune(db) == 2
    db.commit()
    assert db.query(Notification).count() == 1


def test_deleting_review_removes_all_its_notifications(api, db):
    like(api)
    api.post("/reviews/11/comments", headers=who(api, BOB), json={"body": "Hello"})
    assert api.delete("/reviews/11", headers=who(api, ALICE)).status_code == 204
    assert db.query(Notification).count() == 0 and inbox(api)["items"] == []


def test_notification_routes_require_session_and_browser_writes_require_csrf(api):
    for path in ("/me/notifications", "/me/notifications/unread"):
        assert api.get(path).status_code == 401
    csrf = cookie_session(api, ALICE)
    path = "/me/notifications/read?through_id=0"
    for headers in ({}, {"Origin": settings.app_base_url}, {"Origin": "https://elsewhere.example", "X-CSRF-Token": csrf}):
        assert api.post(path, headers=headers).status_code == 403
    assert api.post(path, headers={"Origin": settings.app_base_url, "X-CSRF-Token": csrf}).status_code == 200
    api.cookies.clear()
    assert api.post(path).status_code == 401


def test_read_has_its_own_hourly_budget_and_inputs_are_bounded(api):
    for query in ("?limit=51", "?offset=-1"):
        assert api.get("/me/notifications" + query, headers=who(api, ALICE)).status_code == 422
    for query in ("", "?through_id=-1", "?through_id=2147483648"):
        assert api.post("/me/notifications/read" + query, headers=who(api, ALICE)).status_code == 422
    # Validation happens after the dependency spends its budget.
    for _ in range(117):
        api.store.now += 3
        assert read(api, 0).status_code == 200
    assert read(api, 0).status_code == 429
