"""Explicit, repeatable public catalog bootstrap. Never creates player activity."""

import argparse
import asyncio
import json

from redis.asyncio import Redis

from app.cache import CATALOG, invalidate
from app.config import settings
from app.database import SessionLocal
from app.igdb_catalog import apply_metadata
from app.models import Game, GameExternalId
from app.services import igdb

# Editorial starting points, not a popularity ranking or anyone's library.
STARTER_GAMES = (
    (1145360, "Hades"), (1245620, "ELDEN RING"), (1086940, "Baldur's Gate 3"),
    (367520, "Hollow Knight"), (413150, "Stardew Valley"), (504230, "Celeste"),
    (620, "Portal 2"), (753640, "Outer Wilds"), (1091500, "Cyberpunk 2077"),
    (292030, "The Witcher 3: Wild Hunt"), (2379780, "Balatro"), (427520, "Factorio"),
    (588650, "Dead Cells"), (105600, "Terraria"), (275850, "No Man's Sky"),
    (892970, "Valheim"), (108600, "Project Zomboid"), (1623730, "Palworld"),
    (1966720, "Lethal Company"), (230410, "Warframe"), (730, "Counter-Strike 2"),
    (570, "Dota 2"), (440, "Team Fortress 2"), (252490, "Rust"),
    (1172470, "Apex Legends"), (238960, "Path of Exile"), (578080, "PUBG: BATTLEGROUNDS"),
    (582010, "Monster Hunter: World"), (1817070, "Marvel's Spider-Man Remastered"),
    (1593500, "God of War"), (990080, "Hogwarts Legacy"), (1174180, "Red Dead Redemption 2"),
)


async def populate(db, client) -> dict:
    sources = await client.external_game_sources()
    source = igdb.source_id(sources, igdb.SOURCE_STEAM)
    if source is None:
        raise ValueError("IGDB Steam source is unavailable")
    matches = await client.external_games(source, [appid for appid, _ in STARTER_GAMES])
    details = {row["igdb_id"]: row for row in await client.games(matches.values())}
    added = skipped = 0
    try:
        for appid, _ in STARTER_GAMES:
            metadata = details.get(matches.get(str(appid)))
            existing = db.query(Game.id).filter_by(steam_appid=appid).first()
            if metadata is None or existing:
                skipped += 1
                continue
            owner = db.query(GameExternalId.id).filter_by(
                provider="igdb", external_id=str(metadata["igdb_id"])).first()
            if owner:
                # Store conflicts need the separate reviewed merge workflow.
                skipped += 1
                continue
            game = Game(steam_appid=appid, name=metadata["name"])
            db.add(game)
            apply_metadata(db, game, metadata)
            game.external_ids.append(GameExternalId(provider="igdb", external_id=str(metadata["igdb_id"])))
            db.flush()
            added += 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"added": added, "skipped": skipped}


async def _main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write real public metadata to the configured database")
    args = parser.parse_args()
    if not args.apply:
        parser.error("Pass --apply after backing up the target database")
    if settings.demo_mode or not settings.igdb_enabled:
        parser.error("Requires a non-demo database and configured IGDB credentials")
    async with Redis.from_url(settings.redis_url.get_secret_value()) as store:
        async with igdb.IGDBClient(store=store) as client:
            with SessionLocal() as db:
                result = await populate(db, client)
        await invalidate(store, CATALOG)
    print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(_main())
