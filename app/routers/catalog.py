"""Public game metadata, without ownership or private play records."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.cache import CATALOG, cached
from app.database import get_db
from app.models import Game
from app.routers.parameters import MAX_OFFSET, ResourceId
from app.schemas import GameOut

router = APIRouter(tags=["games"])


@router.get("/games/{game_id}", response_model=GameOut)
async def game_details(game_id: ResourceId, request: Request, db: Session = Depends(get_db)):
    def load():
        game = db.get(Game, game_id)
        if game is None:
            raise HTTPException(status_code=404, detail="Game not found")
        return GameOut.model_validate(game)
    return await cached(request, CATALOG, ("game", game_id), 300, load)


@router.get("/games")
async def catalog(
    request: Request,
    q: str = Query("", max_length=120),
    kind: Literal["game", "software", "all"] = "game",
    limit: int = Query(48, ge=1, le=100),
    offset: int = Query(0, ge=0, le=MAX_OFFSET),
    db: Session = Depends(get_db),
):
    def load():
        query = db.query(Game)
        if kind != "all":
            query = query.filter(Game.content_kind == kind)
        if q.strip():
            query = query.filter(Game.name.icontains(q.strip(), autoescape=True))
        total = query.count()
        games = query.order_by(Game.name, Game.id).offset(offset).limit(limit).all()
        return {"total": total, "games": [GameOut.model_validate(game) for game in games]}
    return await cached(request, CATALOG, ("games", kind, limit, offset, q.strip()), 60, load)
