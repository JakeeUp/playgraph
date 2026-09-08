"""Reviews retain the verified play data available when they were written."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import Comment, Game, PlaytimeSnapshot, Review, User
from app.schemas import CommentCreate, CommentOut, ReviewCreate, ReviewOut

router = APIRouter(tags=["reviews"])


@router.post("/games/{game_id}/reviews", response_model=ReviewOut, status_code=201)
def create_review(
    game_id: int,
    review: ReviewCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if db.get(Game, game_id) is None:
        raise HTTPException(status_code=404, detail="Game not found")
    if db.query(Review).filter_by(user_id=user.id, game_id=game_id).first():
        raise HTTPException(status_code=409, detail="You've already reviewed this game")
    snapshot = (
        db.query(PlaytimeSnapshot)
        .filter_by(user_id=user.id, game_id=game_id)
        .order_by(PlaytimeSnapshot.captured_at.desc(), PlaytimeSnapshot.id.desc())
        .first()
    )
    row = Review(user_id=user.id, game_id=game_id, **review.model_dump())
    # Missing snapshots stay unverified. Zero minutes is still a verified value.
    if snapshot is not None:
        row.verified_playtime_minutes = snapshot.playtime_minutes
        if snapshot.achievements_total and snapshot.achievements_unlocked is not None:
            row.verified_achievement_pct = (
                100 * snapshot.achievements_unlocked / snapshot.achievements_total
            )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        # The unique constraint also covers two requests arriving together.
        db.rollback()
        if db.query(Review).filter_by(user_id=user.id, game_id=game_id).first():
            raise HTTPException(status_code=409, detail="You've already reviewed this game")
        raise
    db.refresh(row)
    return row


@router.get("/games/{game_id}/reviews", response_model=list[ReviewOut])
def list_reviews(
    game_id: int,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    if db.get(Game, game_id) is None:
        raise HTTPException(status_code=404, detail="Game not found")
    return (
        db.query(Review)
        .filter_by(game_id=game_id)
        .order_by(
            Review.verified_playtime_minutes.is_(None),
            Review.verified_playtime_minutes.desc(),
            Review.created_at.desc(),
            Review.id.desc(),
        )
        .offset(offset).limit(limit).all()
    )


@router.post("/reviews/{review_id}/comments", response_model=CommentOut, status_code=201)
def create_comment(
    review_id: int,
    comment: CommentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if db.get(Review, review_id) is None:
        raise HTTPException(status_code=404, detail="Review not found")
    row = Comment(review_id=review_id, user_id=user.id, body=comment.body)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/reviews/{review_id}/comments", response_model=list[CommentOut])
def list_comments(
    review_id: int,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    if db.get(Review, review_id) is None:
        raise HTTPException(status_code=404, detail="Review not found")
    return (
        db.query(Comment).filter_by(review_id=review_id)
        .order_by(Comment.created_at, Comment.id)
        .offset(offset).limit(limit).all()
    )
