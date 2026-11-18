import asyncio
import runpy
from copy import deepcopy

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from app import catalog_sync as sync
from app.config import settings
from app.models import CatalogSyncState, Game, GameExternalId, IgdbMatchCandidate, PlaytimeSnapshot, Review, User, utcnow
from app.services import igdb
from tests.security_helpers import MemoryRedis
from tests.test_igdb import RAW_GAME, FakeIGDB, make_client


def metadata(key, name=None):
    raw = {**deepcopy(RAW_GAME), "id": key, "name": name or f"Game {key}"}
    return igdb.normalize_game(raw)


class Provider:
    def __init__(self):
        self.rows = [metadata(key) for key in range(1, 6)]
        self.changed = {key: 100 for key in range(1, 6)}
        self.links = {}
        self.calls = []
        self.fail = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def external_game_sources(self):
        return {"steam": 1}

    async def catalog_page(self, **params):
        self.calls.append(params)
        if self.fail:
            raise igdb.IGDBError("Unavailable")
        rows = [row for row in self.rows if row["igdb_id"] > params["after_id"]
                and params["updated_after"] <= self.changed[row["igdb_id"]] <= params["window_end"]]
        selected = rows[:2]
        return {"games": selected, "after_id": selected[-1]["igdb_id"] if selected else params["after_id"],
                "complete": len(selected) < 2}

    async def steam_ids(self, ids, source):
        return {key: self.links[key] for key in ids if key in self.links}


@pytest.fixture
def setup(db, monkeypatch):
    provider, store, clock = Provider(), MemoryRedis(), [100000]
    monkeypatch.setattr(settings, "igdb_client_id", "test-client")
    monkeypatch.setattr(settings, "igdb_client_secret", SecretStr("test-secret"))
    monkeypatch.setattr(settings, "igdb_catalog_sync_enabled", True)
    monkeypatch.setattr(sync, "SessionLocal", lambda **kwargs: db)
    monkeypatch.setattr(sync.igdb, "IGDBClient", lambda **kwargs: provider)
    monkeypatch.setattr(sync.time, "time", lambda: clock[0])
    monkeypatch.setattr(sync, "PAGES_PER_JOB", 1)
    return provider, store, clock


def run(store):
    return asyncio.run(sync.sync_igdb_catalog({"redis": store}))


def test_resumes_durable_pages_without_redis_checkpoint_or_fake_activity(db, setup):
    provider, store, _ = setup
    assert run(store)["added"] == 2
    state = db.get(CatalogSyncState, 1)
    assert state.after_id == 2 and state.window_end == 99880
    store = MemoryRedis()
    assert run(store)["added"] == 2
    result = run(store)
    assert result["added"] == 1 and result["complete"]
    assert db.query(Game).count() == 5
    assert db.query(GameExternalId).count() == 5
    assert all(game.catalog_source == "igdb" for game in db.query(Game))
    assert all(db.query(model).count() == 0 for model in (User, Review, PlaytimeSnapshot))
    assert run(store) == {"status": "current"}
    assert len(provider.calls) == 3
    assert store.read("playgraph:cache:gen:catalog") == b"2"


def test_provider_failure_preserves_last_committed_cursor(db, setup):
    provider, store, _ = setup
    run(store)
    provider.fail = True
    assert run(store)["status"] == "retry"
    assert db.get(CatalogSyncState, 1).after_id == 2
    assert db.query(Game).count() == 2
    provider.fail = False
    assert run(store)["added"] == 2
    assert db.get(CatalogSyncState, 1).after_id == 4


def test_worker_yields_after_its_page_budget(db, setup, monkeypatch):
    provider, store, _ = setup
    provider.rows = [metadata(key) for key in range(1, 21)]
    provider.changed = {key: 100 for key in range(1, 21)}
    monkeypatch.setattr(sync, "PAGES_PER_JOB", 3)
    result = run(store)
    assert result["pages"] == 3 and result["added"] == 6 and not result["complete"]
    assert db.get(CatalogSyncState, 1).after_id == 6
    assert len(provider.calls) == 3


def test_catalog_schedule_coalesces_while_the_single_worker_is_busy(setup):
    from app import worker
    namespace = runpy.run_path(worker.__file__)
    scheduled = [job for job in namespace["WorkerSettings"].cron_jobs if job.job_id == sync.JOB_ID]
    assert len(scheduled) == 1
    assert scheduled[0].keep_result_s == 0
    assert scheduled[0].minute == set(range(60))


def test_existing_metadata_is_loaded_in_bounded_queries_not_one_query_per_game(db):
    rows = [metadata(key) for key in range(1, 501)]
    for row in rows:
        game = Game(name=row["name"], added_at=utcnow())
        game.external_ids.append(GameExternalId(provider="igdb", external_id=str(row["igdb_id"])))
        db.add(game)
    db.commit()
    db.expunge_all()
    queries = []
    def count(connection, cursor, statement, *args):
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)
    event.listen(db.get_bind(), "before_cursor_execute", count)
    try:
        result = sync.upsert_page(db, rows, {})
        db.commit()
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", count)
    assert result["refreshed"] == 500
    assert len(queries) <= 4


def test_legacy_refresh_batches_large_catalog_and_caps_automatic_work(db):
    from app import igdb_catalog
    rows = [metadata(key) for key in range(1, 1101)]
    for row in rows:
        game = Game(name=row["name"])
        game.external_ids.append(GameExternalId(provider="igdb", external_id=str(row["igdb_id"])))
        db.add(game)
    db.commit()
    class MetadataProvider:
        sizes = []
        async def games(self, ids):
            self.sizes.append(len(ids))
            return [metadata(key) for key in ids]
    provider = MetadataProvider()
    assert asyncio.run(igdb_catalog.refresh_metadata(db, provider)) == 500
    assert provider.sizes == [500]
    provider.sizes.clear()
    assert asyncio.run(igdb_catalog.refresh_metadata(db, provider, force=True)) == 1100
    assert provider.sizes == [500, 500, 100]


def test_failed_page_rolls_back_metadata_and_cursor_together(db, setup, monkeypatch):
    _, store, _ = setup
    run(store)
    original = sync.upsert_page
    def failure(db, rows, links):
        original(db, rows, links)
        db.flush()
        raise IntegrityError("synthetic concurrent import", {}, None)
    monkeypatch.setattr(sync, "upsert_page", failure)
    assert run(store)["status"] == "retry"
    assert db.get(CatalogSyncState, 1).after_id == 2
    assert db.query(Game).count() == 2
    monkeypatch.setattr(sync, "upsert_page", original)
    assert run(store)["added"] == 2


def test_fixed_windows_and_incremental_refresh_catch_edits_and_new_games(db, setup):
    provider, store, clock = setup
    run(store)
    provider.rows[0]["name"] = "Renamed"
    provider.changed[1] = 100001
    run(store)
    run(store)
    assert db.query(Game).join(GameExternalId).filter(GameExternalId.external_id == "1").one().name == "Game 1"
    provider.rows.append(metadata(6))
    provider.changed[6] = 100002
    clock[0] += sync.REFRESH_SECONDS + 200
    result = run(store)
    assert result["added"] == 1 and not result["complete"]
    assert provider.calls[-1]["updated_after"] == 99879
    result = run(store)
    assert result["complete"]
    assert db.query(Game).count() == 6
    assert db.query(Game).join(GameExternalId).filter(GameExternalId.external_id == "1").one().name == "Renamed"


def test_exact_steam_identity_reuses_existing_row_and_preserves_player_data(db, setup):
    provider, store, _ = setup
    db.add(User(id=1, display_name="Player"))
    db.add(Game(id=42, steam_appid=620, name="Steam title", genres="Store genre"))
    db.flush()
    db.add_all([Review(user_id=1, game_id=42, rating=4, verified_playtime_minutes=600),
                PlaytimeSnapshot(user_id=1, game_id=42, playtime_minutes=600)])
    db.commit()
    provider.rows = [metadata(1)]
    provider.links = {1: [620]}
    assert run(store)["added"] == 0
    game = db.get(Game, 42)
    assert db.query(Game).count() == 1
    assert game.name == "Steam title" and game.genres == "Store genre"
    assert game.summary == "Geralt hunts."
    assert game.external_ids[0].external_id == "1"
    assert db.query(Review).one().verified_playtime_minutes == 600
    assert db.query(PlaytimeSnapshot).one().playtime_minutes == 600


def test_existing_store_conflicts_are_proposed_not_merged(db, setup):
    provider, store, _ = setup
    existing = Game(id=40, name="IGDB owner", added_at=utcnow())
    existing.external_ids.append(GameExternalId(provider="igdb", external_id="1"))
    db.add_all([existing, Game(id=42, steam_appid=620, name="Steam copy")])
    db.commit()
    provider.rows = [metadata(1)]
    provider.links = {1: [620]}
    assert run(store)["proposed"] == 1
    assert db.query(Game).count() == 2
    assert db.get(Game, 40).steam_appid is None
    assert db.get(Game, 42).name == "Steam copy"
    assert db.query(IgdbMatchCandidate).one().game_id == 42


def test_ambiguous_steam_id_does_not_join_two_igdb_games(db, setup):
    provider, store, _ = setup
    provider.rows = [metadata(1), metadata(2)]
    provider.links = {1: [620], 2: [620]}
    result = run(store)
    assert result["added"] == 1 and result["proposed"] == 1
    assert db.query(GameExternalId).count() == 1
    assert db.query(IgdbMatchCandidate).count() == 1


def test_disabled_demo_missing_budget_and_low_storage_never_import(db, setup, monkeypatch):
    provider, store, _ = setup
    monkeypatch.setattr(settings, "igdb_catalog_sync_enabled", False)
    assert run(store)["status"] == "skipped"
    monkeypatch.setattr(settings, "igdb_catalog_sync_enabled", True)
    monkeypatch.setattr(settings, "demo_mode", True)
    assert run(store)["status"] == "skipped"
    monkeypatch.setattr(settings, "demo_mode", False)
    assert run(None)["status"] == "skipped"
    monkeypatch.setattr(sync, "has_disk_space", lambda db: False)
    assert run(store)["status"] == "storage_low"
    assert provider.calls == [] and db.query(Game).count() == 0
    assert db.query(CatalogSyncState).count() == 0


def test_catalog_api_pages_are_filtered_bounded_and_validate_progress():
    fake = FakeIGDB()
    raw = {**deepcopy(RAW_GAME), "updated_at": 500}
    fake.routes["games"] = httpx.Response(200, json=[raw])
    client = make_client(fake)
    result = asyncio.run(client.catalog_page(after_id=100, updated_after=400, window_end=600))
    body = fake.bodies("games")[0]
    assert "id > 100" in body and "updated_at >= 400" in body and "updated_at <= 600" in body
    assert 'version_parent = null' in body and 'game_type.type' in body
    assert "sort id asc; limit 500" in body
    assert result["complete"] and result["after_id"] == 1942
    fake.routes["games"] = httpx.Response(200, json=[raw, raw])
    with pytest.raises(igdb.IGDBError, match="cursor"):
        asyncio.run(client.catalog_page(after_id=100, updated_after=400, window_end=600))


def test_store_identity_lookup_paginates_and_ignores_invalid_appids(monkeypatch):
    fake = FakeIGDB()
    monkeypatch.setattr(igdb, "PAGE_LIMIT", 2)
    pages = [[{"id": 1, "game": 1942, "uid": "620"}, {"id": 2, "game": 1942, "uid": "730"}],
             [{"id": 3, "game": 1942, "uid": "not-an-appid"}]]
    fake.routes["external_games"] = lambda request: httpx.Response(200, json=pages.pop(0))
    assert asyncio.run(make_client(fake).steam_ids([1942], 1)) == {1942: [620, 730]}
    assert 'external_game_source = 1' in fake.bodies("external_games")[0]
    assert 'id > 2' in fake.bodies("external_games")[1]
