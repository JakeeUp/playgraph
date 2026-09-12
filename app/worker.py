"""arq background worker.

Run with: arq app.worker.WorkerSettings

Why arq and not Celery or FastAPI's BackgroundTasks:
- BackgroundTasks runs in the same process as the API and has no
  persistence - if the server restarts mid-sync (or the request that
  triggered it just finishes and the process later dies for any other
  reason), the job is silently gone. Not acceptable for something that
  takes minutes.
- Celery is the traditional choice but drags in a heavier config surface
  (broker/backend split, worker pools, etc.) for what is, here, a single job
  type. It's the right call at bigger scale, but is more infra than this
  project needs to prove the point.
- arq is Redis-backed (we already run Redis for caching), async-native
  (matches the rest of this FastAPI app instead of forcing sync workers),
  and is enough to get real persistence + retries with a fraction of the
  setup.
"""

import asyncio
import logging

from arq.connections import RedisSettings

from app.config import settings
from app.database import SessionLocal, engine
from app.migrations import require_current_schema
from app.models import Game, LinkedAccount, Platform, PlaytimeSnapshot, utcnow
from app.services import steam
from app.queue_codec import QUEUE_NAME, deserialize, serialize

# Steam has no officially documented per-second rate limit (just a
# 100,000 calls/day cap per API key), but community consensus is to pace
# requests around 1/sec to avoid intermittent 429s. Achievements are one
# call per game, so this is the knob that mostly determines sync duration.
SECONDS_BETWEEN_ACHIEVEMENT_CALLS = 1.0

# The store API (used for genre tags) is a different service with much
# tighter limits - roughly 200 requests per 5 minutes per IP by community
# reckoning, which works out to about 1.5s between calls. We only hit it
# for games whose genres we have never looked up, so this cost is paid
# once per game ever, not once per sync.
SECONDS_BETWEEN_STORE_CALLS = 1.5

# Log a progress line every N games. A full sync runs for minutes with no
# output otherwise, which makes a healthy job indistinguishable from a hung
# one - you end up staring at a silent terminal wondering whether to kill it.
PROGRESS_EVERY = 10

# arq configures this logger's handler, so using it means these lines show
# up in the worker's own output alongside its job start/finish lines.
logger = logging.getLogger("arq.worker")


async def sync_steam_library(ctx, user_id: int) -> dict:
    """arq task: pull a user's full Steam library + achievements and store it.

    Commits incrementally (once per game) rather than one big commit at the
    end - if this fails or gets killed partway through a 200-game sync, the
    games already processed stay saved instead of the whole run being lost.
    """
    db = SessionLocal()
    try:
        linked = (
            db.query(LinkedAccount)
            .filter_by(user_id=user_id, platform=Platform.steam)
            .first()
        )
        if linked is None:
            return {"status": "error", "detail": "no linked Steam account"}

        owned_games = await steam.get_owned_games(linked.platform_user_id)
        total = len(owned_games)

        # Rough estimate so the operator knows whether to wait or go get
        # coffee. Genre lookups only happen for unseen games, so repeat syncs
        # finish considerably faster than this first-run figure suggests.
        est_min = (total * (SECONDS_BETWEEN_ACHIEVEMENT_CALLS + SECONDS_BETWEEN_STORE_CALLS)) / 60
        logger.info(
            "sync user=%s: %d games found, roughly %.1f min if every genre is new",
            user_id, total, est_min,
        )

        games_synced = 0
        genres_fetched = 0
        for owned in owned_games:
            appid = owned["appid"]

            game = db.query(Game).filter_by(steam_appid=appid).first()
            if game is None:
                game = Game(steam_appid=appid, name=owned.get("name", f"App {appid}"))
                db.add(game)
                db.flush()  # need game.id before writing the snapshot below

            # Genre enrichment. `genres is None` means we have never asked the
            # store about this game. An empty string means we asked and it had
            # none (delisted app, tool, playtest). Keeping those two cases
            # distinct is what stops us re-requesting dead appids on every
            # future sync, by any user, forever.
            if game.genres is None:
                names = await steam.get_app_genres(appid)
                game.genres = ",".join(names) if names else ""
                genres_fetched += 1
                await asyncio.sleep(SECONDS_BETWEEN_STORE_CALLS)

            # Skip the achievements call for games that have never been
            # played. A game with 0 minutes has nothing unlocked, so the
            # request can only ever come back empty. This is the single
            # biggest win available here: most Steam libraries are mostly
            # unplayed, so this typically removes well over half the HTTP
            # calls, and each skipped call also skips its 1s of pacing.
            playtime = owned.get("playtime_forever", 0)
            if playtime > 0:
                achievements = await steam.get_player_achievements(
                    linked.platform_user_id, appid
                )
                await asyncio.sleep(SECONDS_BETWEEN_ACHIEVEMENT_CALLS)
            else:
                achievements = None

            db.add(
                PlaytimeSnapshot(
                    user_id=user_id,
                    game_id=game.id,
                    playtime_minutes=playtime,
                    achievements_unlocked=achievements["unlocked"] if achievements else None,
                    achievements_total=achievements["total"] if achievements else None,
                )
            )
            db.commit()
            games_synced += 1

            if games_synced % PROGRESS_EVERY == 0 or games_synced == total:
                logger.info(
                    "sync user=%s: %d/%d games (%d genre lookups so far), latest=%s",
                    user_id, games_synced, total, genres_fetched, game.name,
                )

        linked.last_synced_at = utcnow()
        db.commit()

        logger.info(
            "sync user=%s complete: %d games, %d genre lookups",
            user_id, games_synced, genres_fetched,
        )

        return {
            "status": "ok",
            "games_synced": games_synced,
            "genres_fetched": genres_fetched,
        }
    finally:
        db.close()


async def check_database_schema(ctx):
    require_current_schema(engine)


class WorkerSettings:
    on_startup = check_database_schema
    functions = [sync_steam_library]
    queue_name = QUEUE_NAME
    job_serializer = staticmethod(serialize)
    job_deserializer = staticmethod(deserialize)
    redis_settings = RedisSettings.from_dsn(settings.redis_url)

    # Run one job at a time. arq defaults to 10 concurrent jobs, which for
    # this workload is actively wrong: every sync writes the same tables, so
    # concurrency buys nothing (the work is rate-limit bound on Steam's side,
    # not CPU bound) and costs write contention. On SQLite that surfaces
    # immediately as "database is locked"; on Postgres it would quietly waste
    # Steam API quota instead. The per-user job id already stops one user
    # queueing two syncs - this covers the rest, including retried jobs that
    # were enqueued before that guard existed.
    max_jobs = 1

    # arq's default job_timeout is 300 seconds, which silently kills any sync
    # of a decent-sized library partway through - the job dies mid-run with a
    # TimeoutError and the library is left half populated. A first sync is
    # rate-limit bound and legitimately takes many minutes, so the timeout has
    # to be sized to the real work rather than to a default that assumes short
    # jobs. An hour is far more than a sync should ever need, which is the
    # point: it should only ever fire if something is genuinely wedged.
    job_timeout = 3600

    # How long a finished job's result stays readable by
    # GET /me/sync/status/{job_id}. It also sets how long the per-user job id
    # stays taken, so it doubles as the minimum gap between syncs for one
    # account. Five minutes is long enough to poll a result comfortably and
    # short enough that a real re-sync is not blocked for ages.
    keep_result = 300
