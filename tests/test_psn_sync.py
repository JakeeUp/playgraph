"""PlayStation sync against a fake PSN client: mapping, privacy, errors, and
keeping PSN numbers apart from Steam ones in every reader."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from arq import Retry
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import psn_status, worker
from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models import Game, GameExternalId, LinkedAccount, Platform, PlaytimeSnapshot, Review, User
from app.services import psn
from tests.security_helpers import MemoryRedis, auth_headers

ACCOUNT = "6515971742264256071"
GRADES = ("bronze", "silver", "gold", "platinum")


def counts(bronze=0, silver=0, gold=0, platinum=0):
    return {"bronze": bronze, "silver": silver, "gold": gold, "platinum": platinum}


def trophy_list(np_id, name, earned, defined, platforms=("PS4",), progress=50):
    return {"np_communication_id": np_id, "np_service_name": "trophy", "name": name, "platforms": list(platforms),
            "icon_url": None, "earned": earned, "defined": defined, "progress": progress,
            "trophy_set_version": "01.00", "last_updated": None}


def played(title_id, concept_id, name, minutes, concept_title_ids=()):
    return {"title_id": title_id, "concept_id": concept_id, "concept_title_ids": list(concept_title_ids),
            "name": name, "image_url": None, "category": "ps4_game", "platform": "PS4",
            "play_minutes": minutes, "play_count": 3, "first_played": None, "last_played": None}


class FakePSN:
    def __init__(self):
        self.profile = {"online_id": "RenamedPlayer", "about_me": "", "avatar_url": None}
        self.trophies = [
            trophy_list("NPWR00001_00", "Concept Game (PS4)", counts(10, 3, 1, 0), counts(30, 8, 3, 1)),
            trophy_list("NPWR00002_00", "Concept Game (PS5)", counts(5, 1, 0, 0), counts(20, 5, 2, 1),
                        platforms=("PS5",)),
            trophy_list("NPWR00003_00", "Old PS3 Game", counts(2), counts(40, 10, 4, 1), platforms=("PS3",)),
        ]
        self.played = [
            played("CUSA00001_00", "100", "Concept Game", 60, ["CUSA00001_00", "CUSA00009_00", "PPSA00001_00"]),
            played("PPSA00001_00", "100", "Concept Game", 30, ["CUSA00001_00", "PPSA00001_00"]),
            played("PPSA00002_00", "200", "No Trophies Game", 15, ["PPSA00002_00"]),
        ]
        self.title_lists = {"CUSA00001_00": ["NPWR00001_00"], "PPSA00001_00": ["NPWR00002_00"]}
        self.errors = {}
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.client_kwargs = kwargs
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    def _maybe_fail(self, name):
        self.calls.append(name)
        if name in self.errors:
            raise self.errors[name]

    async def get_profile(self, account_id):
        self._maybe_fail("profile")
        return self.profile

    async def get_trophy_titles(self, account_id):
        self._maybe_fail("trophies")
        return [dict(t) for t in self.trophies]

    async def get_played_titles(self, account_id):
        self._maybe_fail("played")
        return None if self.played is None else [dict(t) for t in self.played]

    async def get_trophy_lists_for_titles(self, account_id, title_ids):
        self._maybe_fail("lookup")
        self.looked_up = list(title_ids)
        return {t: self.title_lists[t] for t in title_ids if t in self.title_lists}


@pytest.fixture
def setup(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add(User(id=1, display_name="Player"))
        db.flush()
        db.add_all([
            LinkedAccount(id=1, user_id=1, platform=Platform.steam, platform_user_id="76561198000000001"),
            LinkedAccount(id=2, user_id=1, platform=Platform.psn, platform_user_id=ACCOUNT, display_handle="OldName"),
            Game(id=1, steam_appid=10, name="Steam Game", genres="RPG"),
        ])
        db.flush()
        db.add(PlaytimeSnapshot(user_id=1, game_id=1, linked_account_id=1, source="steam", playtime_minutes=500,
                                achievements_unlocked=5, achievements_total=10))
        db.commit()
    monkeypatch.setattr(worker, "SessionLocal", Session)
    monkeypatch.setattr(settings, "psn_npsso", SecretStr("fake-server-npsso"))
    fake = FakePSN()
    monkeypatch.setattr(worker.psn, "PSNClient", fake)
    store = MemoryRedis()
    return SimpleNamespace(Session=Session, fake=fake, ctx={"redis": store}, store=store)


def psn_rows(Session):
    with Session() as db:
        return db.query(PlaytimeSnapshot).filter_by(source="psn").order_by(PlaytimeSnapshot.id).all()


def game_ids(Session, provider):
    with Session() as db:
        return {row.external_id: row.game_id for row in db.query(GameExternalId).filter_by(provider=provider)}


@pytest.mark.asyncio
async def test_trophies_and_play_time_map_to_one_game_per_concept(setup):
    s = setup
    result = await worker.sync_psn_library(s.ctx, 1)
    assert result["status"] == "ok" and result["games_synced"] == 3 and result["games_created"] == 3
    assert result["trophies_visible"] and result["playtime_visible"] and result["mapping_lookups"] == 1
    # Only titles actually played are looked up, never the whole concept list.
    assert sorted(s.fake.looked_up) == ["CUSA00001_00", "PPSA00001_00", "PPSA00002_00"]
    assert s.fake.client_kwargs["client"] is not None  # one shared httpx client

    concept = game_ids(s.Session, "psn_concept")
    titles = game_ids(s.Session, "psn_title")
    lists = game_ids(s.Session, "psn_trophy")
    game = concept["100"]
    assert titles["CUSA00001_00"] == titles["CUSA00009_00"] == titles["PPSA00001_00"] == game
    assert lists["NPWR00001_00"] == lists["NPWR00002_00"] == game  # PS4 and PS5 lists, one Game
    ps3 = lists["NPWR00003_00"]
    assert ps3 not in concept.values()

    rows = {row.game_id: row for row in psn_rows(s.Session)}
    both = rows[game]
    assert both.playtime_minutes == 90  # PS4 + PS5 versions of one concept are both PSN time
    assert (both.trophies_bronze, both.trophies_silver, both.trophies_gold, both.trophies_platinum) == (15, 4, 1, 0)
    assert (both.trophies_bronze_total, both.trophies_platinum_total) == (50, 2)
    assert both.achievements_unlocked == 20 and both.achievements_total == 70
    assert both.trophy_progress is None  # two lists: no single Sony percentage
    assert both.linked_account_id == 2 and both.user_id == 1
    assert rows[ps3].playtime_minutes is None  # PS3 has no play time: unknown, not zero
    assert rows[ps3].trophy_progress == 50
    no_trophies = rows[concept["200"]]
    assert no_trophies.playtime_minutes == 15 and no_trophies.trophies_bronze_total is None

    with s.Session() as db:
        link = db.get(LinkedAccount, 2)
        assert link.display_handle == "RenamedPlayer" and link.last_synced_at is not None
        new_games = db.query(Game).filter(Game.id != 1).all()
        assert all(g.steam_appid is None and g.content_kind == "game" for g in new_games)
        steam = db.query(PlaytimeSnapshot).filter_by(source="steam").one()
        assert steam.playtime_minutes == 500  # untouched
    status = await psn_status.read_status(s.store, 1)
    assert status["trophies_visible"] and status["playtime_visible"] and status["problem"] is None


@pytest.mark.asyncio
async def test_a_repeat_sync_reuses_mappings_and_appends_snapshots(setup):
    s = setup
    await worker.sync_psn_library(s.ctx, 1)
    s.fake.calls.clear()
    s.fake.played[0]["play_minutes"] = 120
    second = await worker.sync_psn_library(s.ctx, 1)
    assert second["games_created"] == 0 and second["mapping_lookups"] == 0
    assert "lookup" not in s.fake.calls
    assert len(psn_rows(s.Session)) == 6
    with s.Session() as db:
        assert db.query(Game).count() == 4
    newest = {}
    for row in psn_rows(s.Session):
        newest[row.game_id] = row.playtime_minutes
    assert newest[game_ids(s.Session, "psn_concept")["100"]] == 150


@pytest.mark.asyncio
async def test_hidden_play_time_still_imports_trophies(setup):
    s = setup
    s.fake.played = None
    result = await worker.sync_psn_library(s.ctx, 1)
    assert result["status"] == "ok" and result["playtime_visible"] is False
    assert "lookup" not in s.fake.calls
    rows = psn_rows(s.Session)
    assert len(rows) == 3 and all(row.playtime_minutes is None for row in rows)
    assert sum(row.achievements_unlocked for row in rows) == 22
    assert (await psn_status.read_status(s.store, 1))["playtime_visible"] is False


@pytest.mark.asyncio
async def test_hidden_trophies_still_import_play_time(setup):
    s = setup
    s.fake.errors["trophies"] = psn.PSNPrivateError("PSN 403")
    result = await worker.sync_psn_library(s.ctx, 1)
    assert result["status"] == "ok" and result["trophies_visible"] is False
    rows = psn_rows(s.Session)
    assert sorted(row.playtime_minutes for row in rows) == [15, 90]
    assert all(row.trophies_bronze_total is None for row in rows)


@pytest.mark.asyncio
async def test_a_rejected_server_token_fails_the_job_and_stops_later_jobs(setup, caplog):
    s = setup
    s.fake.errors["trophies"] = psn.PSNAuthError("PSN token request was rejected")
    with pytest.raises(worker.PSNOperatorError):
        await worker.sync_psn_library(s.ctx, 1)
    assert "set a fresh PSN_NPSSO" in caplog.text and "fake-server-npsso" not in caplog.text
    assert psn_rows(s.Session) == []
    s.fake.calls.clear()
    del s.fake.errors["trophies"]
    with pytest.raises(worker.PSNOperatorError):
        await worker.sync_psn_library(s.ctx, 1)
    assert s.fake.calls == []  # no Sony call with the rejected token
    settings.psn_npsso = SecretStr("a-fresh-npsso")
    assert (await worker.sync_psn_library(s.ctx, 1))["status"] == "ok"


@pytest.mark.asyncio
async def test_rate_limits_retry_later_and_a_vanished_account_is_reported(setup):
    s = setup
    s.fake.errors["played"] = psn.PSNRateLimitedError("PSN 429", retry_after=psn.DEFAULT_RATE_LIMIT_BACKOFF)
    with pytest.raises(Retry):
        await worker.sync_psn_library(s.ctx, 1)
    assert psn_rows(s.Session) == []
    s.fake.errors["played"] = psn.PSNRateLimitedError("PSN 429", retry_after=42.0)
    with pytest.raises(Retry) as caught:
        await worker.sync_psn_library(s.ctx, 1)
    assert caught.value.defer_score == 43_000  # Sony's Retry-After, rounded up, in ms
    s.fake.errors = {"profile": psn.PSNNotFoundError("PSN 400")}
    result = await worker.sync_psn_library(s.ctx, 1)
    assert result["status"] == "error"
    assert (await psn_status.read_status(s.store, 1))["problem"] == "account_not_found"


@pytest.mark.asyncio
async def test_no_link_or_feature_off_does_nothing(setup, monkeypatch):
    s = setup
    assert (await worker.sync_psn_library(s.ctx, 99))["status"] == "error"
    monkeypatch.setattr(settings, "psn_npsso", SecretStr(""))
    assert (await worker.sync_psn_library(s.ctx, 1))["status"] == "error"
    assert s.fake.calls == []


def test_steam_change_detection_ignores_playstation_snapshots(setup):
    s = setup
    with s.Session() as db:
        # Even a PSN row on the same Game, newer than Steam's, is not a Steam reading.
        db.add(PlaytimeSnapshot(user_id=1, game_id=1, linked_account_id=2, source="psn", playtime_minutes=7,
                                captured_at=datetime.now(timezone.utc) + timedelta(hours=1)))
        db.commit()
        assert worker._previous_snapshots(db, 1) == {1: (500, 5, 10)}


@pytest.fixture
def web(setup, monkeypatch):
    s = setup
    db = s.Session()
    monkeypatch.setattr(app.state, "arq_pool", s.store, raising=False)
    monkeypatch.setitem(app.dependency_overrides, get_db, lambda: db)
    client = TestClient(app, base_url="http://localhost:8000")
    client.headers.update(auth_headers(s.store, 1))
    yield client, db
    db.close()


@pytest.mark.asyncio
async def test_library_keeps_steam_and_playstation_apart(setup, web):
    s = setup
    client, db = web
    await worker.sync_psn_library(s.ctx, 1)
    # A PSN snapshot on the Steam game too: the two must come back as two entries.
    db.add(PlaytimeSnapshot(user_id=1, game_id=1, linked_account_id=2, source="psn", playtime_minutes=40,
                            trophies_bronze=1, trophies_silver=0, trophies_gold=0, trophies_platinum=0,
                            trophies_bronze_total=2, trophies_silver_total=0, trophies_gold_total=0,
                            trophies_platinum_total=0))
    db.commit()
    library = client.get("/me/library").json()
    steam = [e for e in library if e["source"] == "steam"]
    playstation = [e for e in library if e["source"] == "psn"]
    assert [(e["game"]["id"], e["playtime_minutes"], e["trophies"]) for e in steam] == [(1, 500, None)]
    assert {e["game"]["id"]: e["playtime_minutes"] for e in playstation if e["game"]["id"] == 1} == {1: 40}
    assert len(playstation) == 4
    concept = next(e for e in playstation if e["game"]["name"] == "Concept Game")
    assert concept["trophies"] == {"earned": {"platinum": 0, "gold": 1, "silver": 4, "bronze": 15},
                                   "total": {"platinum": 2, "gold": 5, "silver": 13, "bronze": 50}, "progress": None}
    assert concept["game"]["steam_appid"] is None
    # Unknown play time sorts after known play time.
    assert library[-1]["playtime_minutes"] is None
    # The genre graph stays Steam only: 500 minutes, not 540.
    assert client.get("/me/genres").json() == [{"genre": "RPG", "total_minutes": 500, "game_count": 1}]
    assert client.get("/me/feed").status_code == 200  # NULL play time does not break preferences


@pytest.mark.asyncio
async def test_reviews_are_verified_from_one_source_and_say_which(setup, web):
    s = setup
    client, db = web
    s.fake.played = None  # PSN play time hidden: trophies only
    await worker.sync_psn_library(s.ctx, 1)
    ps3 = game_ids(s.Session, "psn_trophy")["NPWR00003_00"]
    headers = auth_headers(s.store, 1)
    psn_review = client.post(f"/games/{ps3}/reviews", json={"rating": 4}, headers=headers).json()
    assert psn_review["verified_source"] == "psn"
    assert psn_review["verified_playtime_minutes"] is None  # hidden hours stay unverified
    assert psn_review["verified_achievement_pct"] == pytest.approx(100 * 2 / 55)

    db.add(PlaytimeSnapshot(user_id=1, game_id=1, linked_account_id=2, source="psn", playtime_minutes=9999,
                            captured_at=datetime.now(timezone.utc) + timedelta(hours=1)))
    db.commit()
    steam_review = client.post("/games/1/reviews", json={"rating": 5}, headers=headers).json()
    assert steam_review["verified_source"] == "steam"
    assert steam_review["verified_playtime_minutes"] == 500  # Steam's number, never 500 + 9999
    with s.Session() as check:
        assert check.get(Review, steam_review["id"]).verified_source == "steam"
