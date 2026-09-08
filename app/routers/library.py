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

from arq.jobs import Job
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session
from redis.exceptions import RedisError

from app.database import get_db
from app.deps import get_current_user
from app.models import Game, LinkedAccount, Platform, PlaytimeSnapshot, User
from app.schemas import GenreBreakdownOut, LibraryEntryOut
from app.queue_codec import QUEUE_NAME, deserialize
from app.security import rate_limit

router = APIRouter(prefix="/me", tags=["library"])


def _latest_snapshots(db: Session, user_id: int):
    """Every game the user owns, paired with its most recent snapshot.

    Snapshots are append-only (see models.py), so a game accumulates one row
    per sync. Both /library and /genres need the newest row per game, so the
    subquery lives here instead of being written twice and drifting.
    """
    latest_per_game = (
        db.query(
            PlaytimeSnapshot.game_id,
            func.max(PlaytimeSnapshot.captured_at).label("latest_captured_at"),
        )
        .filter(PlaytimeSnapshot.user_id == user_id)
        .group_by(PlaytimeSnapshot.game_id)
        .subquery()
    )

    return (
        db.query(PlaytimeSnapshot, Game)
        .join(Game, Game.id == PlaytimeSnapshot.game_id)
        .join(
            latest_per_game,
            (PlaytimeSnapshot.game_id == latest_per_game.c.game_id)
            & (PlaytimeSnapshot.captured_at == latest_per_game.c.latest_captured_at),
        )
        .filter(PlaytimeSnapshot.user_id == user_id)
        .order_by(PlaytimeSnapshot.playtime_minutes.desc())
        .all()
    )


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

    # A fixed job id per user makes this idempotent: arq refuses to enqueue
    # a second job with an id that is already queued, running, or holding a
    # recent result, and returns None instead. Without this, double-clicking
    # the button starts two syncs that race each other writing the same rows
    # (which on SQLite shows up as "database is locked", and on Postgres
    # would just be wasted API quota and duplicate snapshots).
    try:
        job = await request.app.state.arq_pool.enqueue_job(
            "sync_steam_library", user.id, _job_id=f"sync-json-user-{user.id}"
        )
    except RedisError:
        raise HTTPException(status_code=503, detail="Sync service unavailable") from None

    if job is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "A sync for this account is already in progress or finished "
                "very recently. Try again in a few minutes."
            ),
        )

    return {"job_id": job.job_id, "status": "queued"}


@router.get("/sync/status/{job_id}")
async def sync_status(job_id: str, request: Request, user: User = Depends(get_current_user)):
    if job_id != f"sync-json-user-{user.id}":
        raise HTTPException(status_code=404, detail="Sync job not found")
    job = Job(job_id, request.app.state.arq_pool, _queue_name=QUEUE_NAME, _deserializer=deserialize)
    try:
        status = await job.status()
        result = None
        if status.name == "complete":
            info = await job.result_info()
            if info is None or not info.success:
                return {"job_id": job_id, "status": "failed", "result": None}
            data = info.result if isinstance(info.result, dict) else {}
            result = {k: data[k] for k in ("games_synced", "genres_fetched")
                      if type(data.get(k)) is int}
        return {"job_id": job_id, "status": status.name, "result": result}
    except RedisError:
        raise HTTPException(status_code=503, detail="Sync status unavailable") from None


@router.get("/library", response_model=list[LibraryEntryOut])
def get_library(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Each owned game joined with its most recent PlaytimeSnapshot."""
    return [
        LibraryEntryOut(
            game=game,
            playtime_minutes=snapshot.playtime_minutes,
            achievements_unlocked=snapshot.achievements_unlocked,
            achievements_total=snapshot.achievements_total,
            captured_at=snapshot.captured_at,
        )
        for snapshot, game in _latest_snapshots(db, user.id)
    ]


@router.get("/genres", response_model=list[GenreBreakdownOut])
def get_genre_breakdown(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Playtime aggregated by genre. This is what the profile graph reads.

    Aggregated in Python rather than SQL because Game.genres is a comma
    separated string, so there is nothing for the database to GROUP BY yet.
    That is a deliberate v1 shortcut, and it is the first thing to revisit
    if this ever needs to answer cross-user questions like "average hours
    per genre across everyone" - at that point genres want their own table
    and a join.
    """
    totals: dict[str, dict[str, int]] = {}

    for snapshot, game in _latest_snapshots(db, user.id):
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
def get_recommendations(db: Session = Depends(get_db)):
    """
    TODO (milestone 4): content-based to start. Take the user's top 3-5
    genres by playtime from /genres, find games sharing those genres that
    the user does not already own, and rank by genre overlap plus Steam's
    own review score. Response shape: list[RecommendationOut].
    """
    raise NotImplementedError
