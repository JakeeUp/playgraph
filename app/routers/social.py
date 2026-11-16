"""Profiles, follows, blocks, reports and the owner's report queue.

The rules, all enforced here or in app/visibility.py:
- A block hides both people's reviews and comments from each other, and
  removes any follow between them. It is never announced: the blocker's
  profile answers the blocked player with the same 404 as a missing user.
- A private profile is 404 to everyone but its owner. A followers-only
  profile shows its name and counts to everyone else, nothing more, and a
  follow of it is only a request until the owner accepts it. Pending
  requests count for nothing.
- Follower and following lists need a session and follow the same profile rules.
- A player who has never posted or followed anyone cannot be found by
  guessing IDs; see is_public_participant.
- Every write needs a session, and from a browser the exact Origin and the
  CSRF token (get_current_user). Each kind of write has its own hourly budget.
"""
import logging
import time
import hashlib
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func, or_, and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, contains_eager, joinedload

from app.config import settings
from app.database import get_db
from app.deps import get_current_user, get_optional_user
from app import handles, notifications
from app.models import (MAX_FAVORITES, PLAY_STATUSES, QUOTE_MAX, Block, Bookmark, Comment, Favorite, Follow, Game, ProfileImage,
                        GameStatus, Notification, Report, Repost, Review, ReviewLike, User, utcnow)
from app.routers.feed import _anchor, _items, _query, _utc, repost_card
from app.routers.parameters import MAX_OFFSET, MAX_RESOURCE_ID, ResourceId
from app.schemas import (FavoritesUpdate, GameOut, ProfileUpdate, ReportCreate, ReportResolve, RepostCreate,
                         UserCard)
from app.security import rate_limit
from app.cache import REVIEWS, invalidate_from_route
from app.profile_images import normalize_image
from app.visibility import (blocked_by, blocked_either_way, follow_status, follows, is_public_participant,
                            not_hidden, reposter_reachable)

router = APIRouter(tags=["social"])
logger = logging.getLogger("playgraph.moderation")

NOT_FOUND = "User not found"
RECENT_REVIEWS = 10
# Caps keep the follow and block subqueries small. Nobody normal gets near them.
MAX_FOLLOWING = 1000
MAX_BLOCKS = 1000
HOUR = 3600
# Closing a report needs a session signed in within this many seconds.
MODERATOR_FRESHNESS = 15 * 60


def _budget(bucket: str, limit: int):
    """A signed-in user who still has budget for this kind of write this hour."""
    async def spend(request: Request, user: User = Depends(get_current_user)) -> User:
        await rate_limit(request, bucket, str(user.id), limit, HOUR)
        return user
    return spend


follow_budget = _budget("follow", 60)
block_budget = _budget("block", 30)
report_budget = _budget("report", 20)
profile_budget = _budget("profile", 20)
like_budget = _budget("like", 300)
repost_budget = _budget("repost", 60)
bookmark_budget = _budget("bookmark", 120)
notification_budget = _budget("notification-read", 120)
image_budget = _budget("profile-images", 12)
MAX_LIKE_LOOKUP = 100


@router.put("/me/profile-images/{kind}")
def upload_profile_image(kind: Literal["avatar", "cover"], request: Request,
                         payload: bytes = Body(..., media_type="application/octet-stream"),
                         db: Session = Depends(get_db), user: User = Depends(image_budget)):
    data = normalize_image(payload, kind)
    key = hashlib.sha256(data).hexdigest()[:32]
    row = db.query(ProfileImage).filter_by(user_id=user.id, kind=kind).first()
    if row is None:
        row = ProfileImage(user_id=user.id, kind=kind)
        db.add(row)
    row.key, row.data = key, data
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Your image changed in another tab. Try again.") from None
    invalidate_from_route(request, REVIEWS)
    return {"url": f"/users/{user.id}/images/{kind}?v={key}"}


@router.delete("/me/profile-images/{kind}", status_code=204)
def delete_profile_image(kind: Literal["avatar", "cover"], request: Request,
                         db: Session = Depends(get_db), user: User = Depends(image_budget)):
    db.query(ProfileImage).filter_by(user_id=user.id, kind=kind).delete(synchronize_session=False)
    db.commit()
    invalidate_from_route(request, REVIEWS)
    return Response(status_code=204)


@router.get("/users/{user_id}/images/{kind}")
def profile_image(user_id: ResourceId, kind: Literal["avatar", "cover"], v: str = Query("", max_length=32),
                  db: Session = Depends(get_db), viewer: User | None = Depends(get_optional_user)):
    target = _findable(db, viewer, user_id)
    if _access(db, viewer, target) not in ("owner", "full"):
        _missing()
    row = db.query(ProfileImage).filter_by(user_id=user_id, kind=kind).first()
    if row is None or (v and v != row.key):
        _missing()
    return Response(row.data, media_type="image/webp", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/me/notifications")
def my_notifications(limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0, le=MAX_OFFSET),
                     db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return notifications.inbox(db, user.id, limit, offset)


@router.get("/me/notifications/unread")
def unread_notifications(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return {"unread_count": notifications.unread_count(db, user.id)}


@router.post("/me/notifications/read")
def read_notifications(through_id: int = Query(..., ge=0, le=MAX_RESOURCE_ID),
                       db: Session = Depends(get_db), user: User = Depends(notification_budget)):
    # The inbox snapshot leaves notifications arriving after it unread.
    db.query(Notification).filter(Notification.recipient_id == user.id, Notification.id <= through_id,
                                  Notification.read_at.is_(None)).update(
        {Notification.read_at: utcnow()}, synchronize_session=False)
    db.commit()
    return {"unread_count": notifications.unread_count(db, user.id)}


async def list_budget(request: Request, user: User = Depends(get_current_user)) -> User:
    """Paging through who follows whom is the one read here that could map the
    social graph, so it has its own per-minute budget on top of the general one."""
    await rate_limit(request, "follow-lists", str(user.id), 30, 60)
    return user


def _missing():
    raise HTTPException(status_code=404, detail=NOT_FOUND)


def _findable(db: Session, viewer: User | None, user_id: int) -> User:
    """The user, if this viewer could already know they exist; otherwise 404."""
    target = db.get(User, user_id)
    if target is None:
        _missing()
    if viewer is not None and (viewer.id == target.id or follow_status(db, viewer.id, target.id) is not None
                               or blocked_by(db, viewer.id, target.id)):
        return target
    if not is_public_participant(db, target.id):
        _missing()
    return target


def _access(db: Session, viewer: User | None, target: User) -> str:
    """How much of target's profile the viewer gets: owner, full, basic or blocked (a stub
    for the blocker, so they can unblock). Raises the not-found 404 otherwise."""
    if viewer is not None and viewer.id == target.id:
        return "owner"
    if viewer is not None and blocked_by(db, viewer.id, target.id):
        return "blocked"
    if viewer is not None and blocked_by(db, target.id, viewer.id):
        _missing()
    if target.profile_visibility == "private":
        _missing()
    if target.profile_visibility == "followers" and not (viewer is not None and follows(db, viewer.id, target.id)):
        return "basic"
    return "full"


def _counts(db: Session, user_id: int) -> dict:
    return {
        "followers": db.query(func.count(Follow.id))
        .filter(Follow.followee_id == user_id, Follow.status == "accepted").scalar(),
        "following": db.query(func.count(Follow.id))
        .filter(Follow.follower_id == user_id, Follow.status == "accepted").scalar(),
        "reviews": db.query(func.count(Review.id)).filter(Review.user_id == user_id).scalar(),
        "likes_received": db.query(func.count(ReviewLike.id)).join(Review, Review.id == ReviewLike.review_id)
        .filter(Review.user_id == user_id).scalar(),
        # Hours backed by receipts: the verified play time frozen on their game
        # reviews. Software is left out, the same as for the badge beside the name.
        "verified_minutes": db.query(func.coalesce(func.sum(Review.verified_playtime_minutes), 0))
        .join(Game, Game.id == Review.game_id)
        .filter(Review.user_id == user_id, Game.content_kind == "game").scalar(),
    }


def _profile(db: Session, viewer: User | None, target: User) -> dict:
    level = _access(db, viewer, target)
    signed_in = viewer is not None and level != "owner"
    body = {
        "id": target.id, "display_name": target.display_name, "handle": target.handle,
        "badge": target.badge if level != "blocked" else None,
        "limited": level in ("basic", "blocked"),
        "viewer": {
            "signed_in": viewer is not None, "is_self": level == "owner",
            "following": signed_in and follows(db, viewer.id, target.id),
            "requested": signed_in and level != "blocked" and follow_status(db, viewer.id, target.id) == "pending",
            "follows_you": signed_in and level != "blocked" and follows(db, target.id, viewer.id),
            "blocked": level == "blocked",
        },
    }
    if level == "blocked":
        return body
    body["profile_visibility"] = target.profile_visibility
    body["counts"] = _counts(db, target.id)
    if level in ("owner", "full"):
        body["avatar_url"] = target.avatar_url
        body["cover_url"] = target.cover_url
        body["room"] = {}
        # shortcut: 12 games per status shelf; add paging when full shelves are needed.
        for status in ("playing", "completed", "want_to_play"):
            shelf = (db.query(Game).join(GameStatus, GameStatus.game_id == Game.id)
                     .filter(GameStatus.user_id == target.id, GameStatus.status == status, Game.content_kind == "game")
                     .order_by(GameStatus.updated_at.desc(), Game.id).limit(12).all())
            body["room"][status] = [GameOut.model_validate(game) for game in shelf]
        rows = (_query(db, "").filter(Review.user_id == target.id)
                .order_by(Review.created_at.desc(), Review.id.desc()).limit(RECENT_REVIEWS).all())
        # Play statuses follow the same rule as the rest of the full profile.
        found = dict(db.query(GameStatus.status, func.count(GameStatus.id))
                     .filter(GameStatus.user_id == target.id).group_by(GameStatus.status).all())
        body["status_counts"] = {status: found.get(status, 0) for status in PLAY_STATUSES}
        pinned = (db.query(Favorite).join(Game, Game.id == Favorite.game_id)
                  .filter(Favorite.user_id == target.id).order_by(Favorite.position).all())
        body["favorites"] = [GameOut.model_validate(row.game) for row in pinned]
        # The header art: the game of their most verified review, else their crowned favorite.
        top = (db.query(Review).filter(Review.user_id == target.id, Review.verified_playtime_minutes.isnot(None))
               .order_by(Review.verified_playtime_minutes.desc(), Review.id).first())
        banner = top.game if top else (pinned[0].game if pinned else None)
        body["banner_game"] = GameOut.model_validate(banner) if banner else None
        body.update(bio=target.bio, joined=target.created_at,
                    recent_reviews=_items(db, rows, {row.id: "" for row in rows}, viewer.id if viewer else None))
    return body


@router.get("/users/by-handle/{handle}")
def profile_by_handle(handle: str, db: Session = Depends(get_db), viewer: User | None = Depends(get_optional_user)):
    """The same profile as /users/{id}, found by @handle. Same findability rules."""
    found = db.query(User.id).filter(User.handle == handles.normalize(handle[:40])).scalar()
    if found is None:
        _missing()
    return profile(found, db, viewer)


@router.get("/users/{user_id}")
def profile(user_id: ResourceId, db: Session = Depends(get_db), viewer: User | None = Depends(get_optional_user)):
    # Never cached: what it shows depends on who is asking.
    return _profile(db, viewer, _findable(db, viewer, user_id))


def _follow_list(db: Session, viewer: User, user_id: int, direction: str, limit: int, offset: int):
    target = _findable(db, viewer, user_id)
    if _access(db, viewer, target) not in ("owner", "full"):
        _missing()
    mine, other = ((Follow.followee_id, Follow.follower_id) if direction == "followers"
                   else (Follow.follower_id, Follow.followee_id))
    query = (db.query(User).join(Follow, User.id == other).filter(mine == target.id, Follow.status == "accepted")
             # Private players stay out of other people's lists; blocked ones disappear either way.
             .filter(or_(User.profile_visibility != "private", User.id == viewer.id), not_hidden(User.id, viewer.id)))
    total = query.count()
    rows = query.order_by(Follow.id.desc()).offset(offset).limit(limit).all()
    # Names only: when someone followed whom is nobody else's business.
    return {"items": [UserCard.model_validate(user).model_dump() for user in rows], "total": total}


@router.get("/users/{user_id}/followers")
def followers(user_id: ResourceId, limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0, le=MAX_OFFSET),
              db: Session = Depends(get_db), viewer: User = Depends(list_budget)):
    return _follow_list(db, viewer, user_id, "followers", limit, offset)


@router.get("/users/{user_id}/following")
def following(user_id: ResourceId, limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0, le=MAX_OFFSET),
              db: Session = Depends(get_db), viewer: User = Depends(list_budget)):
    return _follow_list(db, viewer, user_id, "following", limit, offset)


def _not_self(user: User, user_id: int, action: str):
    if user.id == user_id:
        raise HTTPException(status_code=422, detail=f"You can't {action} yourself.")


@router.post("/users/{user_id}/follow")
def follow(user_id: ResourceId, db: Session = Depends(get_db),
           user: User = Depends(follow_budget)):
    _not_self(user, user_id, "follow")
    target = _findable(db, user, user_id)
    # A block either way, or a private profile, looks exactly like a missing user.
    if target.profile_visibility == "private" or blocked_either_way(db, user.id, target.id):
        _missing()
    if follow_status(db, user.id, target.id) is None:
        if db.query(func.count(Follow.id)).filter(Follow.follower_id == user.id).scalar() >= MAX_FOLLOWING:
            raise HTTPException(status_code=409, detail=f"You can follow up to {MAX_FOLLOWING} players.")
        # A followers-only profile decides who gets in; a public one has nothing to protect.
        status = "pending" if target.profile_visibility == "followers" else "accepted"
        db.add(Follow(follower_id=user.id, followee_id=target.id, status=status))
        try:
            db.flush()
            if status == "accepted":
                notifications.notify(db, target.id, user.id, "follow")
            db.commit()
        except IntegrityError:
            db.rollback()  # a second tab got there first; the result is the same
        # A block that landed between the check and the insert wins.
        if blocked_either_way(db, user.id, target.id):
            db.query(Follow).filter_by(follower_id=user.id, followee_id=target.id).delete(synchronize_session=False)
            notifications.remove_between(db, user.id, target.id)
            db.commit()
            _missing()
    status = follow_status(db, user.id, target.id)
    return {"following": status == "accepted", "requested": status == "pending", "counts": _counts(db, target.id)}


@router.delete("/users/{user_id}/follow", status_code=204)
def unfollow(user_id: ResourceId, db: Session = Depends(get_db),
             user: User = Depends(follow_budget)):
    # Also cancels a pending request. Always 204, so it cannot be used to probe for users.
    db.query(Follow).filter_by(follower_id=user.id, followee_id=user_id).delete(synchronize_session=False)
    db.commit()
    return Response(status_code=204)


@router.get("/me/follow-requests")
def follow_requests(limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0, le=MAX_OFFSET),
                    db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    query = (db.query(Follow, User).join(User, User.id == Follow.follower_id)
             .filter(Follow.followee_id == user.id, Follow.status == "pending", not_hidden(User.id, user.id)))
    rows = query.order_by(Follow.id.desc()).offset(offset).limit(limit).all()
    return {"items": [{"id": row.id, "user": UserCard.model_validate(person).model_dump()} for row, person in rows],
            "total": query.count()}


def _pending_request(db: Session, user: User, request_id: int) -> Follow:
    row = db.query(Follow).filter_by(id=request_id, followee_id=user.id, status="pending").first()
    if row is None:
        raise HTTPException(status_code=404, detail="Request not found")
    return row


@router.post("/me/follow-requests/{request_id}/accept")
def accept_request(request_id: ResourceId, db: Session = Depends(get_db), user: User = Depends(follow_budget)):
    row = _pending_request(db, user, request_id)
    row.status = "accepted"
    notifications.notify(db, row.follower_id, user.id, "follow_accepted")
    notifications.notify(db, user.id, row.follower_id, "follow")
    db.commit()
    return {"accepted": True, "counts": _counts(db, user.id)}


@router.post("/me/follow-requests/{request_id}/decline", status_code=204)
def decline_request(request_id: ResourceId, db: Session = Depends(get_db), user: User = Depends(follow_budget)):
    # The requester is not told; to them the request just stays unanswered.
    db.delete(_pending_request(db, user, request_id))
    db.commit()
    return Response(status_code=204)


@router.post("/users/{user_id}/block", status_code=204)
def block(user_id: ResourceId, db: Session = Depends(get_db),
          user: User = Depends(block_budget)):
    _not_self(user, user_id, "block")
    target = _findable(db, user, user_id)
    if not blocked_by(db, user.id, target.id):
        if db.query(func.count(Block.id)).filter(Block.blocker_id == user.id).scalar() >= MAX_BLOCKS:
            raise HTTPException(status_code=409, detail=f"You can block up to {MAX_BLOCKS} players.")
        db.add(Block(blocker_id=user.id, blocked_id=target.id))
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
    db.query(Follow).filter(or_(and_(Follow.follower_id == user.id, Follow.followee_id == target.id),
                                and_(Follow.follower_id == target.id, Follow.followee_id == user.id))
                            ).delete(synchronize_session=False)
    notifications.remove_between(db, user.id, target.id)
    db.commit()
    return Response(status_code=204)


@router.delete("/users/{user_id}/block", status_code=204)
def unblock(user_id: ResourceId, db: Session = Depends(get_db),
            user: User = Depends(block_budget)):
    db.query(Block).filter_by(blocker_id=user.id, blocked_id=user_id).delete(synchronize_session=False)
    db.commit()
    return Response(status_code=204)


@router.get("/me/blocks")
def my_blocks(limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0, le=MAX_OFFSET),
              db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    query = db.query(User, Block.created_at).join(Block, Block.blocked_id == User.id).filter(Block.blocker_id == user.id)
    rows = query.order_by(Block.id.desc()).offset(offset).limit(limit).all()
    return {"items": [{**UserCard.model_validate(row).model_dump(), "since": since} for row, since in rows],
            "total": query.count()}


@router.post("/reports", status_code=201)
def report(payload: ReportCreate, db: Session = Depends(get_db),
           user: User = Depends(report_budget)):
    # Reporting works whatever the block state, so it never reveals one.
    if payload.target_type == "user":
        _not_self(user, payload.target_id, "report")
        _findable(db, user, payload.target_id)
    else:
        model = {"review": Review, "comment": Comment, "repost": Repost}[payload.target_type]
        author = db.query(model.user_id).filter(model.id == payload.target_id).scalar()
        if author is None or (payload.target_type == "repost" and blocked_either_way(db, user.id, author)):
            # A quote from someone you blocked looks the same as a missing one.
            raise HTTPException(status_code=404, detail="Not found")
        if author == user.id:
            raise HTTPException(status_code=422, detail="You can't report your own post.")
    db.add(Report(reporter_id=user.id, **payload.model_dump()))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="You already reported this. It's waiting for review.") from None
    return {"status": "open"}


@router.patch("/me/profile")
def edit_profile(changes: ProfileUpdate, db: Session = Depends(get_db),
                 user: User = Depends(profile_budget)):
    if "profile_visibility" in changes.model_fields_set and changes.profile_visibility is None:
        raise HTTPException(status_code=422, detail="Choose who can see your profile.")
    if "handle" in changes.model_fields_set and changes.handle is None:
        raise HTTPException(status_code=422, detail="Choose a handle.")
    if changes.handle is not None and db.query(User.id).filter(User.handle == changes.handle, User.id != user.id).first():
        raise HTTPException(status_code=409, detail="That handle is taken. Try another.")
    for field in changes.model_fields_set:
        setattr(user, field, getattr(changes, field))
    if user.profile_visibility == "public":
        # Nothing left to approve: waiting requests go through.
        for pending in db.query(Follow).filter_by(followee_id=user.id, status="pending").all():
            notifications.notify(db, pending.follower_id, user.id, "follow_accepted")
            notifications.notify(db, user.id, pending.follower_id, "follow")
        db.query(Follow).filter_by(followee_id=user.id, status="pending").update(
            {"status": "accepted"}, synchronize_session=False)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="That handle is taken. Try another.") from None
    db.refresh(user)
    return _profile(db, user, user)


def _followed_reviews(db: Session, user: User, q: str, anchor: int):
    return _query(db, q).filter(
        Review.id <= anchor,
        Review.user_id.in_(select(Follow.followee_id)
                           .where(Follow.follower_id == user.id, Follow.status == "accepted")),
        not_hidden(Review.user_id, user.id),
    )


def _followed_reposts(db: Session, user: User, q: str, anchor: int, repost_anchor: int):
    """Reposts from people you follow, of reviews you are still allowed to see.

    A private profile's reposts drop out even if you used to follow them.
    A followers-only profile's reposts stay, because an accepted follow is
    what this feed is. Blocks hide the reposter and the review's author.
    """
    query = (db.query(Repost)
             .join(Review, Review.id == Repost.review_id)
             .join(Game, Game.id == Review.game_id)
             .join(User, User.id == Repost.user_id)
             .options(joinedload(Repost.user),
                      joinedload(Repost.review).joinedload(Review.user),
                      joinedload(Repost.review).joinedload(Review.game))
             .filter(Repost.id <= repost_anchor, Review.id <= anchor, Game.content_kind == "game",
                     Repost.user_id.in_(select(Follow.followee_id)
                                        .where(Follow.follower_id == user.id, Follow.status == "accepted")),
                     not_hidden(Repost.user_id, user.id), not_hidden(Review.user_id, user.id),
                     reposter_reachable(user.id)))
    if q := q.strip():
        query = query.filter(Game.name.icontains(q, autoescape=True))
    return query.order_by(Repost.created_at.desc(), Repost.id.desc()).limit(500).all()


@router.get("/me/following-feed")
def following_feed(limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0, le=MAX_OFFSET),
                   q: str = Query("", max_length=120), before: int | None = Query(None, ge=0, le=MAX_RESOURCE_ID),
                   repost_before: int | None = Query(None, ge=0, le=MAX_RESOURCE_ID),
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    anchor = _anchor(db, before)
    repost_anchor = repost_before if repost_before is not None else (db.query(func.max(Repost.id)).scalar() or 0)
    reposts = _followed_reposts(db, user, q, anchor, repost_anchor)
    query = _followed_reviews(db, user, q, anchor)
    # No reposts: the same pages as before, counted in SQL so a long list still pages.
    if not reposts:
        total = query.count()
        rows = query.order_by(Review.created_at.desc(), Review.id.desc()).offset(offset).limit(limit).all()
        return {"items": _items(db, rows, {row.id: "From someone you follow" for row in rows}, user.id),
                "total": total, "before": anchor, "repost_before": repost_anchor, "mode": "following"}
    reviews = query.order_by(Review.created_at.desc(), Review.id.desc()).limit(500).all()
    events = [("review", _utc(row.created_at), row.id, row, None) for row in reviews]
    events.extend(("repost", _utc(row.created_at), row.id, row.review, row) for row in reposts)
    events.sort(key=lambda event: (event[1], event[2]), reverse=True)
    page = events[offset:offset + limit]
    reasons = ["From someone you follow" if repost is None else (
        f"{repost.user.display_name} quoted" if repost.quote else f"{repost.user.display_name} reposted")
        for _kind, _when, _tie, _review, repost in page]
    items = _items(db, [review for _kind, _when, _tie, review, _repost in page], {}, user.id, reasons)
    for item, (_kind, _when, _tie, _review, repost) in zip(items, page):
        if repost is not None:
            item["repost"] = repost_card(repost)
    return {"items": items, "total": len(events), "before": anchor, "repost_before": repost_anchor,
            "mode": "following"}


def _owner(user: User):
    # Not an owner reads exactly like a route that does not exist.
    if settings.demo_mode or user.id not in settings.owners:
        raise HTTPException(status_code=404, detail="Not Found")


def _excerpt(text: str | None) -> str | None:
    return None if text is None else text[:280]


def _report_out(db: Session, row: Report) -> dict:
    target = None
    if row.target_type == "review":
        review = db.get(Review, row.target_id)
        if review is not None:
            target = {"author": UserCard.model_validate(review.user).model_dump(), "game_id": review.game_id,
                      "rating": review.rating, "body": _excerpt(review.body)}
    elif row.target_type == "comment":
        comment = db.get(Comment, row.target_id)
        if comment is not None:
            target = {"author": UserCard.model_validate(comment.user).model_dump(), "review_id": comment.review_id,
                      "body": _excerpt(comment.body)}
    elif row.target_type == "repost":
        repost = db.get(Repost, row.target_id)
        if repost is not None:
            target = {"author": UserCard.model_validate(repost.user).model_dump(), "review_id": repost.review_id,
                      "body": _excerpt(repost.quote)}
    else:
        person = db.get(User, row.target_id)
        if person is not None:
            target = {"author": UserCard.model_validate(person).model_dump(), "body": _excerpt(person.bio)}
    reporter = db.get(User, row.reporter_id)
    return {"id": row.id, "target_type": row.target_type, "target_id": row.target_id, "reason": row.reason,
            "note": row.note, "status": row.status, "created_at": row.created_at,
            "reporter": UserCard.model_validate(reporter).model_dump(), "target": target}


@router.get("/admin/reports")
def report_queue(status: str = Query("open", pattern="^(open|dismissed|actioned)$"),
                 limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0, le=MAX_OFFSET),
                 db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _owner(user)
    logger.info("moderation action=view_queue moderator=%s status=%s", user.id, status)
    query = db.query(Report).filter(Report.status == status)
    rows = query.order_by(Report.id.desc()).offset(offset).limit(limit).all()
    return {"items": [_report_out(db, row) for row in rows], "total": query.count()}


@router.post("/admin/reports/{report_id}")
def resolve_report(report_id: ResourceId, change: ReportResolve, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    _owner(user)
    # A moderation decision needs a recent sign-in, not just a session that is still alive.
    if time.time() - request.state.session_issued > MODERATOR_FRESHNESS:
        logger.warning("moderation action=refused_stale_session moderator=%s report=%s", user.id, report_id)
        raise HTTPException(status_code=403, detail="Sign in again to moderate reports.")
    row = db.get(Report, report_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Report not found")
    # Closing a report records the decision; removing content is a separate step.
    row.status = change.status
    db.commit()
    # IDs and the decision only, never the reported text.
    logger.info("moderation action=%s moderator=%s report=%s", change.status, user.id, row.id)
    return _report_out(db, row)


# ---------------------------------------------------------------- likes and favorites

def _likeable(db: Session, viewer: User, review_id: int) -> Review:
    """A review this viewer may like: it exists and no block stands between them."""
    review = db.get(Review, review_id)
    if review is None or blocked_either_way(db, viewer.id, review.user_id):
        raise HTTPException(status_code=404, detail="Review not found")
    return review


def _like_state(db: Session, viewer: User, review_id: int) -> dict:
    liked = db.query(ReviewLike.id).filter_by(user_id=viewer.id, review_id=review_id).first() is not None
    count = db.query(func.count(ReviewLike.id)).filter(ReviewLike.review_id == review_id).scalar()
    return {"review_id": review_id, "liked": liked, "like_count": count}


@router.post("/reviews/{review_id}/like")
def like(review_id: ResourceId, db: Session = Depends(get_db), user: User = Depends(like_budget)):
    """Liking twice is the same as liking once."""
    review = _likeable(db, user, review_id)
    if db.query(ReviewLike.id).filter_by(user_id=user.id, review_id=review_id).first() is None:
        db.add(ReviewLike(user_id=user.id, review_id=review_id))
        try:
            db.flush()
            notifications.notify(db, review.user_id, user.id, "like", review_id)
            db.commit()
        except IntegrityError:
            db.rollback()  # A second tab liked it at the same moment; one like stands.
    return _like_state(db, user, review_id)


@router.delete("/reviews/{review_id}/like")
def unlike(review_id: ResourceId, db: Session = Depends(get_db), user: User = Depends(like_budget)):
    db.query(ReviewLike).filter_by(user_id=user.id, review_id=review_id).delete(synchronize_session=False)
    db.query(Notification).filter_by(actor_id=user.id, review_id=review_id, kind="like", read_at=None).delete(
        synchronize_session=False)
    db.commit()
    return _like_state(db, user, review_id)


@router.get("/me/likes")
def my_likes(review_ids: str = Query("", max_length=1200), db: Session = Depends(get_db),
             user: User = Depends(get_current_user)):
    """Which of these reviews the signed-in player liked. Feeds and review lists
    are cached for everyone, so the hearts are filled in from here."""
    try:
        ids = sorted({int(part) for part in review_ids.split(",") if part.strip()})
    except ValueError:
        raise HTTPException(status_code=422, detail="review_ids must be a list of numbers") from None
    if len(ids) > MAX_LIKE_LOOKUP or any(not 1 <= value <= MAX_RESOURCE_ID for value in ids):
        raise HTTPException(status_code=422, detail=f"Ask about at most {MAX_LIKE_LOOKUP} reviews at once")
    liked = [] if not ids else [row[0] for row in db.query(ReviewLike.review_id)
                                .filter(ReviewLike.user_id == user.id, ReviewLike.review_id.in_(ids)).all()]
    return {"liked": sorted(liked)}


@router.put("/me/favorites")
def set_favorites(change: FavoritesUpdate, db: Session = Depends(get_db), user: User = Depends(profile_budget)):
    """Replace the pinned games in one go, in the order given. Slot 1 is the crown."""
    ids = change.game_ids
    if ids and db.query(func.count(Game.id)).filter(Game.id.in_(ids)).scalar() != len(ids):
        raise HTTPException(status_code=404, detail="Game not found")
    db.query(Favorite).filter_by(user_id=user.id).delete(synchronize_session=False)
    db.flush()
    db.add_all([Favorite(user_id=user.id, game_id=game_id, position=index)
                for index, game_id in enumerate(ids[:MAX_FAVORITES], start=1)])
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Your favorites changed in another tab. Try again.") from None
    pinned = db.query(Favorite).filter_by(user_id=user.id).order_by(Favorite.position).all()
    return {"favorites": [GameOut.model_validate(row.game) for row in pinned]}


# ---------------------------------------------------------------- reposts and bookmarks

def _shareable(db: Session, viewer: User, review_id: int) -> Review:
    """A review this viewer may repost or save. Blocks hide it both ways.
    Your own review can be saved, but not reposted (the caller checks that)."""
    review = db.get(Review, review_id)
    if review is None or blocked_either_way(db, viewer.id, review.user_id):
        raise HTTPException(status_code=404, detail="Review not found")
    return review


def _ids_param(review_ids: str) -> list[int]:
    try:
        ids = sorted({int(part) for part in review_ids.split(",") if part.strip()})
    except ValueError:
        raise HTTPException(status_code=422, detail="review_ids must be a list of numbers") from None
    if len(ids) > MAX_LIKE_LOOKUP or any(not 1 <= value <= MAX_RESOURCE_ID for value in ids):
        raise HTTPException(status_code=422, detail=f"Ask about at most {MAX_LIKE_LOOKUP} reviews at once")
    return ids


def _repost_state(db: Session, viewer: User, review: Review, quote_id: int | None = None) -> dict:
    plain = db.query(Repost.id).filter_by(user_id=viewer.id, review_id=review.id, quote=None).first() is not None
    count = db.query(func.count(Repost.id)).filter(Repost.review_id == review.id).scalar()
    body = {"review_id": review.id, "reposted": plain, "repost_count": count}
    if quote_id is not None:
        body["quote_id"] = quote_id
    return body


@router.post("/reviews/{review_id}/repost")
def repost(review_id: ResourceId, payload: RepostCreate | None = None, db: Session = Depends(get_db),
           user: User = Depends(repost_budget)):
    """A blank body is a plain repost (one per player). Text is a new quote."""
    review = _shareable(db, user, review_id)
    if review.user_id == user.id:
        raise HTTPException(status_code=422, detail="You can't repost your own review.")
    quote = None if payload is None else payload.quote
    quote_id = None
    if quote:
        if len(quote) > QUOTE_MAX:
            raise HTTPException(status_code=422, detail=f"Keep a quote to {QUOTE_MAX} characters.")
        row = Repost(user_id=user.id, review_id=review.id, quote=quote)
        db.add(row)
        notifications.notify(db, review.user_id, user.id, "quote", review.id)
        db.commit()
        quote_id = row.id
    elif db.query(Repost.id).filter_by(user_id=user.id, review_id=review.id, quote=None).first() is None:
        db.add(Repost(user_id=user.id, review_id=review.id, quote=None))
        try:
            db.flush()
            notifications.notify(db, review.user_id, user.id, "repost", review.id)
            db.commit()
        except IntegrityError:
            db.rollback()
    return _repost_state(db, user, review, quote_id)


@router.delete("/reviews/{review_id}/repost")
def unrepost(review_id: ResourceId, db: Session = Depends(get_db), user: User = Depends(repost_budget)):
    """Removes the plain repost. Quotes stay until you delete each one."""
    db.query(Repost).filter_by(user_id=user.id, review_id=review_id, quote=None).delete(synchronize_session=False)
    db.query(Notification).filter_by(actor_id=user.id, review_id=review_id, kind="repost", read_at=None).delete(
        synchronize_session=False)
    db.commit()
    review = db.get(Review, review_id)
    if review is None:
        return {"review_id": review_id, "reposted": False, "repost_count": 0}
    return _repost_state(db, user, review)


@router.delete("/reposts/{repost_id}")
def delete_quote(repost_id: ResourceId, db: Session = Depends(get_db), user: User = Depends(repost_budget)):
    """Remove one of your own quotes. Someone else's quote looks missing."""
    row = db.get(Repost, repost_id)
    if row is None or row.user_id != user.id or row.quote is None:
        raise HTTPException(status_code=404, detail="Repost not found")
    review_id = row.review_id
    db.delete(row)
    db.commit()
    review = db.get(Review, review_id)
    return _repost_state(db, user, review) if review is not None else {
        "review_id": review_id, "reposted": False, "repost_count": 0}


@router.get("/me/reposts")
def my_reposts(review_ids: str = Query("", max_length=1200), db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    """Which of these reviews the signed-in player has plain-reposted."""
    ids = _ids_param(review_ids)
    rows = [] if not ids else [row[0] for row in db.query(Repost.review_id).filter(
        Repost.user_id == user.id, Repost.quote.is_(None), Repost.review_id.in_(ids)).all()]
    return {"reposted": sorted(rows)}


def _bookmark_state(db: Session, viewer: User, review_id: int) -> dict:
    saved = db.query(Bookmark.id).filter_by(user_id=viewer.id, review_id=review_id).first() is not None
    return {"review_id": review_id, "bookmarked": saved}


@router.post("/reviews/{review_id}/bookmark")
def bookmark(review_id: ResourceId, db: Session = Depends(get_db), user: User = Depends(bookmark_budget)):
    """Saving twice is the same as saving once. Nothing here is public."""
    _shareable(db, user, review_id)
    if db.query(Bookmark.id).filter_by(user_id=user.id, review_id=review_id).first() is None:
        db.add(Bookmark(user_id=user.id, review_id=review_id))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
    return _bookmark_state(db, user, review_id)


@router.delete("/reviews/{review_id}/bookmark")
def unbookmark(review_id: ResourceId, db: Session = Depends(get_db), user: User = Depends(bookmark_budget)):
    db.query(Bookmark).filter_by(user_id=user.id, review_id=review_id).delete(synchronize_session=False)
    db.commit()
    return _bookmark_state(db, user, review_id)


@router.get("/me/bookmarks")
def my_bookmarks(review_ids: str | None = Query(None, max_length=1200),
                 limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0, le=MAX_OFFSET),
                 db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """With review_ids, which of those this player saved. Without, their list.

    Only this player is ever asked: the route is /me, and the rows are filtered
    to them. A block hides a saved review the same way it hides it everywhere else.
    """
    if review_ids is not None:
        ids = _ids_param(review_ids)
        rows = [] if not ids else [row[0] for row in db.query(Bookmark.review_id).filter(
            Bookmark.user_id == user.id, Bookmark.review_id.in_(ids)).all()]
        return {"bookmarked": sorted(rows)}
    query = (db.query(Review).join(Bookmark, Bookmark.review_id == Review.id)
             .join(Review.game).filter(Bookmark.user_id == user.id, not_hidden(Review.user_id, user.id))
             .options(joinedload(Review.user), contains_eager(Review.game)))
    total = query.count()
    rows = query.order_by(Bookmark.id.desc()).offset(offset).limit(limit).all()
    return {"items": _items(db, rows, {row.id: "Saved by you" for row in rows}, user.id), "total": total}
