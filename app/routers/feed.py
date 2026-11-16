"""Public reviews and an explainable, bounded personal review feed."""
from collections import Counter
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, contains_eager, joinedload

from app.database import get_db
from app.deps import get_current_user, get_optional_user
from app.models import Comment, Game, PlaytimeSnapshot, Repost, Review, User
from app.routers.library import _latest_snapshots
from app.routers.parameters import MAX_OFFSET, MAX_RESOURCE_ID, ResourceId
from app.schemas import GameOut, ReviewOut
from app.visibility import for_viewer, not_hidden, reposter_reachable

router = APIRouter(tags=["feed"])
CANDIDATE_LIMIT = 500


def _utc(moment: datetime) -> datetime:
    # SQLite hands times back without a zone, stored as UTC. Postgres hands them
    # back in the session's zone, where daylight saving can give rows different
    # offsets, so those are converted rather than relabeled.
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)


def _query(db: Session, q: str):
    # The join that filters on the game also fills Review.game; joinedload
    # would join games a second time just to load the same row.
    query = db.query(Review).join(Review.game).filter(Game.content_kind == "game")
    if q := q.strip():
        query = query.filter(Game.name.icontains(q, autoescape=True))
    return query.options(joinedload(Review.user), contains_eager(Review.game))


def _anchor(db: Session, before: int | None) -> int:
    """Pin pagination to the newest review now, so new posts cannot shift later pages."""
    return before if before is not None else (db.query(func.max(Review.id)).scalar() or 0)


def _items(db: Session, rows, reasons, viewer_id: int | None = None, reason_list=None):
    ids = [row.id for row in rows]
    counts = {}
    if ids:
        # Comments from someone the viewer blocked (either way) are not counted,
        # since the viewer will not see them in the thread either.
        query = db.query(Comment.review_id, func.count(Comment.id)).filter(Comment.review_id.in_(ids))
        if viewer_id is not None:
            query = query.filter(not_hidden(Comment.user_id, viewer_id))
        counts = dict(query.group_by(Comment.review_id).all())
    # reason_list is one reason per row. The dict is keyed by review id, which
    # collides when the same review appears as itself and as a quote of it.
    return [{"review": ReviewOut.model_validate(row), "game": GameOut.model_validate(row.game),
             "comment_count": counts.get(row.id, 0),
             "reason": reason_list[index] if reason_list is not None else reasons[row.id]}
            for index, row in enumerate(rows)]


def repost_card(repost: Repost) -> dict:
    """The repost wrapped around a review in a feed item. None means the review stands alone."""
    return {
        "id": repost.id,
        "user_id": repost.user_id,
        "author_name": repost.user.display_name,
        "author_handle": repost.user.handle,
        "author_badge": repost.user.badge,
        "author_avatar_url": repost.user.avatar_url,
        "quote": repost.quote,
        "created_at": repost.created_at,
    }


def stamp_reposts(items: list, reposts: list[Repost | None]) -> list:
    """Pair feed items with the repost that carried each one, when there is one."""
    for item, repost in zip(items, reposts):
        if repost is not None:
            item["repost"] = repost_card(repost)
    return items


def _popular_reposts(db: Session, user: User, anchor: int, q: str) -> list[Repost]:
    """Quotes, and plain reposts of a review at least two people shared.

    A single plain repost is not 'popular'; it still shows on Following.
    One card per quote, and one plain card per review (the newest).
    """
    query = (db.query(Repost)
             .join(Review, Review.id == Repost.review_id)
             .join(Game, Game.id == Review.game_id)
             .join(User, User.id == Repost.user_id)
             .options(joinedload(Repost.user),
                      joinedload(Repost.review).joinedload(Review.user),
                      joinedload(Repost.review).joinedload(Review.game))
             .filter(Review.id <= anchor, Game.content_kind == "game",
                     Repost.user_id != user.id, Review.user_id != user.id,
                     not_hidden(Repost.user_id, user.id), not_hidden(Review.user_id, user.id),
                     reposter_reachable(user.id),
                     or_(Repost.quote.isnot(None), Review.repost_count >= 2)))
    if q := q.strip():
        query = query.filter(Game.name.icontains(q, autoescape=True))
    rows = query.order_by(Repost.created_at.desc(), Repost.id.desc()).limit(CANDIDATE_LIMIT).all()
    seen, picked = set(), []
    for repost in rows:
        key = ("quote", repost.id) if repost.quote else ("plain", repost.review_id)
        if key in seen:
            continue
        seen.add(key)
        picked.append(repost)
    return picked


@router.get("/feed")
async def recent_reviews(request: Request, limit: int = Query(20, ge=1, le=50),
                         offset: int = Query(0, ge=0, le=MAX_OFFSET), q: str = Query("", max_length=120),
                         before: int | None = Query(None, ge=0, le=MAX_RESOURCE_ID), db: Session = Depends(get_db),
                         viewer: User | None = Depends(get_optional_user)):
    def load(viewer_id):
        anchor = _anchor(db, before)
        query = _query(db, q).filter(Review.id <= anchor)
        if viewer_id is not None:
            query = query.filter(not_hidden(Review.user_id, viewer_id))
        total = query.count()
        rows = query.order_by(Review.created_at.desc(), Review.id.desc()).offset(offset).limit(limit).all()
        return {"items": _items(db, rows, {row.id: "New in the community" for row in rows}, viewer_id),
                "total": total, "before": anchor, "mode": "latest"}
    return await for_viewer(request, db, viewer, ("feed", limit, offset, q.strip(), before), load)


@router.get("/me/feed")
def for_you(limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0, le=CANDIDATE_LIMIT),
            q: str = Query("", max_length=120), before: int | None = Query(None, ge=0, le=MAX_RESOURCE_ID),
            library_before: int | None = Query(None, ge=0, le=MAX_RESOURCE_ID),
            user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    anchor = _anchor(db, before)
    library_anchor = library_before if library_before is not None else (
        db.query(func.max(PlaytimeSnapshot.id)).filter(PlaytimeSnapshot.user_id == user.id).scalar() or 0
    )
    candidates = (_query(db, q).filter(Review.id <= anchor, Review.user_id != user.id,
                                       not_hidden(Review.user_id, user.id))
                  .order_by(Review.created_at.desc(), Review.id.desc()).limit(CANDIDATE_LIMIT).all())
    owned, preferences = set(), Counter()
    # Pin the append-only library snapshots too, so a sync cannot reorder later pages.
    for snapshot, game in _latest_snapshots(db, user.id, before_id=library_anchor):
        if game.content_kind != "game":
            continue
        owned.add(game.id)
        for genre in (game.genres or "").split(","):
            # NULL is unknown PlayStation play time; it adds no preference weight.
            if genre.strip() and (snapshot.playtime_minutes or 0) > 0:
                preferences[genre.strip()] += snapshot.playtime_minutes
    favorite_genres = {genre for genre, _ in preferences.most_common(5)}
    reasons, scores = {}, {}
    # Score against a fixed anchor in this candidate set, keeping pagination
    # deterministic while newly published reviews wait for an explicit refresh.
    reference = max((_utc(row.created_at) for row in candidates),
                    default=datetime.now(timezone.utc))
    for row in candidates:
        overlap = sorted({genre.strip() for genre in (row.game.genres or "").split(",")} & favorite_genres)
        age_days = max(0, (reference - _utc(row.created_at)).total_seconds() / 86400)
        scores[row.id] = (4 if row.game_id in owned else 0) + min(3, len(overlap)) + 2 / (1 + age_days / 7)
        reasons[row.id] = ("In your game library" if row.game_id in owned else
                           f"Because you play {overlap[0]} games" if overlap else "New in the community")
    candidates.sort(key=lambda row: (scores[row.id], row.created_at, row.id), reverse=True)
    # Room under the same 500 cap, so a quiet feed can surface a quote without
    # pushing the review candidates off the end of a full one.
    extras = _popular_reposts(db, user, anchor, q) if len(candidates) < CANDIDATE_LIMIT else []
    if not extras:
        rows = candidates[offset:offset + limit]
        return {"items": _items(db, rows, reasons, user.id), "total": len(candidates), "before": anchor,
                "mode": "for_you", "candidate_limit": CANDIDATE_LIMIT, "library_before": library_anchor}
    packed = [(row, scores[row.id], _utc(row.created_at), row.id, None) for row in candidates]
    for repost in extras:
        row = repost.review
        overlap = sorted({genre.strip() for genre in (row.game.genres or "").split(",")} & favorite_genres)
        age_days = max(0, (reference - _utc(repost.created_at)).total_seconds() / 86400)
        score = (4 if row.game_id in owned else 0) + min(3, len(overlap)) + 2 / (1 + age_days / 7)
        packed.append((row, score, _utc(repost.created_at), repost.id, repost))
    packed.sort(key=lambda item: (item[1], item[2], item[3]), reverse=True)
    packed = packed[:CANDIDATE_LIMIT]
    page = packed[offset:offset + limit]
    page_reasons = []
    for row, _score, _when, _tie, repost in page:
        if repost is None:
            page_reasons.append(reasons[row.id])
        else:
            page_reasons.append(f"{repost.user.display_name} quoted" if repost.quote
                                else f"{repost.user.display_name} reposted")
    items = _items(db, [row for row, *_rest in page], reasons, user.id, page_reasons)
    for item, (_row, _score, _when, _tie, repost) in zip(items, page):
        if repost is not None:
            item["repost"] = repost_card(repost)
    return {"items": items, "total": len(packed), "before": anchor,
            "mode": "for_you", "candidate_limit": CANDIDATE_LIMIT, "library_before": library_anchor}


@router.get("/reviews/{review_id}")
async def review_thread(review_id: ResourceId, request: Request, db: Session = Depends(get_db),
                        viewer: User | None = Depends(get_optional_user)):
    def load(viewer_id):
        query = (db.query(Review).options(joinedload(Review.user), joinedload(Review.game))
                 .filter(Review.id == review_id))
        if viewer_id is not None:
            # A blocked author's review reads as missing, never as "blocked".
            query = query.filter(not_hidden(Review.user_id, viewer_id))
        row = query.first()
        if row is None:
            raise HTTPException(status_code=404, detail="Review not found")
        return _items(db, [row], {row.id: "Review discussion"}, viewer_id)[0]
    return await for_viewer(request, db, viewer, ("thread", review_id), load)
