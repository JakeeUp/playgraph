"""Explore: this week's trends, and players the viewer does not follow yet.

Trends are one public blob. Blocks only hide a review from the people involved,
after the blob is read, so one player's blocks never change what others cache.
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_optional_user
from app.models import User
from app.security import app_store
from app.trending import load_trending, suggest_accounts
from app.visibility import blocked_either_way, has_blocks

router = APIRouter(tags=["explore"])


def _for_viewer(payload: dict, db: Session, viewer: User | None) -> dict:
    if viewer is None or not has_blocks(db, viewer):
        return payload
    reviews = [item for item in payload["reviews"]
               if not blocked_either_way(db, viewer.id, item["review"]["user_id"])]
    return {**payload, "reviews": reviews}


@router.get("/explore")
async def explore(request: Request, db: Session = Depends(get_db),
                  viewer: User | None = Depends(get_optional_user)):
    payload = await load_trending(app_store(request), db)
    return _for_viewer(payload, db, viewer)


@router.get("/explore/suggestions")
def suggestions(db: Session = Depends(get_db), viewer: User | None = Depends(get_optional_user)):
    return {"items": suggest_accounts(db, viewer)}
