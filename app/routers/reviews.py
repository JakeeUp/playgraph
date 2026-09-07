"""Milestone 3: reviews, comments, and the verified-playtime credibility badge.

Not implemented yet - sketched so the route shape exists.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import CommentCreate, ReviewCreate

router = APIRouter(tags=["reviews"])


@router.post("/games/{game_id}/reviews")
def create_review(game_id: int, review: ReviewCreate, db: Session = Depends(get_db)):
    """
    TODO (milestone 3):
    1. Create the Review row
    2. Look up the user's most recent PlaytimeSnapshot for this game_id
    3. Copy playtime_minutes / achievement pct onto the review's
       verified_playtime_minutes / verified_achievement_pct fields at
       creation time (see the comment on Review in app/models.py for why
       this is denormalized instead of computed live)
    4. What happens if they have no PlaytimeSnapshot for this game at all -
       reject the review, or allow it as an explicitly "unverified" review?
       That's a real product decision, not just an implementation detail.
    """
    raise NotImplementedError


@router.get("/games/{game_id}/reviews")
def list_reviews(game_id: int, db: Session = Depends(get_db)):
    """TODO (milestone 3): list reviews for a game, most-verified-first probably."""
    raise NotImplementedError


@router.post("/reviews/{review_id}/comments")
def create_comment(review_id: int, comment: CommentCreate, db: Session = Depends(get_db)):
    """TODO (milestone 3)."""
    raise NotImplementedError
