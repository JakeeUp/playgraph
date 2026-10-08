"""IGDB client and catalog refresh, against a fake IGDB. No network."""
import asyncio
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import igdb_catalog
from app.config import settings
from app.database import Base
from app.models import Game, GameExternalId, GamePlatform, IgdbMatchCandidate
from app.services import igdb
from app.services.igdb import IGDBAuthError, IGDBClient, IGDBRateLimitedError
from tests.security_helpers import MemoryRedis

CLIENT_ID = "fake-client-id"
SECRET = "fake-client-secret-value"  # nosec B105 - test fixture


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class FakeIGDB:
    """Twitch's token endpoint plus IGDB endpoints answered by handlers."""

    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.issued = 0
        self.token_status = 200
        self.routes = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.url.host == "id.twitch.tv":
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"message": "invalid client secret"})
            self.issued += 1
            return httpx.Response(200, json={"access_token": f"app-token-{self.issued}", "expires_in": 5000000,
                                             "token_type": "bearer"})
        endpoint = request.url.path.rsplit("/", 1)[-1]
        handler = self.routes[endpoint]
        return handler(request) if callable(handler) else handler

    def bodies(self, endpoint):
        return [r.content.decode() for r in self.calls if r.url.path.endswith("/" + endpoint)]


def make_client(fake, clock=None, sleeps=None):
    clock = clock or Clock()

    async def sleep(seconds):
        if sleeps is not None:
            sleeps.append(seconds)
        clock.now += seconds

    http = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    return IGDBClient(CLIENT_ID, SecretStr(SECRET), client=http, state=igdb._State(clock), sleep=sleep)


def run(coro):
    return asyncio.run(coro)


RAW_GAME = {"id": 1942, "name": "The Witcher 3: Wild Hunt", "slug": "the-witcher-3-wild-hunt",
            "summary": "Geralt hunts.", "first_release_date": 1431993600,
            "cover": {"id": 1, "image_id": "co1wyy"}, "genres": [{"id": 12, "name": "Role-playing (RPG)"}],
            "artworks": [{"id": 3, "image_id": "ar1abc"}], "screenshots": [{"id": 4, "image_id": "sc1abc"}],
            "platforms": [{"id": 6, "slug": "win", "name": "PC (Microsoft Windows)", "abbreviation": "PC"},
                          {"id": 167, "slug": "ps5", "name": "PlayStation 5", "abbreviation": "PS5"}]}


# Client --------------------------------------------------------------------

def test_token_is_fetched_once_with_the_secret_in_the_body_not_the_url():
    fake = FakeIGDB()
    fake.routes["games"] = httpx.Response(200, json=[])
    client = make_client(fake)
    run(client.query("games", "fields name; limit 1;"))
    run(client.query("games", "fields name; limit 1;"))
    token_calls = [r for r in fake.calls if r.url.host == "id.twitch.tv"]
    assert len(token_calls) == 1
    assert SECRET not in str(token_calls[0].url)
    assert parse_qs(token_calls[0].content.decode())["client_secret"] == [SECRET]
    api = [r for r in fake.calls if r.url.host == "api.igdb.com"]
    assert api[0].headers["Client-ID"] == CLIENT_ID
    assert api[0].headers["Authorization"] == "Bearer app-token-1"
    assert all(SECRET not in r.headers.get("Authorization", "") for r in api)


def test_rejected_credentials_are_an_auth_error_without_the_secret():
    fake = FakeIGDB()
    fake.token_status = 403
    with pytest.raises(IGDBAuthError) as caught:
        run(make_client(fake).query("games", "fields name;"))
    assert SECRET not in str(caught.value)


def test_missing_credentials_never_send_a_request():
    fake = FakeIGDB()
    client = IGDBClient("", SecretStr(""), client=httpx.AsyncClient(transport=httpx.MockTransport(fake)))
    with pytest.raises(IGDBAuthError):
        run(client.query("games", "fields name;"))
    assert fake.calls == []


def test_a_revoked_token_is_renewed_once():
    fake = FakeIGDB()
    statuses = iter([401, 200])
    fake.routes["games"] = lambda request: httpx.Response(next(statuses), json=[])
    assert run(make_client(fake).query("games", "fields name;")) == []
    assert fake.issued == 2


def test_429_is_retried_after_a_pause_then_gives_up():
    fake = FakeIGDB()
    statuses = iter([429, 200])
    fake.routes["games"] = lambda request: httpx.Response(next(statuses), json=[])
    sleeps = []
    assert run(make_client(fake, sleeps=sleeps).query("games", "fields name;")) == []
    assert sleeps and sleeps[-1] == 1.0

    fake.routes["games"] = httpx.Response(429, json=[])
    with pytest.raises(IGDBRateLimitedError):
        run(make_client(fake).query("games", "fields name;"))


def test_requests_stay_under_four_per_second():
    fake = FakeIGDB()
    fake.routes["games"] = httpx.Response(200, json=[])
    clock, sleeps = Clock(), []
    client = make_client(fake, clock, sleeps)

    async def burst():
        for _ in range(6):
            await client.query("games", "fields name;")

    run(burst())
    # Four start at once; the fifth waits for the first to leave the one-second
    # window, and the sixth then fits beside it.
    assert sleeps == [pytest.approx(1.0)]
    assert len(fake.bodies("games")) == 6


def test_search_text_cannot_break_out_of_the_query_string():
    assert igdb.search_text('zelda"; fields *; where id = 1') == "zelda fields * where id = 1"
    assert igdb.search_text("a\\b\x00c") == "a b c"
    assert len(igdb.search_text("x" * 500)) == igdb.MAX_QUERY_TEXT
    fake = FakeIGDB()
    fake.routes["games"] = httpx.Response(200, json=[RAW_GAME])
    found = run(make_client(fake).search('witcher"; fields *;', platform_slugs=["ps5", 'bad"slug']))
    body = fake.bodies("games")[0]
    assert body.startswith('search "witcher fields *"; ')
    assert 'platforms.slug = ("ps5")' in body and "bad" not in body
    assert found[0]["igdb_id"] == 1942
    assert run(make_client(fake).search(" a ")) == []  # too short: no request


def test_external_game_uids_are_quoted_and_validated():
    fake = FakeIGDB()
    fake.routes["external_games"] = httpx.Response(200, json=[
        {"id": 5, "uid": "292030", "game": 1942}, {"id": 6, "uid": "999", "game": 7}])
    found = run(make_client(fake).external_games(1, ["292030", '1"); fields *', "292030"]))
    assert found == {"292030": 1942}  # an ID not asked for is ignored
    assert 'uid = ("292030")' in fake.bodies("external_games")[0]


def test_normalize_game_keeps_only_safe_values():
    game = igdb.normalize_game(RAW_GAME)
    assert game["first_release_date"].year == 2015
    assert game["cover_image_id"] == "co1wyy"
    assert [p["slug"] for p in game["platforms"]] == ["win", "ps5"]
    assert igdb.normalize_game({"id": 1}) is None
    assert game["hero_image_id"] == "ar1abc"  # artwork wins over screenshots
    odd = igdb.normalize_game({"id": 2, "name": "X", "cover": {"image_id": "../evil"},
                               "platforms": [{"slug": "Bad Slug"}], "artworks": [{"image_id": "../x"}],
                               "screenshots": [{"image_id": "sc9"}]})
    assert odd["cover_image_id"] is None and odd["platforms"] == []
    assert odd["hero_image_id"] == "sc9"
    assert igdb.cover_url("co1wyy") == "https://images.igdb.com/igdb/image/upload/t_cover_big/co1wyy.jpg"
    assert igdb.cover_url("../x") is None


# Catalog refresh -----------------------------------------------------------

@pytest.fixture
def catalog(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add_all([
            Game(id=1, steam_appid=292030, name="The Witcher 3", genres="RPG"),
            Game(id=2, steam_appid=499450, name="The Witcher 3 GOTY", genres="RPG"),
            Game(id=3, steam_appid=555, name="Unknown to IGDB"),
            Game(id=4, steam_appid=None, name="PlayStation game"),
            Game(id=5, steam_appid=None, name="Witcher on PlayStation"),
        ])
        db.add_all([GameExternalId(game_id=4, provider="psn_concept", external_id="111"),
                    GameExternalId(game_id=5, provider="psn_concept", external_id="204794")])
        db.commit()
    fake = FakeIGDB()
    fake.routes["external_game_sources"] = httpx.Response(200, json=[{"id": 1, "name": "Steam"},
                                                                     {"id": 36, "name": "Playstation Store US"}])

    def external_games(request):
        # Both Steam app IDs, and the Witcher's Sony concept ID, point at one IGDB game.
        if "external_game_source = 1 " in request.content.decode():
            return httpx.Response(200, json=[{"uid": "292030", "game": 1942}, {"uid": "499450", "game": 1942}])
        return httpx.Response(200, json=[{"uid": "204794", "game": 1942}])

    fake.routes["external_games"] = external_games
    fake.routes["games"] = httpx.Response(200, json=[RAW_GAME])
    monkeypatch.setattr(igdb_catalog, "SessionLocal", Session)
    monkeypatch.setattr(igdb_catalog.igdb, "IGDBClient", lambda: make_client(fake))
    monkeypatch.setattr(settings, "igdb_client_id", CLIENT_ID)
    monkeypatch.setattr(settings, "igdb_client_secret", SecretStr(SECRET))
    return Session, fake


def test_refresh_maps_by_exact_steam_id_and_proposes_instead_of_merging(catalog):
    Session, fake = catalog
    result = run(igdb_catalog.refresh_igdb_catalog({"redis": MemoryRedis()}))
    assert result == {"status": "ok", "steam_mapped": 1, "steam_proposed": 1, "psn_mapped": 0,
                      "psn_proposed": 1, "metadata_refreshed": 3}
    with Session() as db:
        mapped = {(e.game_id, e.external_id) for e in db.query(GameExternalId).filter_by(provider="igdb")}
        assert mapped == {(1, "1942")}
        proposals = {(c.game_id, c.igdb_id, c.reason, c.status) for c in db.query(IgdbMatchCandidate)}
        # The GOTY Steam copy and the PlayStation copy are proposed, never merged.
        assert proposals == {(2, 1942, "igdb_taken", "pending"), (5, 1942, "igdb_taken", "pending")}
        witcher = db.get(Game, 1)
        assert witcher.summary == "Geralt hunts." and witcher.cover_image_id == "co1wyy"
        assert witcher.genres == "RPG"  # the store's own genres are kept
        assert {p.slug for p in witcher.platforms} == {"win", "ps5"}
        # Proposed copies show the IGDB game's art and facts but stay separate games.
        for proposed in (db.get(Game, 2), db.get(Game, 5)):
            assert proposed.cover_image_id == "co1wyy" and proposed.hero_image_id == "ar1abc"
            assert not db.query(GameExternalId).filter_by(game_id=proposed.id, provider="igdb").count()
        assert db.get(Game, 4).summary is None  # never matched by name
    # Store IDs only: no game name is ever sent as a match key.
    sent = "".join(fake.bodies("external_games"))
    assert "PlayStation game" not in sent and '"111"' in sent and '"204794"' in sent


def test_second_refresh_skips_fresh_games_and_does_not_duplicate_proposals(catalog):
    Session, fake = catalog
    run(igdb_catalog.refresh_igdb_catalog({"redis": MemoryRedis()}))
    calls = len(fake.bodies("games"))
    result = run(igdb_catalog.refresh_igdb_catalog({"redis": MemoryRedis()}))
    assert result["steam_mapped"] == result["psn_mapped"] == 0
    assert result["steam_proposed"] == result["psn_proposed"] == result["metadata_refreshed"] == 0
    assert len(fake.bodies("games")) == calls
    with Session() as db:
        assert db.query(IgdbMatchCandidate).count() == 2


def test_metadata_fills_empty_genres_and_replaces_stale_platforms(catalog):
    Session, _ = catalog
    with Session() as db:
        game = db.get(Game, 4)
        game.platforms.append(GamePlatform(slug="ps3", name="PlayStation 3"))
        db.add(GameExternalId(game_id=4, provider="igdb", external_id="1942"))
        db.query(Game).filter(Game.id.in_([1, 2])).update({"steam_appid": None}, synchronize_session=False)
        db.commit()
    run(igdb_catalog.refresh_igdb_catalog({"redis": MemoryRedis()}))
    with Session() as db:
        game = db.get(Game, 4)
        assert game.genres == "Role-playing (RPG)"
        assert {p.slug for p in game.platforms} == {"win", "ps5"}


def test_refresh_is_skipped_without_credentials(catalog, monkeypatch):
    monkeypatch.setattr(settings, "igdb_client_id", "")
    assert run(igdb_catalog.refresh_igdb_catalog({"redis": None}))["status"] == "skipped"
    assert catalog[1].calls == []


def test_rejected_credentials_end_the_refresh_cleanly(catalog):
    _, fake = catalog
    fake.token_status = 400
    assert run(igdb_catalog.refresh_igdb_catalog({"redis": None})) == {
        "status": "error", "detail": "IGDB credentials rejected"}
