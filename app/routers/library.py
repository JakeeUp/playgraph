"""Sync, library listing, and the genre breakdown that feeds the profile graph.

Multi-genre decision (settled, do not silently change it): a game's FULL
playtime counts toward EVERY genre tag it carries, rather than being split
evenly across them. So a 100 hour game tagged Action/RPG/Indie contributes
100 hours to all three buckets.

That means the genre totals deliberately do NOT sum to total playtime, and
that is not a bug. The reasoning: almost every game carries several tags, so
splitting would understate every genre at once and make the numbers useless
for the thing the graph is actually answering, which is "how much time has
this person spent on games with X in them."
"""

from typing import Literal
from datetime import datetime, timezone

from arq.jobs import Job
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import case, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from redis.exceptions import RedisError

from app.database import get_db
from app.deps import get_current_user
from app.models import Game, GameStatus, LinkedAccount, Platform, PlaytimeSnapshot, Review, SnapshotSource, User, utcnow
from app.routers.parameters import ResourceId
from app.schemas import GameOut, GameStatusOut, GenreBreakdownOut, LibraryEntryOut, PlayStatus, StatusUpdate, TrophiesOut
from app.replay import build_replay
from app.queue_codec import QUEUE_NAME, deserialize
from app.security import rate_limit
from app.services import psn

router = APIRouter(prefix="/me", tags=["library"])


@router.get("/replay")
def get_replay(year: int | None = Query(None, ge=2000, le=9998),
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    now = utcnow()
    year = year if year is not None else now.year
    if year > now.year:
        raise HTTPException(422, "Choose the current year or an earlier year")
    start = datetime(year, 1, 1, tzinfo=timezone.utc)
    end = min(datetime(year + 1, 1, 1, tzinfo=timezone.utc), now)
    snapshot = PlaytimeSnapshot
    rank = func.row_number().over(partition_by=(snapshot.game_id, snapshot.source,
        case((snapshot.captured_at < start, 0), else_=1)),
        order_by=(snapshot.captured_at.desc(), snapshot.id.desc()))
    ranked = (db.query(snapshot.id.label("id"), rank.label("position"))
        .join(Game, Game.id == snapshot.game_id).filter(snapshot.user_id == user.id,
            snapshot.captured_at < end, Game.content_kind == "game").subquery())
    rows = (db.query(snapshot, Game).join(Game, Game.id == snapshot.game_id)
        .join(ranked, snapshot.id == ranked.c.id)
        .filter(or_(snapshot.captured_at >= start, ranked.c.position == 1)).all())
    game_out = lambda game: GameOut.model_validate(game).model_dump(mode="json")
    snapshots = [{**{column.name: getattr(row, column.name) for column in snapshot.__table__.columns},
        "game": game_out(game)} for row, game in rows]
    first = (db.query(func.min(snapshot.captured_at)).join(Game, Game.id == snapshot.game_id)
        .filter(snapshot.user_id == user.id, snapshot.captured_at < now, Game.content_kind == "game").scalar())
    reviews = [{"game": game_out(game), "rating": review.rating, "created_at": review.created_at}
        for review, game in db.query(Review, Game).join(Game, Game.id == Review.game_id)
        .filter(Review.user_id == user.id, Review.created_at >= start, Review.created_at < end,
            Game.content_kind == "game").all()]
    completed = [{"game": game_out(game), "completed_at": status.completed_at}
        for status, game in db.query(GameStatus, Game).join(Game, Game.id == GameStatus.game_id)
        .filter(GameStatus.user_id == user.id, GameStatus.status == "completed",
            GameStatus.completed_at >= start, GameStatus.completed_at < end, Game.content_kind == "game").all()]
    # Private mutable data stays uncached, just like /library and /statuses.
    result = build_replay(year, snapshots, reviews, completed, now=now, first_sync=first)
    first_year = min(user.created_at.year, first.year if first else now.year, year)
    result["available_years"] = list(range(now.year, max(2000, first_year) - 1, -1))
    return result


def _latest_snapshots(db: Session, user_id: int, before_id: int | None = None,
                      kind: str = "all", source: str | None = None):
    """Every game the user owns, paired with its most recent snapshot per source.

    Snapshots are append-only (see models.py), so a game accumulates one row
    per sync. Both /library and /genres need the newest row per game, so the
    subquery lives here instead of being written twice and drifting. The
    ranking reads ix_playtime_snapshots_user_game_captured rather than the
    table. `kind` filters in SQL so unwanted rows are never turned into objects.

    Ranked per (game, source): a Steam snapshot never hides a PlayStation one
    for the same game, or the other way round. `source` limits to one store.
    """
    snapshot_query = (
        db.query(
            PlaytimeSnapshot.id.label("snapshot_id"),
            func.row_number().over(
                partition_by=(PlaytimeSnapshot.game_id, PlaytimeSnapshot.source),
                order_by=(PlaytimeSnapshot.captured_at.desc(), PlaytimeSnapshot.id.desc()),
            ).label("position"),
        )
        .filter(PlaytimeSnapshot.user_id == user_id)
    )
    if before_id is not None:
        snapshot_query = snapshot_query.filter(PlaytimeSnapshot.id <= before_id)
    if source is not None:
        snapshot_query = snapshot_query.filter(PlaytimeSnapshot.source == source)
    latest_per_game = snapshot_query.subquery()

    query = (
        db.query(PlaytimeSnapshot, Game)
        .join(Game, Game.id == PlaytimeSnapshot.game_id)
        .join(
            latest_per_game,
            (PlaytimeSnapshot.id == latest_per_game.c.snapshot_id)
            & (latest_per_game.c.position == 1),
        )
        .filter(PlaytimeSnapshot.user_id == user_id)
    )
    if kind != "all":
        query = query.filter(Game.content_kind == kind)
    # Unknown (NULL) playtime sorts last on both SQLite and Postgres.
    return query.order_by(PlaytimeSnapshot.playtime_minutes.desc().nulls_last(), PlaytimeSnapshot.id).all()


@router.post("/sync", status_code=202)
async def sync_library(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Kick off a background sync of the user's Steam library. Returns
    immediately with a job id to poll - see GET /me/sync/status/{job_id}.
    A full sync (owned games, per-game achievements, and genre lookups for
    anything new, all rate-limit paced) can take several minutes on a large
    library, which is why this does not run inline.
    """
    linked = (
        db.query(LinkedAccount)
        .filter_by(user_id=user.id, platform=Platform.steam)
        .first()
    )
    if linked is None:
        raise HTTPException(status_code=400, detail="No linked Steam account")

    await rate_limit(request, "sync", str(user.id), 3, 3600)

    return await enqueue_sync(request, "sync_steam_library", user.id, steam_job_id(user.id),
                              "A sync for this account is already in progress or finished "
                              "very recently. Try again in a few minutes.")


@router.get("/sync/status/{job_id}")
async def sync_status(job_id: str, request: Request, user: User = Depends(get_current_user)):
    if job_id != steam_job_id(user.id):
        raise HTTPException(status_code=404, detail="Sync job not found")
    return await job_status(request, job_id, int_keys=("games_synced", "genres_fetched"))


def steam_job_id(user_id: int) -> str:
    return f"sync-json-user-{user_id}"


async def enqueue_sync(request: Request, task: str, user_id: int, job_id: str, busy_detail: str) -> dict:
    """Queue a sync under a fixed per-user job id.

    The fixed id makes this idempotent: arq refuses to enqueue a second job
    with an id that is already queued, running, or holding a recent result,
    and returns None instead. Without this, double-clicking the button starts
    two syncs that race each other writing the same rows (which on SQLite
    shows up as "database is locked", and on Postgres would just be wasted
    API quota and duplicate snapshots).
    """
    try:
        job = await request.app.state.arq_pool.enqueue_job(task, user_id, _job_id=job_id)
    except RedisError:
        raise HTTPException(status_code=503, detail="Sync service unavailable") from None
    if job is None:
        raise HTTPException(status_code=409, detail=busy_detail)
    return {"job_id": job.job_id, "status": "queued"}


async def job_status(request: Request, job_id: str, int_keys: tuple[str, ...] = (),
                     bool_keys: tuple[str, ...] = ()) -> dict:
    """A sync job's arq status, passing through only the whitelisted result fields."""
    job = Job(job_id, request.app.state.arq_pool, _queue_name=QUEUE_NAME, _deserializer=deserialize)
    try:
        status = await job.status()
        result = None
        if status.name == "complete":
            info = await job.result_info()
            if info is None or not info.success:
                return {"job_id": job_id, "status": "failed", "result": None}
            data = info.result if isinstance(info.result, dict) else {}
            result = {k: data[k] for k in int_keys if type(data.get(k)) is int}
            result.update({k: data[k] for k in bool_keys if type(data.get(k)) is bool})
        return {"job_id": job_id, "status": status.name, "result": result}
    except RedisError:
        raise HTTPException(status_code=503, detail="Sync status unavailable") from None


@router.get("/library", response_model=list[LibraryEntryOut])
def get_library(user: User = Depends(get_current_user), db: Session = Depends(get_db),
                kind: Literal["game", "software", "all"] = "all"):
    """Each owned game joined with its most recent PlaytimeSnapshot, one entry
    per source. Clients must keep sources apart; see LibraryEntryOut."""
    return [
        LibraryEntryOut(
            game=game,
            source=snapshot.source,
            playtime_minutes=snapshot.playtime_minutes,
            achievements_unlocked=snapshot.achievements_unlocked,
            achievements_total=snapshot.achievements_total,
            trophies=_trophies(snapshot),
            captured_at=snapshot.captured_at,
        )
        for snapshot, game in _latest_snapshots(db, user.id, kind=kind)
    ]


def _trophies(snapshot: PlaytimeSnapshot) -> TrophiesOut | None:
    if snapshot.source != SnapshotSource.psn.value or snapshot.trophies_bronze_total is None:
        return None
    return TrophiesOut(
        earned={grade: getattr(snapshot, f"trophies_{grade}") or 0 for grade in psn.TROPHY_GRADES},
        total={grade: getattr(snapshot, f"trophies_{grade}_total") or 0 for grade in psn.TROPHY_GRADES},
        progress=snapshot.trophy_progress,
    )


# Play statuses are set by hand and never touched by a sync; see GameStatus.
# The cap keeps GET /me/statuses bounded. It is far above any real backlog.
MAX_STATUSES = 5000
STATUS_WRITES_PER_HOUR = 300


@router.get("/statuses", response_model=list[GameStatusOut])
def get_statuses(user: User = Depends(get_current_user), db: Session = Depends(get_db),
                 status: PlayStatus | None = None):
    """Every game the player has given a status, newest change first.
    Includes games they don't own, which is the point of "want to play"."""
    query = (db.query(GameStatus).join(Game, Game.id == GameStatus.game_id)
             .filter(GameStatus.user_id == user.id))
    if status is not None:
        query = query.filter(GameStatus.status == status)
    return query.order_by(GameStatus.updated_at.desc(), GameStatus.id.desc()).limit(MAX_STATUSES).all()


@router.put("/games/{game_id}/status", response_model=GameStatusOut)
async def set_status(game_id: ResourceId, change: StatusUpdate, request: Request,
                     user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    await rate_limit(request, "status", str(user.id), STATUS_WRITES_PER_HOUR, 3600)
    if db.get(Game, game_id) is None:
        raise HTTPException(status_code=404, detail="Game not found")
    row = db.query(GameStatus).filter_by(user_id=user.id, game_id=game_id).first()
    if row is None:
        if db.query(func.count(GameStatus.id)).filter(GameStatus.user_id == user.id).scalar() >= MAX_STATUSES:
            raise HTTPException(status_code=409, detail="You've reached the limit of games with a status.")
        row = GameStatus(user_id=user.id, game_id=game_id)
        db.add(row)
    _apply(row, change.status)
    try:
        db.commit()
    except IntegrityError:
        # Two tabs set a status for the same game at once. The unique
        # constraint kept one row; apply this change to it.
        db.rollback()
        row = db.query(GameStatus).filter_by(user_id=user.id, game_id=game_id).one()
        _apply(row, change.status)
        db.commit()
    db.refresh(row)
    return row


def _apply(row: GameStatus, status: str) -> None:
    now = utcnow()
    if status == "completed" and row.status != "completed":
        row.completed_at = now
    elif status != "completed":
        row.completed_at = None
    row.status = status
    row.updated_at = now


@router.delete("/games/{game_id}/status", status_code=204)
async def clear_status(game_id: ResourceId, request: Request,
                       user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    await rate_limit(request, "status", str(user.id), STATUS_WRITES_PER_HOUR, 3600)
    db.query(GameStatus).filter_by(user_id=user.id, game_id=game_id).delete(synchronize_session=False)
    db.commit()
    return Response(status_code=204)


@router.get("/genres", response_model=list[GenreBreakdownOut])
def get_genre_breakdown(
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
    kind: Literal["game", "software", "all"] = "game",
):
    """Playtime aggregated by genre. This is what the profile graph reads.

    Aggregated in Python rather than SQL because Game.genres is a comma
    separated string, so there is nothing for the database to GROUP BY yet.
    That is a deliberate v1 shortcut, and it is the first thing to revisit
    if this ever needs to answer cross-user questions like "average hours
    per genre across everyone" - at that point genres want their own table
    and a join.

    Steam only: genres come from the Steam store, and PlayStation hours are
    reported separately rather than folded into these totals.
    """
    totals: dict[str, dict[str, int]] = {}

    for snapshot, game in _latest_snapshots(db, user.id, kind=kind, source=SnapshotSource.steam.value):
        if not game.genres:
            continue
        minutes = snapshot.playtime_minutes or 0
        for raw in game.genres.split(","):
            genre = raw.strip()
            if not genre:
                continue
            bucket = totals.setdefault(genre, {"minutes": 0, "games": 0})
            bucket["minutes"] += minutes
            bucket["games"] += 1

    return sorted(
        (
            GenreBreakdownOut(
                genre=genre, total_minutes=v["minutes"], game_count=v["games"]
            )
            for genre, v in totals.items()
        ),
        key=lambda g: g.total_minutes,
        reverse=True,
    )


@router.get("/recommendations")
def get_recommendations(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    TODO (milestone 4): content-based to start. Take the user's top 3-5
    genres by playtime from /genres, find games sharing those genres that
    the user does not already own, and rank by genre overlap plus Steam's
    own review score. Response shape: list[RecommendationOut].

    Gated behind the session before the body exists, so the contract is already
    right when it is written: every other /me route is private, and this one
    will read the caller's own library. An unbuilt endpoint answers 501 rather
    than raising, which would surface to an anonymous caller as a 500.
    """
    raise HTTPException(status_code=501, detail="Recommendations are not available yet")
