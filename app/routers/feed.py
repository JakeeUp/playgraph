"""Public reviews and an explainable, bounded personal review feed."""
from collections import Counter
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.deps import get_current_user
from app.models import Comment, Game, PlaytimeSnapshot, Review, User
from app.routers.library import _latest_snapshots
from app.routers.parameters import MAX_OFFSET, MAX_RESOURCE_ID, ResourceId
from app.schemas import GameOut, ReviewOut

router = APIRouter(tags=["feed"])
CANDIDATE_LIMIT = 500


def _query(db: Session, q: str):
    query = db.query(Review).join(Game).filter(Game.content_kind == "game")
    if q.strip():
        query = query.filter(Game.name.icontains(q.strip(), autoescape=True))
    return query.options(joinedload(Review.user), joinedload(Review.game))


def _items(db: Session, rows, reasons):
    ids = [row.id for row in rows]
    counts = dict(db.query(Comment.review_id, func.count(Comment.id))
                  .filter(Comment.review_id.in_(ids)).group_by(Comment.review_id).all()) if ids else {}
    return [{"review": ReviewOut.model_validate(row), "game": GameOut.model_validate(row.game),
             "comment_count": counts.get(row.id, 0), "reason": reasons[row.id]} for row in rows]


@router.get("/feed")
def recent_reviews(limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0, le=MAX_OFFSET),
                   q: str = Query("", max_length=120),
                   before: int | None = Query(None, ge=0, le=MAX_RESOURCE_ID), db: Session = Depends(get_db)):
    anchor = before if before is not None else (db.query(func.max(Review.id)).scalar() or 0)
    query = _query(db, q).filter(Review.id <= anchor)
    total = query.count()
    rows = query.order_by(Review.created_at.desc(), Review.id.desc()).offset(offset).limit(limit).all()
    return {"items": _items(db, rows, {row.id: "New in the community" for row in rows}),
            "total": total, "before": anchor, "mode": "latest"}


@router.get("/me/feed")
def for_you(limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0, le=CANDIDATE_LIMIT),
            q: str = Query("", max_length=120), before: int | None = Query(None, ge=0, le=MAX_RESOURCE_ID),
            library_before: int | None = Query(None, ge=0, le=MAX_RESOURCE_ID),
            user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    anchor = before if before is not None else (db.query(func.max(Review.id)).scalar() or 0)
    library_anchor = library_before if library_before is not None else (
        db.query(func.max(PlaytimeSnapshot.id)).filter(PlaytimeSnapshot.user_id == user.id).scalar() or 0
    )
    candidates = (_query(db, q).filter(Review.id <= anchor, Review.user_id != user.id)
                  .order_by(Review.created_at.desc(), Review.id.desc()).limit(CANDIDATE_LIMIT).all())
    owned, preferences = set(), Counter()
    # Pin the append-only library snapshots too, so a sync cannot reorder later pages.
    for snapshot, game in _latest_snapshots(db, user.id, before_id=library_anchor):
        if game.content_kind != "game":
            continue
        owned.add(game.id)
        for genre in (game.genres or "").split(","):
            if genre.strip() and snapshot.playtime_minutes > 0:
                preferences[genre.strip()] += snapshot.playtime_minutes
    favorite_genres = {genre for genre, _ in preferences.most_common(5)}
    reasons, scores = {}, {}
    # Score against a fixed anchor in this candidate set, keeping pagination
    # deterministic while newly published reviews wait for an explicit refresh.
    reference = max((row.created_at.replace(tzinfo=timezone.utc) for row in candidates),
                    default=datetime.now(timezone.utc))
    for row in candidates:
        overlap = sorted({genre.strip() for genre in (row.game.genres or "").split(",")} & favorite_genres)
        age_days = max(0, (reference - row.created_at.replace(tzinfo=timezone.utc)).total_seconds() / 86400)
        scores[row.id] = (4 if row.game_id in owned else 0) + min(3, len(overlap)) + 2 / (1 + age_days / 7)
        reasons[row.id] = ("In your game library" if row.game_id in owned else
                           f"Because you play {overlap[0]} games" if overlap else "New in the community")
    candidates.sort(key=lambda row: (scores[row.id], row.created_at, row.id), reverse=True)
    rows = candidates[offset:offset + limit]
    return {"items": _items(db, rows, reasons), "total": len(candidates), "before": anchor,
            "mode": "for_you", "candidate_limit": CANDIDATE_LIMIT, "library_before": library_anchor}


@router.get("/reviews/{review_id}")
def review_thread(review_id: ResourceId, db: Session = Depends(get_db)):
    row = (db.query(Review).options(joinedload(Review.user), joinedload(Review.game))
           .filter(Review.id == review_id).first())
    if row is None:
        raise HTTPException(status_code=404, detail="Review not found")
    return _items(db, [row], {row.id: "Review discussion"})[0]
