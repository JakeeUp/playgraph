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

import httpx

from arq import Retry
from arq.connections import RedisSettings
from sqlalchemy import func

from app.cache import CATALOG, REVIEWS, invalidate
from app.config import settings
from app.database import SessionLocal, engine
from app.migrations import require_current_schema
from app import psn_status
from app.models import (Game, GameExternalId, LinkedAccount, Platform, PlaytimeSnapshot, SnapshotSource,
                        utcnow)
from app.services import psn, steam
from app.queue_codec import QUEUE_NAME, deserialize, serialize
from app.schemas import display_name

# Steam has no officially documented per-second rate limit (just a
# 100,000 calls/day cap per API key), but community consensus is to pace
# requests around 1/sec to avoid intermittent 429s. That advice was followed
# by a serial loop that also waited out every response first, so its real
# pace was nearer one call per 1.2s. This is the gap between call *starts*:
# about twice the old pace, a modest step, with steam.py's 429 handling
# slowing every in-flight call at once if Steam objects. If 429s show up in
# the worker log, raise it back toward 1.0.
ACHIEVEMENT_INTERVAL = 0.5
# A few calls in flight so one slow response does not stall the pace. The
# interval above, not this number, decides the request rate.
ACHIEVEMENT_CONCURRENCY = 4

# The store API (used for genre tags) is a different service with much
# tighter limits - roughly 200 requests per 5 minutes per IP by community
# reckoning, which is 1.5s between calls. The old loop got a safety margin for
# free by waiting on each response before its 1.5s sleep; spacing call starts
# directly removes that, so the margin is put back here explicitly. We only
# hit this for games whose genres we have never looked up, so the cost is
# paid once per game ever, not once per sync. It runs alongside the
# achievement calls, and on a first sync it is usually the slower of the two.
STORE_INTERVAL = 1.6
STORE_CONCURRENCY = 2

# Commit every N writes instead of every game. A crash loses at most this many
# games' work, and a rerun skips the achievement call for anything whose
# playtime already matches what was saved, so the committed part costs almost
# nothing to pass over again.
COMMIT_EVERY = 25

# Give up once this many calls have failed outright (every retry spent). By
# then Steam is down or has blocked us, and carrying on just burns minutes of
# backoff per game. Committed batches survive for the next run.
MAX_FAILED_CALLS = 10

# Keeps IN (...) lists under SQLite's bound-parameter cap on huge libraries.
IN_CHUNK = 500

# Log a progress line every N games. A full sync runs for minutes with no
# output otherwise, which makes a healthy job indistinguishable from a hung
# one - you end up staring at a silent terminal wondering whether to kill it.
PROGRESS_EVERY = 10

# arq configures this logger's handler, so using it means these lines show
# up in the worker's own output alongside its job start/finish lines.
logger = logging.getLogger("arq.worker")

_FAILED = object()


def _games_by_appid(db, owned_games: list[dict]) -> dict[int, Game]:
    """Every owned game's row keyed by appid, creating the missing ones. A few
    queries for the whole library instead of one SELECT per game."""
    appids = list({owned["appid"] for owned in owned_games})
    games: dict[int, Game] = {}
    for start in range(0, len(appids), IN_CHUNK):
        chunk = appids[start:start + IN_CHUNK]
        games.update((g.steam_appid, g) for g in db.query(Game).filter(Game.steam_appid.in_(chunk)))
    for owned in owned_games:
        appid = owned["appid"]
        if appid not in games:
            games[appid] = Game(steam_appid=appid, name=owned.get("name", f"App {appid}"))
            db.add(games[appid])
    db.flush()  # one round trip assigns every new game.id the snapshots need
    return games


def _previous_snapshots(db, user_id: int) -> dict[int, tuple]:
    """game_id -> (playtime, unlocked, total) from the user's newest Steam snapshot.

    One windowed query, served by ix_playtime_snapshots_user_game_captured.
    Only Steam's own rows count: a PlayStation snapshot must never read as a
    Steam playtime change, or mask one.
    """
    ranked = (
        db.query(
            PlaytimeSnapshot.game_id,
            PlaytimeSnapshot.playtime_minutes,
            PlaytimeSnapshot.achievements_unlocked,
            PlaytimeSnapshot.achievements_total,
            func.row_number().over(
                partition_by=PlaytimeSnapshot.game_id,
                order_by=(PlaytimeSnapshot.captured_at.desc(), PlaytimeSnapshot.id.desc()),
            ).label("position"),
        )
        .filter(PlaytimeSnapshot.user_id == user_id,
                PlaytimeSnapshot.source == SnapshotSource.steam.value)
        .subquery()
    )
    return {
        row.game_id: (row.playtime_minutes, row.achievements_unlocked, row.achievements_total)
        for row in db.query(ranked).filter(ranked.c.position == 1)
    }


async def sync_steam_library(ctx, user_id: int) -> dict:
    """arq task: pull a user's full Steam library + achievements and store it.

    Commits in small batches rather than one big commit at the end - if this
    fails or gets killed partway through a 200-game sync, the games already
    processed stay saved instead of the whole run being lost.
    """
    # Batches commit mid-run. With the default expire_on_commit, every Game
    # row touched after a commit would reload itself with its own SELECT.
    db = SessionLocal(expire_on_commit=False)
    try:
        linked = (
            db.query(LinkedAccount)
            .filter_by(user_id=user_id, platform=Platform.steam)
            .first()
        )
        if linked is None:
            return {"status": "error", "detail": "no linked Steam account"}

        async with steam.make_client() as client:
            owned_games = await steam.get_owned_games(linked.platform_user_id, client=client)
            stats = await _sync_owned_games(db, user_id, linked.platform_user_id, owned_games, client,
                                            linked_account_id=linked.id)

        linked.last_synced_at = utcnow()
        db.commit()
        # New games and freshly looked-up genres change the public catalog, and
        # the feed shows game data too. Retire both caches once the rows are in.
        await invalidate(ctx.get("redis"), CATALOG)
        await invalidate(ctx.get("redis"), REVIEWS)

        logger.info(
            "sync user=%s complete: %d games, %d achievement calls, %d genre lookups, %d failed calls",
            user_id, stats["games_synced"], stats["achievement_calls"],
            stats["genres_fetched"], stats["failed_calls"],
        )
        return {"status": "ok", **stats}
    finally:
        db.close()


async def _sync_owned_games(db, user_id, steam_id, owned_games, client, linked_account_id=None) -> dict:
    games = _games_by_appid(db, owned_games)
    previous = _previous_snapshots(db, user_id)
    total = len(owned_games)

    ready, to_fetch = [], []
    for owned in owned_games:
        game = games[owned["appid"]]
        playtime = owned.get("playtime_forever", 0)
        last = previous.get(game.id)
        if playtime == 0:
            # Skip the achievements call for games that have never been
            # played. A game with 0 minutes has nothing unlocked, so the
            # request can only ever come back empty. Most Steam libraries are
            # mostly unplayed, so this removes well over half the calls.
            ready.append((game, 0, None, None))
        elif last is not None and last[0] == playtime:
            # Achievements unlock while playing, and playing moves playtime.
            # Unchanged minutes therefore mean unchanged counts, so reuse them.
            # This turns a repeat sync from one call per played game into one
            # per game actually played since. (A game that later adds
            # achievements shows the new total once it is next played.)
            ready.append((game, playtime, last[1], last[2]))
        else:
            to_fetch.append((game, playtime))
    # Genre enrichment. `genres is None` means we have never asked the store
    # about this game. An empty string means we asked and it had none
    # (delisted app, tool, playtest). Keeping those two cases distinct is
    # what stops us re-requesting dead appids on every future sync, by any
    # user, forever.
    need_genres = [game for game in games.values() if game.genres is None]
    unchanged = sum(1 for row in ready if row[1] > 0)

    logger.info(
        "sync user=%s: %d games, %d achievement calls (%d unchanged), %d genre lookups, roughly %.1f min",
        user_id, total, len(to_fetch), unchanged, len(need_genres),
        max(len(to_fetch) * ACHIEVEMENT_INTERVAL, len(need_genres) * STORE_INTERVAL) / 60,
    )

    stats = {"games_synced": 0, "genres_fetched": 0, "achievement_calls": len(to_fetch),
             "achievements_reused": unchanged, "failed_calls": 0}
    pending = 0

    def wrote() -> None:
        nonlocal pending
        pending += 1
        if pending >= COMMIT_EVERY:
            db.commit()
            pending = 0

    def save(game, playtime, unlocked, achievements_total) -> None:
        # Unchanged games still get a row. Snapshots are the append-only
        # history, and the game page tells the user their numbers come "from
        # your Steam sync on <captured_at>", which has to be this sync. The
        # rows are cheap; the API calls they used to cost are what we skip.
        db.add(PlaytimeSnapshot(
            user_id=user_id, game_id=game.id, playtime_minutes=playtime,
            achievements_unlocked=unlocked, achievements_total=achievements_total,
            source=SnapshotSource.steam.value, linked_account_id=linked_account_id,
        ))
        stats["games_synced"] += 1
        wrote()
        if stats["games_synced"] % PROGRESS_EVERY == 0 or stats["games_synced"] == total:
            logger.info(
                "sync user=%s: %d/%d games (%d genre lookups so far), latest=%s",
                user_id, stats["games_synced"], total, stats["genres_fetched"], game.name,
            )

    achievement_pace = steam.RateLimiter(ACHIEVEMENT_INTERVAL)
    store_pace = steam.RateLimiter(STORE_INTERVAL)
    achievement_slots = asyncio.Semaphore(ACHIEVEMENT_CONCURRENCY)
    store_slots = asyncio.Semaphore(STORE_CONCURRENCY)

    async def fetch_achievements(game, playtime):
        async with achievement_slots:
            try:
                result = await steam.get_player_achievements(
                    steam_id, game.steam_appid, client=client, limiter=achievement_pace)
            except steam.SteamUnavailable:
                result = _FAILED
        return "achievements", game, playtime, result

    async def fetch_genres(game):
        async with store_slots:
            try:
                result = await steam.get_app_genres(game.steam_appid, client=client, limiter=store_pace)
            except steam.SteamUnavailable:
                result = _FAILED
        return "genres", game, None, result

    for row in ready:
        save(*row)

    # The two APIs have separate limits, so they run as separate pipelines at
    # the same time. Only HTTP happens inside the tasks; every database write
    # stays here, in one coroutine, because a Session is not safe to share.
    tasks = [asyncio.create_task(fetch_achievements(g, p)) for g, p in to_fetch]
    tasks += [asyncio.create_task(fetch_genres(g)) for g in need_genres]
    try:
        for finished in asyncio.as_completed(tasks):
            kind, game, playtime, result = await finished
            if result is _FAILED:
                stats["failed_calls"] += 1
                if stats["failed_calls"] > MAX_FAILED_CALLS:
                    db.commit()
                    raise steam.SteamUnavailable("too many Steam calls failed; stopping this sync")
                # A failed genre lookup stays None and is retried next sync. A
                # failed achievement call keeps the previous snapshot as the
                # latest, so the playtime still differs next sync and the call
                # is retried; a game with no snapshot yet is saved without
                # counts so it still shows up in the library.
                if kind == "achievements" and game.id not in previous:
                    save(game, playtime, None, None)
            elif kind == "genres":
                game.genres = ",".join(result) if result else ""
                stats["genres_fetched"] += 1
                wrote()
            else:
                save(game, playtime, result["unlocked"] if result else None,
                     result["total"] if result else None)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    db.commit()
    return stats

# PlayStation ---------------------------------------------------------------
#
# One server-owned NPSSO reads each linked player's public trophy list and,
# where their privacy settings allow it, PS4/PS5 play time. See
# docs/PSN_INTEGRATION.md. PSNClient spends a per-process request budget, so a
# sync is a handful of paged calls plus one lookup per five new games.

PSN_CONCEPT, PSN_TITLE, PSN_TROPHY = "psn_concept", "psn_title", "psn_trophy"
MAX_NAME = 200


class PSNOperatorError(RuntimeError):
    """The server NPSSO was rejected. Raised to fail the job; carries no token."""


class _PsnIds:
    """game_external_ids lookups for one sync: bulk loaded, then extended in memory."""

    def __init__(self, db):
        self.db = db
        self.known: dict[str, dict[str, Game]] = {PSN_CONCEPT: {}, PSN_TITLE: {}, PSN_TROPHY: {}}

    def load(self, provider: str, ids) -> None:
        wanted = list({str(i) for i in ids if i} - set(self.known[provider]))
        for start in range(0, len(wanted), IN_CHUNK):
            rows = (self.db.query(GameExternalId.external_id, Game)
                    .join(Game, Game.id == GameExternalId.game_id)
                    .filter(GameExternalId.provider == provider,
                            GameExternalId.external_id.in_(wanted[start:start + IN_CHUNK])))
            self.known[provider].update((external_id, game) for external_id, game in rows)

    def game(self, provider: str, external_id) -> Game | None:
        return self.known[provider].get(str(external_id)) if external_id else None

    def attach(self, game: Game, provider: str, external_id) -> None:
        # The first Game to claim a store ID keeps it; unique (provider,
        # external_id) in the database backs this up.
        if not external_id or str(external_id) in self.known[provider]:
            return
        # A saved game is linked by id, so its external_ids collection is never
        # lazy loaded; a game created in this sync is linked by object.
        link = {"game_id": game.id} if game.id is not None else {"game": game}
        self.db.add(GameExternalId(provider=provider, external_id=str(external_id), **link))
        self.known[provider][str(external_id)] = game


def _group_played(played: list[dict]) -> list[dict]:
    """One entry per concept. A game's PS4 and PS5 versions are separate
    gamelist rows under one concept; both are PlayStation time, so they add."""
    groups: dict[str, dict] = {}
    for title in played:
        if title.get("concept_id"):
            key = f"concept:{title['concept_id']}"
        elif title.get("title_id"):
            key = f"title:{title['title_id']}"
        else:
            continue
        group = groups.setdefault(key, {"concept_id": title.get("concept_id"), "name": title.get("name"),
                                        "image_url": title.get("image_url"), "title_ids": set(),
                                        "played_title_ids": [], "minutes": None})
        if title.get("title_id"):
            group["title_ids"].add(title["title_id"])
            group["played_title_ids"].append(title["title_id"])
        group["title_ids"].update(t for t in title.get("concept_title_ids") or [] if isinstance(t, str))
        if title.get("play_minutes") is not None:
            group["minutes"] = (group["minutes"] or 0) + title["play_minutes"]
    return list(groups.values())


def _blank_psn_entry() -> dict:
    return {"minutes": None, "earned": None, "defined": None, "progress": None, "lists": 0}


async def _sync_psn_titles(db, linked: LinkedAccount, client: psn.PSNClient) -> dict:
    account_id = linked.platform_user_id
    try:
        profile = await client.get_profile(account_id)
        if profile.get("online_id"):
            linked.display_handle = display_name(profile["online_id"], linked.display_handle or "", limit=32)
    except psn.PSNPrivateError:
        pass  # the handle stays as last seen
    try:
        trophy_titles = await client.get_trophy_titles(account_id)
        trophies_visible = True
    except psn.PSNPrivateError:
        trophy_titles, trophies_visible = [], False
    played = await client.get_played_titles(account_id)
    playtime_visible = played is not None
    groups = _group_played(played or [])

    ids = _PsnIds(db)
    ids.load(PSN_CONCEPT, [g["concept_id"] for g in groups])
    ids.load(PSN_TITLE, [t for g in groups for t in g["title_ids"]])
    ids.load(PSN_TROPHY, [t["np_communication_id"] for t in trophy_titles])

    # Keyed by id(game), because games created in this sync have no row id yet.
    entries: dict[int, tuple[Game, dict]] = {}

    def entry_for(game: Game) -> dict:
        return entries.setdefault(id(game), (game, _blank_psn_entry()))[1]

    created = 0
    played_games: list[tuple[Game, dict]] = []
    for group in groups:
        game = ids.game(PSN_CONCEPT, group["concept_id"]) or next(
            filter(None, (ids.game(PSN_TITLE, t) for t in sorted(group["title_ids"]))), None)
        if game is None:
            first = group["played_title_ids"][0] if group["played_title_ids"] else group["concept_id"]
            game = Game(name=display_name(group["name"], f"PlayStation title {first}", limit=MAX_NAME),
                        header_image_url=group["image_url"])
            db.add(game)
            created += 1
        ids.attach(game, PSN_CONCEPT, group["concept_id"])
        for title_id in sorted(group["title_ids"]):
            ids.attach(game, PSN_TITLE, title_id)
        entry = entry_for(game)
        if group["minutes"] is not None:
            entry["minutes"] = (entry["minutes"] or 0) + group["minutes"]
        played_games.append((game, group))

    # Trophy lists carry no title ID. Ask Sony which lists belong to the
    # played titles, but only while some of this account's lists are still
    # unmapped, and only for games that have no list yet. Mappings are stored,
    # so a repeat sync makes no lookups unless a new game appears.
    lookups = 0
    unmapped = [t for t in trophy_titles if ids.game(PSN_TROPHY, t["np_communication_id"]) is None]
    if unmapped and played_games:
        with_lists = {id(game) for game in ids.known[PSN_TROPHY].values()}
        candidates = [t for game, group in played_games if id(game) not in with_lists
                      for t in group["played_title_ids"]]
        if candidates:
            lookups = -(-len(set(candidates)) // psn.TITLE_LOOKUP_BATCH)
            try:
                found = await client.get_trophy_lists_for_titles(account_id, candidates)
            except psn.PSNPrivateError:
                found = {}
            for title_id, lists in found.items():
                game = ids.game(PSN_TITLE, title_id)
                if game is None:
                    continue
                for np_id in lists:
                    ids.attach(game, PSN_TROPHY, np_id)

    for title in trophy_titles:
        np_id = title.get("np_communication_id")
        if not np_id:
            continue
        game = ids.game(PSN_TROPHY, np_id)
        if game is None:
            # PS3/Vita lists, or PS4/PS5 lists whose game's play time is hidden:
            # a Game keyed by the trophy list alone. Never matched by name.
            game = Game(name=display_name(title.get("name"), f"PlayStation trophies {np_id}", limit=MAX_NAME),
                        header_image_url=title.get("icon_url"))
            db.add(game)
            created += 1
            ids.attach(game, PSN_TROPHY, np_id)
        entry = entry_for(game)
        entry["lists"] += 1
        for side in ("earned", "defined"):
            counts = entry[side] or dict.fromkeys(psn.TROPHY_GRADES, 0)
            entry[side] = {grade: counts[grade] + title[side][grade] for grade in psn.TROPHY_GRADES}
        # Sony's percentage weighs grades by points. With two lists (PS4 and
        # PS5) there is no honest single figure, so leave it to the counts.
        entry["progress"] = title.get("progress") if entry["lists"] == 1 else None

    db.flush()  # one round trip gives every new game and mapping its id
    pending = 0
    for game, entry in entries.values():
        earned, defined = entry["earned"], entry["defined"]
        db.add(PlaytimeSnapshot(
            user_id=linked.user_id, game_id=game.id, linked_account_id=linked.id,
            source=SnapshotSource.psn.value, playtime_minutes=entry["minutes"],
            achievements_unlocked=sum(earned.values()) if earned else None,
            achievements_total=sum(defined.values()) if defined else None,
            trophy_progress=entry["progress"],
            **{f"trophies_{grade}": earned[grade] if earned else None for grade in psn.TROPHY_GRADES},
            **{f"trophies_{grade}_total": defined[grade] if defined else None for grade in psn.TROPHY_GRADES},
        ))
        pending += 1
        if pending >= COMMIT_EVERY:
            db.commit()
            pending = 0
    db.commit()
    return {"games_synced": len(entries), "games_created": created, "trophy_lists": len(trophy_titles),
            "played_titles": len(played or []), "mapping_lookups": lookups,
            "trophies_visible": trophies_visible, "playtime_visible": playtime_visible}


def _psn_http_client() -> httpx.AsyncClient:
    """One connection pool for every Sony call in a sync. PSNClient never closes
    a client it was handed, so the sync owns and closes it."""
    return httpx.AsyncClient(timeout=15, limits=httpx.Limits(max_connections=4, max_keepalive_connections=4))


async def sync_psn_library(ctx, user_id: int) -> dict:
    """arq task: import a linked PlayStation account's public trophies and play time."""
    store = ctx.get("redis")
    if not settings.psn_enabled:
        return {"status": "error", "detail": "PlayStation is not enabled"}
    if await psn_status.auth_blocked(store):
        logger.error("psn sync user=%s not started: Sony rejected PSN_NPSSO earlier. %s",
                     user_id, psn_status.OPERATOR_HINT)
        raise PSNOperatorError("PSN sign-in for the server account is failing; the operator must set PSN_NPSSO")
    db = SessionLocal(expire_on_commit=False)
    try:
        linked = db.query(LinkedAccount).filter_by(user_id=user_id, platform=Platform.psn).first()
        if linked is None:
            return {"status": "error", "detail": "no linked PlayStation account"}
        try:
            async with _psn_http_client() as http:
                async with psn.PSNClient(client=http) as client:
                    stats = await _sync_psn_titles(db, linked, client)
        except psn.PSNAuthError:
            db.rollback()
            await psn_status.block_auth(store)
            # Operator-facing and secret-free: PSN exception texts never hold a token.
            logger.error("psn sync user=%s failed: Sony rejected the server PSN_NPSSO. %s",
                         user_id, psn_status.OPERATOR_HINT)
            raise PSNOperatorError("PSN sign-in for the server account failed; the operator must set PSN_NPSSO") from None
        except psn.PSNRateLimitedError as exc:
            db.rollback()
            defer = max(5, int(exc.retry_after) + 1)  # Sony's Retry-After, or the client's default pause
            logger.warning("psn sync user=%s: Sony rate limited us; retrying in %ss", user_id, defer)
            raise Retry(defer=defer) from None
        except psn.PSNNotFoundError:
            db.rollback()
            await psn_status.write_status(store, user_id, {"problem": "account_not_found",
                                                           "checked_at": utcnow().isoformat()})
            logger.warning("psn sync user=%s: the linked PSN account was not found", user_id)
            return {"status": "error", "detail": "PlayStation account not found"}

        linked.last_synced_at = utcnow()
        db.commit()
        await psn_status.write_status(store, user_id, {
            "problem": None, "trophies_visible": stats["trophies_visible"],
            "playtime_visible": stats["playtime_visible"], "checked_at": utcnow().isoformat()})
        await invalidate(store, CATALOG)
        await invalidate(store, REVIEWS)
        logger.info("psn sync user=%s complete: %d games (%d new), %d trophy lists, %d played titles, "
                    "%d mapping lookups, trophies %s, play time %s", user_id, stats["games_synced"],
                    stats["games_created"], stats["trophy_lists"], stats["played_titles"],
                    stats["mapping_lookups"], "visible" if stats["trophies_visible"] else "hidden",
                    "visible" if stats["playtime_visible"] else "hidden")
        return {"status": "ok", **stats}
    finally:
        db.close()


async def check_database_schema(ctx):
    require_current_schema(engine)


class WorkerSettings:
    on_startup = check_database_schema
    functions = [sync_steam_library, sync_psn_library]
    queue_name = QUEUE_NAME
    job_serializer = staticmethod(serialize)
    job_deserializer = staticmethod(deserialize)
    redis_settings = RedisSettings.from_dsn(settings.redis_url.get_secret_value())

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
