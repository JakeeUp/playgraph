"""Keep the catalog in step with IGDB: exact store-ID mapping, then metadata.

Runs as an arq job (refresh_igdb_catalog), queued after every library sync
and runnable by hand with `python -m app.igdb_catalog` (add --force to
refresh every game's metadata, not just stale ones). One run:

1. Maps games that have no IGDB ID yet by exact store ID through IGDB's
   external_games: Steam games by app ID, PlayStation games by Sony concept
   ID. Never by name.
2. If that IGDB game already belongs to a different PlayGraph game, nothing
   is merged: the pair is recorded in igdb_match_candidates for the owner.
3. Refreshes metadata (summary, release date, cover, platforms, and genres
   where the store gave none) for mapped games never refreshed or older than
   REFRESH_DAYS.

A first run over ~2,600 games is about a dozen IGDB requests: IGDB answers
500 rows per request.
"""
import asyncio
import logging
import sys
from datetime import timedelta

from sqlalchemy import or_

from app.cache import CATALOG, invalidate
from app.config import settings
from app.database import SessionLocal
from app.models import Game, GameExternalId, GamePlatform, IgdbMatchCandidate, utcnow
from app.services import igdb

logger = logging.getLogger("playgraph.igdb")
PROVIDER = "igdb"
JOB_ID = "igdb-refresh"
REFRESH_DAYS = 30


def _igdb_owners(db) -> dict[int, int]:
    """{IGDB game id: PlayGraph game id} for every mapped game."""
    return {int(external_id): game_id for game_id, external_id in
            db.query(GameExternalId.game_id, GameExternalId.external_id).filter_by(provider=PROVIDER)}


def _propose(db, game_id: int, igdb_id: int, reason: str) -> bool:
    exists = db.query(IgdbMatchCandidate.id).filter_by(game_id=game_id, igdb_id=igdb_id).first()
    if exists:
        return False
    db.add(IgdbMatchCandidate(game_id=game_id, igdb_id=igdb_id, reason=reason))
    return True


def _unmapped_store_ids(db, store: str) -> list[tuple[int, str]]:
    """(game id, store uid) for games with no IGDB ID yet, oldest game first."""
    mapped = {game_id for (game_id,) in db.query(GameExternalId.game_id).filter_by(provider=PROVIDER)}
    if store == "steam":
        rows = db.query(Game.id, Game.steam_appid).filter(Game.steam_appid.is_not(None))
    else:
        rows = db.query(GameExternalId.game_id, GameExternalId.external_id).filter_by(provider="psn_concept")
    return sorted((game_id, str(uid)) for game_id, uid in rows if game_id not in mapped)


async def map_store_games(db, client: igdb.IGDBClient, sources: dict[str, int], store: str) -> dict:
    """Attach IGDB IDs to one store's games by exact store ID."""
    name = igdb.SOURCE_STEAM if store == "steam" else igdb.SOURCE_PLAYSTATION_STORE
    empty = {f"{store}_mapped": 0, f"{store}_proposed": 0}
    source = igdb.source_id(sources, name)
    if source is None:
        logger.warning("igdb: no '%s' external game source; %s mapping skipped", name, store)
        return empty
    candidates = _unmapped_store_ids(db, store)
    if not candidates:
        return empty
    matches = await client.external_games(source, [uid for _, uid in candidates])
    owners = _igdb_owners(db)
    mapped = proposed = 0
    for game_id, uid in candidates:
        igdb_id = matches.get(uid)
        if igdb_id is None:
            continue
        owner = owners.get(igdb_id)
        if owner is None:
            db.add(GameExternalId(game_id=game_id, provider=PROVIDER, external_id=str(igdb_id)))
            owners[igdb_id] = game_id
            mapped += 1
        elif owner != game_id:
            # Same IGDB game as another record: the Steam copy of a PlayStation
            # game, or another edition. Merging moves reviews and stats, so the
            # owner decides.
            proposed += _propose(db, game_id, igdb_id, "igdb_taken")
    db.commit()
    return {f"{store}_mapped": mapped, f"{store}_proposed": proposed}


def apply_metadata(db, game: Game, details: dict) -> None:
    game.summary = details["summary"]
    game.first_release_date = details["first_release_date"]
    game.cover_image_id = details["cover_image_id"]
    game.hero_image_id = details["hero_image_id"]
    if not game.genres and details["genres"]:
        game.genres = ",".join(details["genres"])[:500]
    wanted = {p["slug"]: p for p in details["platforms"]}
    for platform in list(game.platforms):
        if platform.slug not in wanted:
            game.platforms.remove(platform)
    have = {p.slug for p in game.platforms}
    for slug, platform in wanted.items():
        if slug not in have:
            game.platforms.append(GamePlatform(slug=slug, name=platform["name"][:80],
                                               abbreviation=(platform["abbreviation"] or None)))
    game.igdb_refreshed_at = utcnow()


async def refresh_metadata(db, client: igdb.IGDBClient, *, force: bool = False) -> int:
    """Refresh games mapped to IGDB, plus games whose exact match is waiting
    as a merge proposal: art and facts belong to the IGDB game, so both copies
    show them while the owner decides. Nothing is merged here."""
    cutoff = utcnow() - timedelta(days=REFRESH_DAYS)
    mapped = (db.query(Game, GameExternalId.external_id)
              .join(GameExternalId, (GameExternalId.game_id == Game.id) & (GameExternalId.provider == PROVIDER)))
    proposed = (db.query(Game, IgdbMatchCandidate.igdb_id)
                .join(IgdbMatchCandidate, IgdbMatchCandidate.game_id == Game.id)
                .filter(IgdbMatchCandidate.reason == "igdb_taken", IgdbMatchCandidate.status == "pending"))
    by_igdb: dict[int, list[Game]] = {}
    for query in (mapped, proposed):
        if not force:
            query = query.filter(or_(Game.igdb_refreshed_at.is_(None), Game.igdb_refreshed_at < cutoff))
        for game, igdb_id in query:
            by_igdb.setdefault(int(igdb_id), []).append(game)
    if not by_igdb:
        return 0
    refreshed = 0
    for details in await client.games(by_igdb):
        for game in by_igdb.get(details["igdb_id"], []):
            apply_metadata(db, game, details)
            refreshed += 1
    db.commit()
    return refreshed


async def refresh_igdb_catalog(ctx, force: bool = False) -> dict:
    """arq task: map new games to IGDB and refresh stale metadata."""
    if not settings.igdb_enabled:
        return {"status": "skipped", "detail": "IGDB is not configured"}
    db = SessionLocal(expire_on_commit=False)
    try:
        async with igdb.IGDBClient() as client:
            sources = await client.external_game_sources()
            stats = await map_store_games(db, client, sources, "steam")
            stats.update(await map_store_games(db, client, sources, "psn"))
            stats["metadata_refreshed"] = await refresh_metadata(db, client, force=force)
    except igdb.IGDBAuthError:
        db.rollback()
        logger.error("igdb refresh failed: Twitch rejected IGDB_CLIENT_ID/IGDB_CLIENT_SECRET. "
                     "Check the application at https://dev.twitch.tv/console/apps.")
        return {"status": "error", "detail": "IGDB credentials rejected"}
    finally:
        db.close()
    if stats["steam_mapped"] or stats["psn_mapped"] or stats["metadata_refreshed"]:
        await invalidate(ctx.get("redis"), CATALOG)
    logger.info("igdb refresh complete: mapped %d Steam and %d PlayStation games, %d merge proposals, "
                "%d games refreshed", stats["steam_mapped"], stats["psn_mapped"],
                stats["steam_proposed"] + stats["psn_proposed"], stats["metadata_refreshed"])
    return {"status": "ok", **stats}


async def queue_refresh(store) -> None:
    """Queue a catalog refresh after a sync. One at a time, by fixed job id;
    a refresh already queued covers the new games too."""
    if store is None or not settings.igdb_enabled:
        return
    try:
        await store.enqueue_job("refresh_igdb_catalog", _job_id=JOB_ID)
    except Exception as exc:  # advisory: a sync must never fail because of this
        logger.warning("igdb refresh not queued: %s", type(exc).__name__)


async def _main() -> None:
    from arq import create_pool
    from arq.connections import RedisSettings

    from app.queue_codec import QUEUE_NAME, deserialize, serialize

    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url.get_secret_value()),
                             job_serializer=serialize, job_deserializer=deserialize, default_queue_name=QUEUE_NAME)
    force = "--force" in sys.argv  # refresh every game's metadata, not only stale ones
    try:
        job = await pool.enqueue_job("refresh_igdb_catalog", force, _job_id=JOB_ID)
        print("IGDB refresh queued." if job else "An IGDB refresh is already queued or ran very recently.")
    finally:
        await pool.aclose()


if __name__ == "__main__":
    asyncio.run(_main())
