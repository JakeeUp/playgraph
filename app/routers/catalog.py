"""Public game metadata, without ownership or private play records."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Game
from app.routers.parameters import MAX_OFFSET, ResourceId
from app.schemas import GameOut

router = APIRouter(tags=["games"])


@router.get("/games/{game_id}", response_model=GameOut)
def game_details(game_id: ResourceId, db: Session = Depends(get_db)):
    game = db.get(Game, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="Game not found")
    return game


@router.get("/games")
def catalog(
    q: str = Query("", max_length=120),
    kind: Literal["game", "software", "all"] = "game",
    limit: int = Query(48, ge=1, le=100),
    offset: int = Query(0, ge=0, le=MAX_OFFSET),
    db: Session = Depends(get_db),
):
    query = db.query(Game)
    if kind != "all":
        query = query.filter(Game.content_kind == kind)
    if q.strip():
        query = query.filter(Game.name.icontains(q.strip(), autoescape=True))
    total = query.count()
    games = query.order_by(Game.name, Game.id).offset(offset).limit(limit).all()
    return {"total": total, "games": [GameOut.model_validate(game) for game in games]}
