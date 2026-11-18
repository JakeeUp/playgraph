"""Bounded IGDB catalog batches, then six-hourly incremental refreshes.

Enable IGDB_CATALOG_SYNC_ENABLED on the worker after migrating. To queue a
batch manually: python -m app.catalog_sync. The worker owns all writes.
"""
import argparse
import asyncio
import json
import logging
import shutil
import time
from pathlib import Path

from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload

from app.cache import CATALOG, REVIEWS, invalidate
from app.config import settings
from app.database import SessionLocal
from app.igdb_catalog import _propose, apply_metadata
from app.models import CatalogSyncState, Game, GameExternalId, utcnow
from app.queue_codec import QUEUE_NAME, deserialize, serialize
from app.services import igdb

logger = logging.getLogger(__name__)
PAGES_PER_JOB = 5
REFRESH_SECONDS = 6 * 3600
MIN_FREE_BYTES = 256 * 1024 * 1024
JOB_ID = "igdb-catalog-sync"


def upsert_page(db, rows: list[dict], steam_ids: dict[int, list[int]]) -> dict:
    ids = [str(row["igdb_id"]) for row in rows]
    mapped = (db.query(Game, GameExternalId.external_id).join(GameExternalId)
              .filter(GameExternalId.provider == "igdb", GameExternalId.external_id.in_(ids))
              .options(selectinload(Game.platforms), selectinload(Game.external_ids)).all())
    owners = {int(external_id): game for game, external_id in mapped}
    appids = sorted({appid for values in steam_ids.values() for appid in values})
    steam = {}
    for start in range(0, len(appids), 500):
        steam.update((game.steam_appid, game) for game in db.query(Game)
            .filter(Game.steam_appid.in_(appids[start:start + 500]))
            .options(selectinload(Game.platforms), selectinload(Game.external_ids)))
    claimed = {game for game in steam.values() if any(identity.provider == "igdb" for identity in game.external_ids)}
    stats = {"added": 0, "refreshed": 0, "proposed": 0}
    for metadata in rows:
        igdb_id = metadata["igdb_id"]
        candidates = [steam[appid] for appid in steam_ids.get(igdb_id, []) if appid in steam]
        game = owners.get(igdb_id)
        if game is None:
            game = next((candidate for candidate in candidates if candidate not in claimed), None)
            if game is None and candidates:
                for candidate in candidates:
                    if candidate.id is None:
                        db.flush()
                    stats["proposed"] += _propose(db, candidate.id, igdb_id, "igdb_taken")
                continue
            if game is None:
                game = Game(name=metadata["name"], added_at=utcnow())
                db.add(game)
                stats["added"] += 1
            game.external_ids.append(GameExternalId(provider="igdb", external_id=str(igdb_id)))
            owners[igdb_id] = game
            claimed.add(game)
        else:
            stats["refreshed"] += 1
        for candidate in candidates:
            if candidate is not game:
                stats["proposed"] += _propose(db, candidate.id, igdb_id, "igdb_taken")
        if game.steam_appid is None:
            available = next((appid for appid in steam_ids.get(igdb_id, []) if appid not in steam), None)
            if available is not None:
                game.steam_appid = available
                steam[available] = game
        if game.steam_appid is None and game.added_at is not None:
            game.name = metadata["name"]
            game.genres = ",".join(metadata["genres"])[:500]
        apply_metadata(db, game, metadata)
    return stats


def has_disk_space(db) -> bool:
    url = db.get_bind().url
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return True
    return shutil.disk_usage(Path(url.database).resolve().parent).free >= MIN_FREE_BYTES


async def sync_igdb_catalog(ctx) -> dict:
    if not settings.igdb_enabled or not settings.igdb_catalog_sync_enabled:
        return {"status": "skipped"}
    store = ctx.get("redis")
    if store is None:
        return {"status": "skipped", "detail": "Shared provider budget is required"}
    stats = {"added": 0, "refreshed": 0, "proposed": 0, "pages": 0}
    with SessionLocal(expire_on_commit=False) as db:
        state = db.get(CatalogSyncState, 1)
        now = int(time.time())
        if state and state.window_end is None and now - state.updated_after < REFRESH_SECONDS:
            return {"status": "current"}
        if state is None:
            state = CatalogSyncState(id=1, after_id=0, updated_after=0)
            db.add(state)
        if state.window_end is None:
            # A fixed window plus a boundary overlap catches edits made during a scan.
            state.window_end = now - 120
        try:
            async with igdb.IGDBClient(store=store) as client:
                sources = await client.external_game_sources()
                source = igdb.source_id(sources, igdb.SOURCE_STEAM)
                if source is None:
                    raise igdb.IGDBError("IGDB Steam source unavailable")
                for _ in range(PAGES_PER_JOB):
                    if not has_disk_space(db):
                        db.rollback()
                        return {"status": "storage_low", **stats}
                    page = await client.catalog_page(after_id=state.after_id,
                        updated_after=max(0, state.updated_after - 1), window_end=state.window_end)
                    links = await client.steam_ids([row["igdb_id"] for row in page["games"]], source)
                    changes = upsert_page(db, page["games"], links)
                    state.after_id = page["after_id"]
                    if page["complete"]:
                        state.updated_after = state.window_end
                        state.after_id, state.window_end = 0, None
                        state.completed_at = utcnow()
                    # Games and cursor are atomic. A failed page is safe to retry.
                    db.commit()
                    for name, count in changes.items():
                        stats[name] += count
                    stats["pages"] += 1
                    await invalidate(store, CATALOG)
                    await invalidate(store, REVIEWS)
                    if page["complete"]:
                        break
        except (igdb.IGDBError, SQLAlchemyError) as error:
            db.rollback()
            logger.warning("Catalog batch deferred: %s", type(error).__name__)
            return {"status": "retry", **stats}
        logger.info("Catalog batch: %d added, %d refreshed, %d pages", stats["added"], stats["refreshed"], stats["pages"])
        return {"status": "ok", "after_id": state.after_id, "complete": state.window_end is None, **stats}


async def _main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true", help="Read catalog count and durable progress without importing")
    args = parser.parse_args()
    if args.status:
        with SessionLocal() as db:
            state = db.get(CatalogSyncState, 1)
            print(json.dumps({"games": db.query(Game).filter_by(content_kind="game").count(),
                "after_id": state.after_id if state else 0,
                "updated_through": state.updated_after if state else 0,
                "window_end": state.window_end if state else None,
                "completed_at": state.completed_at.isoformat() if state and state.completed_at else None}))
        return
    if not settings.igdb_enabled or not settings.igdb_catalog_sync_enabled:
        raise SystemExit("Enable configured IGDB and IGDB_CATALOG_SYNC_ENABLED on the worker first.")
    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url.get_secret_value()),
        job_serializer=serialize, job_deserializer=deserialize, default_queue_name=QUEUE_NAME)
    try:
        job = await pool.enqueue_job("sync_igdb_catalog", _job_id=JOB_ID)
        print(json.dumps({"queued": job is not None}))
    finally:
        await pool.aclose()


if __name__ == "__main__":
    asyncio.run(_main())
