"""Public game metadata, without ownership or private play records."""

import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select, or_
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel, Field
from fastapi.encoders import jsonable_encoder

from app.cache import CATALOG, cached, invalidate
from app.config import settings
from app.database import get_db
from app.deps import get_current_user, get_optional_user
from app.igdb_catalog import apply_metadata
from app.models import Game, GameExternalId, GamePlatform, Review, User, utcnow
from app.routers.parameters import MAX_OFFSET, MAX_RESOURCE_ID, ResourceId
from app.schemas import GameDetailOut, GameOut
from app.security import app_store, rate_limit, redis_call, state_key
from app.services.igdb import IGDBClient, IGDBError, IGDBRateLimitedError, RESTRICTED_SUMMARY, cover_url, search_text

router = APIRouter(tags=["games"])


class GameImport(BaseModel):
    igdb_id: int = Field(strict=True, ge=1, le=2147483647)


def imported(db, igdb_id):
    return (db.query(Game).join(GameExternalId).filter(GameExternalId.provider == "igdb",
            GameExternalId.external_id == str(igdb_id)).first())


def provider_failure(error):
    if isinstance(error, IGDBRateLimitedError):
        return HTTPException(status_code=429, detail="Game search is busy. Try again shortly.", headers={"Retry-After": "1"})
    return HTTPException(status_code=503, detail="Game search is temporarily unavailable. Try again shortly.")


def imports_enabled():
    if settings.demo_mode:
        raise HTTPException(status_code=404, detail="Not found")


def browseable(query):
    # IGDB keeps a restricted page as a title plus this sentence. Those pages
    # stay out of search and the catalog. A direct library link is unchanged.
    hidden = Game.summary.ilike("%" + RESTRICTED_SUMMARY + "%")
    return query.filter(or_(Game.summary.is_(None), ~hidden))


def restricted(game) -> bool:
    return bool(game.summary and RESTRICTED_SUMMARY in game.summary.casefold())


@router.get("/games/search", dependencies=[Depends(imports_enabled)])
async def search_games(request: Request, q: str = Query(..., min_length=2, max_length=120),
                       platform: str = Query("", pattern=r"^([a-z0-9][a-z0-9-]{0,60})?$"),
                       user: User | None = Depends(get_optional_user), db: Session = Depends(get_db)):
    text = search_text(q).casefold()
    if len(text) < 2:
        raise HTTPException(status_code=422, detail="Enter at least two letters")
    query = browseable(db.query(Game).filter(Game.content_kind == "game", Game.name.icontains(text, autoescape=True)))
    if platform:
        query = query.filter(Game.platforms.any(GamePlatform.slug == platform))
    local = query.order_by(Game.name, Game.id).limit(20).all()
    result = {"local": [GameDetailOut.model_validate(game) for game in local], "external": []}
    if user is None:
        return result
    await rate_limit(request, "igdb-search", str(user.id), 20, 60)
    key = state_key("igdb-search-v1", f"{text}:{platform}")
    data = await redis_call(request, "get", key)
    rows = json.loads(data) if data else None
    if rows is None:
        try:
            async with IGDBClient(store=app_store(request, required=True)) as client:
                rows = await client.search(text, platform_slugs=[platform] if platform else [])
        except IGDBError as error:
            raise provider_failure(error) from None
        await redis_call(request, "set", key, json.dumps(jsonable_encoder(rows)), ex=86400)
    owners = dict(db.query(GameExternalId.external_id, GameExternalId.game_id).filter(
        GameExternalId.provider == "igdb", GameExternalId.external_id.in_([str(row["igdb_id"]) for row in rows])))
    result["external"] = [{"igdb_id": row["igdb_id"], "name": row["name"],
        "first_release_date": row["first_release_date"], "cover_url": cover_url(row["cover_image_id"]),
        "platforms": row["platforms"], "game_id": owners.get(str(row["igdb_id"]))} for row in rows]
    return result


@router.post("/games/import", response_model=GameDetailOut, dependencies=[Depends(imports_enabled)])
async def import_game(body: GameImport, request: Request, user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    existing = imported(db, body.igdb_id)
    if existing:
        return existing
    await rate_limit(request, "igdb-import", str(user.id), 30, 86400)
    try:
        async with IGDBClient(store=app_store(request, required=True)) as client:
            details = await client.import_game(body.igdb_id)
    except IGDBError as error:
        raise provider_failure(error) from None
    if details is None:
        raise HTTPException(status_code=404, detail="No eligible game found. DLC and editions cannot be added.")
    game = Game(name=details["name"], added_by_user_id=user.id, added_at=utcnow())
    db.add(game)
    apply_metadata(db, game, details)
    game.external_ids.append(GameExternalId(provider="igdb", external_id=str(body.igdb_id)))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        game = imported(db, body.igdb_id)
        if game is None:
            raise
    await invalidate(app_store(request), CATALOG)
    return game


@router.get("/games/{game_id}", response_model=GameDetailOut)
async def game_details(game_id: ResourceId, request: Request, db: Session = Depends(get_db)):
    def load():
        game = db.get(Game, game_id)
        if game is None or restricted(game):
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
    after: int | None = Query(None, ge=1, le=MAX_RESOURCE_ID),
    sort: Literal["name", "newest", "reviewed"] = "name",
    platform: str = Query("", pattern=r"^([a-z0-9][a-z0-9-]{0,60})?$"),
    db: Session = Depends(get_db),
):
    if after is not None and (offset or sort != "name"):
        raise HTTPException(422, "Title cursors cannot be combined with offsets or another sort")
    # Every order here uses public data only. Ranking by how many people own
    # or play a game would, with a handful of players, publish one person's
    # private library, so there is deliberately no "most played" order.
    def load():
        query = browseable(db.query(Game))
        if kind != "all":
            query = query.filter(Game.content_kind == kind)
        if q.strip():
            query = query.filter(Game.name.icontains(q.strip(), autoescape=True))
        if platform:
            query = query.filter(Game.platforms.any(GamePlatform.slug == platform))
        total = query.count()
        if after is not None:
            anchor = db.get(Game, after)
            if anchor is None:
                raise HTTPException(400, "Catalog changed. Start again from the first page.")
            query = query.filter(or_(Game.name > anchor.name, (Game.name == anchor.name) & (Game.id > anchor.id)))
        if sort == "newest":
            order = (Game.first_release_date.is_(None), Game.first_release_date.desc(), Game.name, Game.id)
        elif sort == "reviewed":
            reviews = (select(func.count(Review.id)).where(Review.game_id == Game.id)
                       .correlate(Game).scalar_subquery())
            order = (reviews.desc(), Game.name, Game.id)
        else:
            order = (Game.name, Game.id)
        games = query.order_by(*order).offset(offset).limit(limit + 1).all()
        next_after = games[limit - 1].id if sort == "name" and len(games) > limit else None
        return {"total": total, "games": [GameOut.model_validate(game) for game in games[:limit]],
                "next_after": next_after}
    return await cached(request, CATALOG, ("games", kind, limit, offset, q.strip(), sort, platform, after), 60, load)
