"""What is trending this week, and who a player might follow.

The window is 7 days. A new review, like, comment or repost adds its weight to
the game and the review. Weight halves every 48 hours, so a post from this
morning outranks the same post from six days ago. Hours in the trend line are
only the verified time on reviews written this week, not likes of older ones.

The worker refreshes the public blob every few minutes. A request with a cold
cache computes the same blob, so Explore still works when the worker is down.
Who to follow is personal (your library, your follows, your blocks), so it is
never part of that shared blob.
"""

import json
from collections import Counter
from datetime import timedelta

from fastapi.encoders import jsonable_encoder
from redis.exceptions import RedisError
from sqlalchemy.orm import Session, joinedload

from app.models import Comment, Follow, Game, PlaytimeSnapshot, Repost, Review, ReviewLike, User, utcnow
from app.routers.feed import _items, _utc
from app.schemas import GameOut
from app.visibility import blocked_either_way

WINDOW = timedelta(days=7)
HALF_LIFE_HOURS = 48
# One Explore read must stay a few indexed scans, not a walk of every row ever.
EVENT_CAP = 2000
TREND_LIMIT = 8
SUGGEST_LIMIT = 8
TRENDING_KEY = "playgraph:trending"
TRENDING_TTL = 15 * 60


def event_weight(when, now) -> float:
    """1.0 at the moment it happens, half every 48 hours, 0 after the window."""
    hours = (_utc(now) - _utc(when)).total_seconds() / 3600
    if hours < 0:
        hours = 0
    if hours > WINDOW.total_seconds() / 3600:
        return 0.0
    return 0.5 ** (hours / HALF_LIFE_HOURS)


def _apply(scores, review_scores, game_id, review_id, weight):
    if weight <= 0:
        return
    scores[game_id] += weight
    review_scores[review_id] += weight


def compute_trending(db: Session, now=None) -> dict:
    """Public games and reviews, highest score first. No viewer, no blocks."""
    now = now or utcnow()
    scores, review_scores, minutes = Counter(), Counter(), Counter()
    reviews = (db.query(Review.id, Review.game_id, Review.created_at, Review.verified_playtime_minutes)
               .join(Game, Game.id == Review.game_id)
               .filter(Game.content_kind == "game")
               .order_by(Review.created_at.desc(), Review.id.desc()).limit(EVENT_CAP).all())
    for row in reviews:
        weight = event_weight(row.created_at, now)
        _apply(scores, review_scores, row.game_id, row.id, weight)
        if weight > 0 and row.verified_playtime_minutes:
            minutes[row.game_id] += row.verified_playtime_minutes
    likes = (db.query(ReviewLike.created_at, Review.id, Review.game_id)
             .join(Review, Review.id == ReviewLike.review_id)
             .join(Game, Game.id == Review.game_id)
             .filter(Game.content_kind == "game")
             .order_by(ReviewLike.created_at.desc(), ReviewLike.id.desc()).limit(EVENT_CAP).all())
    for created_at, review_id, game_id in likes:
        _apply(scores, review_scores, game_id, review_id, event_weight(created_at, now))
    comments = (db.query(Comment.created_at, Review.id, Review.game_id)
                .join(Review, Review.id == Comment.review_id)
                .join(Game, Game.id == Review.game_id)
                .filter(Game.content_kind == "game")
                .order_by(Comment.created_at.desc(), Comment.id.desc()).limit(EVENT_CAP).all())
    for created_at, review_id, game_id in comments:
        _apply(scores, review_scores, game_id, review_id, event_weight(created_at, now))
    reposts = (db.query(Repost.created_at, Review.id, Review.game_id)
               .join(Review, Review.id == Repost.review_id)
               .join(Game, Game.id == Review.game_id)
               .filter(Game.content_kind == "game")
               .order_by(Repost.created_at.desc(), Repost.id.desc()).limit(EVENT_CAP).all())
    for created_at, review_id, game_id in reposts:
        _apply(scores, review_scores, game_id, review_id, event_weight(created_at, now))

    game_ids = sorted(scores, key=lambda gid: (-scores[gid], -minutes[gid], gid))[:TREND_LIMIT]
    review_ids = sorted(review_scores, key=lambda rid: (-review_scores[rid], -rid))[:TREND_LIMIT]
    games = {game.id: game for game in db.query(Game).filter(Game.id.in_(game_ids)).all()} if game_ids else {}
    rows = (db.query(Review).options(joinedload(Review.user), joinedload(Review.game))
            .filter(Review.id.in_(review_ids)).all()) if review_ids else []
    by_id = {row.id: row for row in rows}
    ordered = [by_id[rid] for rid in review_ids if rid in by_id]
    items = _items(db, ordered, {row.id: "Trending this week" for row in ordered})
    return {
        "games": [{
            "game": GameOut.model_validate(games[gid]).model_dump(mode="json"),
            "genre": ((games[gid].genres or "").split(",")[0].strip() if gid in games else ""),
            "hours": minutes[gid] // 60,
            "score": round(scores[gid], 4),
        } for gid in game_ids if gid in games],
        "reviews": items,
    }


def suggest_accounts(db: Session, viewer: User | None) -> list[dict]:
    """Public players who reviewed a game this week, best shared library first.

    Already followed (including a pending request), blocked either way, private,
    and the viewer are left out. A followers-only player can still be suggested.
    """
    now = utcnow()
    recent = (db.query(Review.user_id, Review.created_at)
              .join(User, User.id == Review.user_id)
              .join(Game, Game.id == Review.game_id)
              .filter(Game.content_kind == "game", User.profile_visibility != "private")
              .order_by(Review.created_at.desc(), Review.id.desc()).limit(EVENT_CAP).all())
    active = {user_id for user_id, created_at in recent if event_weight(created_at, now) > 0}
    if viewer is not None:
        active.discard(viewer.id)
        followed = {row.followee_id for row in db.query(Follow.followee_id).filter(Follow.follower_id == viewer.id)}
        active -= followed
        active = {user_id for user_id in active if not blocked_either_way(db, viewer.id, user_id)}
    if not active:
        return []
    owned = set()
    if viewer is not None:
        owned = {game_id for (game_id,) in db.query(PlaytimeSnapshot.game_id)
                 .filter(PlaytimeSnapshot.user_id == viewer.id).distinct()}
    played = {}
    for user_id, game_id in db.query(Review.user_id, Review.game_id).filter(Review.user_id.in_(active)).distinct():
        played.setdefault(user_id, set()).add(game_id)
    # One row per like, so the count is how many rows name that player.
    like_counts = Counter(user_id for user_id, _like in db.query(Review.user_id, ReviewLike.id)
                          .join(ReviewLike, ReviewLike.review_id == Review.id)
                          .filter(Review.user_id.in_(active)))
    people = db.query(User).filter(User.id.in_(active)).all()
    ranked = sorted(people, key=lambda person: (
        -len(played.get(person.id, ()) & owned),
        -like_counts[person.id],
        person.display_name.lower(),
        person.id,
    ))
    return [{
        "id": person.id,
        "display_name": person.display_name,
        "handle": person.handle,
        "badge": person.badge,
        "avatar_url": person.avatar_url,
        "shared_games": len(played.get(person.id, ()) & owned),
        "likes_received": like_counts[person.id],
        "profile_visibility": person.profile_visibility,
    } for person in ranked[:SUGGEST_LIMIT]]


async def load_trending(store, db: Session) -> dict:
    """The shared blob. Redis when it is fresh, otherwise compute and store it."""
    if store is not None:
        try:
            raw = await store.get(TRENDING_KEY)
            if raw:
                return json.loads(raw)
        except (RedisError, OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    payload = jsonable_encoder(compute_trending(db))
    if store is not None:
        try:
            await store.set(TRENDING_KEY, json.dumps(payload, separators=(",", ":")), ex=TRENDING_TTL)
        except (RedisError, OSError):
            pass
    return payload
