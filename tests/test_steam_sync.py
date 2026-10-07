"""Sync pipeline against a fake Steam: pacing, retries, reuse, batching."""
import asyncio
import time

import httpx
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import worker
from app.database import Base
from app.models import Game, LinkedAccount, Platform, PlaytimeSnapshot, User
from app.services import steam

STEAM_ID = "76561198000000001"


class FakeSteam:
    """MockTransport handler that records every call and its timing."""

    def __init__(self, playtimes, latency=0.0):
        self.playtimes = dict(playtimes)
        self.latency = latency
        self.calls = []            # (kind, appid, start time)
        self.in_flight = self.max_in_flight = 0
        self.achievement_status = {}   # appid -> list of statuses to return first
        self.store_status = {}

    async def __call__(self, request):
        if request.url.host == "store.steampowered.com":
            appid = int(request.url.params["appids"])
            kind = "genres"
        elif "GetOwnedGames" in request.url.path:
            return httpx.Response(200, json={"response": {"games": [
                {"appid": a, "name": f"Game {a}", "playtime_forever": m} for a, m in self.playtimes.items()]}})
        else:
            appid = int(request.url.params["appid"])
            kind = "achievements"
        self.calls.append((kind, appid, time.monotonic()))
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.latency)
        finally:
            self.in_flight -= 1
        queued = (self.store_status if kind == "genres" else self.achievement_status).get(appid)
        if queued:
            status = queued.pop(0) if len(queued) > 1 else queued[0]
            if status != 200:
                return httpx.Response(status, headers={"retry-after": "0"})
        if kind == "genres":
            return httpx.Response(200, json={str(appid): {"success": True, "data": {"genres": [{"description": "RPG"}]}}})
        return httpx.Response(200, json={"playerstats": {"success": True, "achievements": [
            {"achieved": 1}, {"achieved": 0}]}})

    def count(self, kind):
        return sum(1 for k, _, _ in self.calls if k == kind)


@pytest.fixture
def setup(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add(User(id=1, display_name="Player"))
        db.flush()
        db.add(LinkedAccount(user_id=1, platform=Platform.steam, platform_user_id=STEAM_ID))
        db.commit()
    monkeypatch.setattr(worker, "SessionLocal", Session)
    monkeypatch.setattr(worker, "ACHIEVEMENT_INTERVAL", 0.0)
    monkeypatch.setattr(worker, "STORE_INTERVAL", 0.0)
    monkeypatch.setattr(steam, "BACKOFF_SECONDS", 0.0)
    real_client = httpx.AsyncClient

    def install(fake):
        monkeypatch.setattr(steam.httpx, "AsyncClient",
                            lambda **kwargs: real_client(transport=httpx.MockTransport(fake), **kwargs))
        return fake

    return engine, Session, install


def latest(Session):
    with Session() as db:
        rows = db.query(PlaytimeSnapshot).order_by(PlaytimeSnapshot.id).all()
        newest = {}
        for row in rows:
            newest[row.game_id] = (row.playtime_minutes, row.achievements_unlocked, row.achievements_total)
        return len(rows), newest


@pytest.mark.asyncio
async def test_unchanged_playtime_reuses_counts_and_skips_achievement_calls(setup):
    engine, Session, install = setup
    fake = install(FakeSteam({10: 120, 20: 0, 30: 45}))
    first = await worker.sync_steam_library({}, 1)
    assert first["status"] == "ok" and first["games_synced"] == 3
    # Never-played games are not asked about; every game gets a genre lookup once.
    assert fake.count("achievements") == 2 and fake.count("genres") == 3

    fake.calls.clear()
    fake.playtimes[30] = 90
    second = await worker.sync_steam_library({}, 1)
    assert fake.count("achievements") == 1 and fake.calls[0][1] == 30
    assert fake.count("genres") == 0
    assert second["achievements_reused"] == 1 and second["genres_fetched"] == 0
    rows, newest = latest(Session)
    # Every owned game still gets a row per sync: captured_at is shown as "your
    # numbers are from your Steam sync on ...", and history stays append-only.
    assert rows == 6
    with Session() as db:
        ids = {g.steam_appid: g.id for g in db.query(Game)}
    assert newest[ids[10]] == (120, 1, 2)       # reused from the first sync
    assert newest[ids[20]] == (0, None, None)
    assert newest[ids[30]] == (90, 1, 2)


@pytest.mark.asyncio
async def test_concurrent_calls_respect_the_limiter_and_concurrency_cap(setup, monkeypatch):
    engine, Session, install = setup
    fake = install(FakeSteam({a: 60 for a in range(1, 13)}, latency=0.05))
    with Session() as db:   # genres already known, so only achievement calls run
        db.add_all(Game(steam_appid=a, name=f"Game {a}", genres="RPG") for a in range(1, 13))
        db.commit()
    monkeypatch.setattr(worker, "ACHIEVEMENT_INTERVAL", 0.04)
    monkeypatch.setattr(worker, "ACHIEVEMENT_CONCURRENCY", 3)
    await worker.sync_steam_library({}, 1)
    starts = sorted(t for k, _, t in fake.calls if k == "achievements")
    assert len(starts) == 12
    # Windows' event loop may fire a timer up to one clock tick (~16ms) early,
    # so allow that per gap; the overall pace must still hold.
    assert min(b - a for a, b in zip(starts, starts[1:])) >= 0.04 - 0.017
    assert starts[-1] - starts[0] >= 11 * 0.04 - 0.017
    # Overlapping, which a serial loop never does, but never past the cap.
    assert 1 < fake.max_in_flight <= 3


@pytest.mark.asyncio
async def test_store_and_achievement_pipelines_run_side_by_side(setup, monkeypatch):
    engine, Session, install = setup
    fake = install(FakeSteam({a: 60 for a in range(1, 5)}, latency=0.05))
    monkeypatch.setattr(worker, "STORE_CONCURRENCY", 1)
    monkeypatch.setattr(worker, "ACHIEVEMENT_CONCURRENCY", 1)
    started = time.monotonic()
    await worker.sync_steam_library({}, 1)
    # 8 calls of 50ms: serial would be 400ms, two parallel pipelines about 200ms.
    assert time.monotonic() - started < 0.35
    assert fake.count("genres") == 4 and fake.count("achievements") == 4


@pytest.mark.asyncio
async def test_retries_429_and_honors_retry_after(monkeypatch):
    statuses = [429, 503, 200]
    seen = []

    async def handle(request):
        seen.append(request)
        status = statuses.pop(0)
        if status != 200:
            return httpx.Response(status, headers={"retry-after": "7"} if status == 429 else {})
        return httpx.Response(200, json={"playerstats": {"success": True, "achievements": [{"achieved": 1}]}})

    delays = []

    async def fake_sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr(steam.asyncio, "sleep", fake_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await steam.get_player_achievements(STEAM_ID, 10, client=client)
    assert result == {"unlocked": 1, "total": 1}
    assert len(seen) == 3
    assert delays == [7.0, steam.BACKOFF_SECONDS * 2]   # Retry-After, then exponential backoff


@pytest.mark.asyncio
async def test_429_defers_every_caller_sharing_the_limiter():
    limiter = steam.RateLimiter(0.0)
    statuses = [429, 200]

    async def handle(request):
        status = statuses.pop(0)
        return httpx.Response(status, headers={"retry-after": "1"}) if status == 429 else httpx.Response(400)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        started = time.monotonic()
        assert await steam.get_player_achievements(STEAM_ID, 10, client=client, limiter=limiter) is None
    assert time.monotonic() - started >= 0.95


@pytest.mark.asyncio
async def test_exhausted_retries_raise_instead_of_reporting_no_data(monkeypatch):
    monkeypatch.setattr(steam, "BACKOFF_SECONDS", 0.0)

    async def handle(request):
        return httpx.Response(429)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(steam.SteamUnavailable):
            await steam.get_app_genres(10, client=client)
        with pytest.raises(steam.SteamUnavailable):
            await steam.get_player_achievements(STEAM_ID, 10, client=client)


@pytest.mark.asyncio
async def test_throttled_lookups_leave_data_to_retry_next_sync(setup):
    engine, Session, install = setup
    fake = install(FakeSteam({10: 120, 20: 30}))
    await worker.sync_steam_library({}, 1)
    with Session() as db:
        ids = {g.steam_appid: g.id for g in db.query(Game)}
        db.query(Game).filter_by(steam_appid=20).update({"genres": None})
        db.commit()

    fake.calls.clear()
    fake.playtimes[10] = 200
    fake.achievement_status[10] = [429]
    fake.store_status[20] = [403]
    result = await worker.sync_steam_library({}, 1)
    assert result["failed_calls"] == 2
    _, newest = latest(Session)
    # The failed game keeps its previous snapshot as the latest, so the changed
    # playtime triggers the call again next sync instead of being saved blank.
    assert newest[ids[10]] == (120, 1, 2)
    with Session() as db:
        assert db.get(Game, ids[20]).genres is None   # not "", which means "asked, none"


@pytest.mark.asyncio
async def test_commits_in_batches_and_keeps_them_when_the_run_dies(setup, monkeypatch):
    engine, Session, install = setup
    monkeypatch.setattr(worker, "COMMIT_EVERY", 4)
    monkeypatch.setattr(worker, "ACHIEVEMENT_CONCURRENCY", 1)
    fake = install(FakeSteam({a: 60 for a in range(1, 11)}))
    with Session() as db:
        db.add_all(Game(steam_appid=a, name=f"Game {a}", genres="RPG") for a in range(1, 11))
        db.commit()
    commits = []
    event.listen(engine, "commit", lambda conn: commits.append(1))

    original = fake.__call__

    async def dies_on_last_game(request):
        if request.url.params.get("appid") == "10":
            raise RuntimeError("worker killed")
        return await original(request)

    install(dies_on_last_game)
    with pytest.raises(RuntimeError):
        await worker.sync_steam_library({}, 1)
    rows, _ = latest(Session)
    assert rows == 8          # two full batches of 4 survived; the partial one did not
    assert len(commits) == 2  # not one commit per game


@pytest.mark.asyncio
async def test_too_many_failures_stop_the_sync_but_keep_committed_work(setup, monkeypatch):
    engine, Session, install = setup
    monkeypatch.setattr(worker, "MAX_FAILED_CALLS", 2)
    monkeypatch.setattr(worker, "COMMIT_EVERY", 100)
    fake = install(FakeSteam({**{a: 0 for a in range(1, 4)}, **{a: 60 for a in range(10, 16)}}))
    with Session() as db:
        db.add_all(Game(steam_appid=a, name=f"Game {a}", genres="RPG") for a in [*range(1, 4), *range(10, 16)])
        db.commit()
    for appid in range(10, 16):
        fake.achievement_status[appid] = [503]
    with pytest.raises(steam.SteamUnavailable):
        await worker.sync_steam_library({}, 1)
    rows, _ = latest(Session)
    assert rows >= 3          # the unplayed games' rows were committed before giving up


@pytest.mark.asyncio
async def test_games_and_previous_snapshots_are_loaded_in_bulk(setup):
    engine, Session, install = setup
    install(FakeSteam({a: 60 for a in range(1, 41)}))
    await worker.sync_steam_library({}, 1)   # creates the games
    statements = []
    event.listen(engine, "before_cursor_execute",
                 lambda conn, cursor, sql, params, context, many: statements.append(sql))
    await worker.sync_steam_library({}, 1)
    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    game_selects = [s for s in selects if "FROM games" in s]
    snapshot_selects = [s for s in selects if "FROM playtime_snapshots" in s]
    # One query each for 40 games, not one per game.
    assert len(game_selects) == 1 and len(snapshot_selects) == 1
    assert len(selects) <= 4
