"""Public game metadata, without ownership or private play records."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.cache import CATALOG, cached
from app.database import get_db
from app.models import Game, Review
from app.routers.parameters import MAX_OFFSET, ResourceId
from app.schemas import GameDetailOut, GameOut

router = APIRouter(tags=["games"])


@router.get("/games/{game_id}", response_model=GameDetailOut)
async def game_details(game_id: ResourceId, request: Request, db: Session = Depends(get_db)):
    def load():
        game = db.get(Game, game_id)
        if game is None:
            raise HTTPException(status_code=404, detail="Game not found")
        return GameDetailOut.model_validate(game)
    return await cached(request, CATALOG, ("game-detail", game_id), 300, load)


@router.get("/games")
async def catalog(
    request: Request,
    q: str = Query("", max_length=120),
    kind: Literal["game", "software", "all"] = "game",
    limit: int = Query(48, ge=1, le=100),
    offset: int = Query(0, ge=0, le=MAX_OFFSET),
    sort: Literal["name", "newest", "reviewed"] = "name",
    db: Session = Depends(get_db),
):
    # Every order here uses public data only. Ranking by how many people own
    # or play a game would, with a handful of players, publish one person's
    # private library, so there is deliberately no "most played" order.
    def load():
        query = db.query(Game)
        if kind != "all":
            query = query.filter(Game.content_kind == kind)
        if q.strip():
            query = query.filter(Game.name.icontains(q.strip(), autoescape=True))
        total = query.count()
        if sort == "newest":
            order = (Game.first_release_date.is_(None), Game.first_release_date.desc(), Game.name, Game.id)
        elif sort == "reviewed":
            reviews = (select(func.count(Review.id)).where(Review.game_id == Game.id)
                       .correlate(Game).scalar_subquery())
            order = (reviews.desc(), Game.name, Game.id)
        else:
            order = (Game.name, Game.id)
        games = query.order_by(*order).offset(offset).limit(limit).all()
        return {"total": total, "games": [GameOut.model_validate(game) for game in games]}
    return await cached(request, CATALOG, ("games", kind, limit, offset, q.strip(), sort), 60, load)
