import asyncio
from unittest.mock import AsyncMock

import pytest

from app import catalog_starters as starters
from app.models import Game, GameExternalId, PlaytimeSnapshot, Review, User


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(starters, "STARTER_GAMES", ((620, "Portal 2"), (730, "Counter-Strike 2")))
    return AsyncMock(
        external_game_sources=AsyncMock(return_value={"steam": 1}),
        external_games=AsyncMock(return_value={"620": 72, "730": 73}),
        games=AsyncMock(return_value=[{
            "igdb_id": key, "name": name, "summary": "Real provider description",
            "first_release_date": None, "cover_image_id": "co123", "hero_image_id": None,
            "genres": ["Shooter"], "platforms": [{"slug": "win", "name": "PC", "abbreviation": "PC"}],
        } for key, name in ((72, "Provider Portal 2"), (73, "Provider Counter-Strike 2"))]),
    )


def test_populates_real_metadata_once_without_creating_activity(db, provider):
    assert asyncio.run(starters.populate(db, provider)) == {"added": 2, "skipped": 0}
    assert asyncio.run(starters.populate(db, provider)) == {"added": 0, "skipped": 2}
    game = db.query(Game).filter_by(steam_appid=620).one()
    assert game.name == "Provider Portal 2"
    assert game.summary == "Real provider description"
    assert game.cover_image_id == "co123"
    assert game.platforms[0].slug == "win"
    assert game.added_by_user_id is None
    assert db.query(GameExternalId).count() == 2
    assert all(db.query(model).count() == 0 for model in (User, Review, PlaytimeSnapshot))
    provider.external_games.assert_awaited_with(1, [620, 730])


def test_missing_mapping_and_metadata_do_not_create_guessed_games(db, provider):
    provider.games.return_value = []
    assert asyncio.run(starters.populate(db, provider)) == {"added": 0, "skipped": 2}
    assert db.query(Game).count() == 0


def test_existing_games_and_store_conflicts_are_not_changed_or_merged(db, provider):
    steam = Game(steam_appid=620, name="Existing Steam name")
    other = Game(name="Existing other store")
    other.external_ids.append(GameExternalId(provider="igdb", external_id="73"))
    db.add_all([steam, other])
    db.commit()
    assert asyncio.run(starters.populate(db, provider)) == {"added": 0, "skipped": 2}
    assert db.query(Game).count() == 2
    assert steam.name == "Existing Steam name" and steam.summary is None
    assert other.steam_appid is None


def test_provider_failure_leaves_database_unchanged(db, provider):
    provider.games.side_effect = ValueError("Provider unavailable")
    with pytest.raises(ValueError):
        asyncio.run(starters.populate(db, provider))
    assert db.query(Game).count() == 0


def test_mid_write_failure_rolls_back_whole_bootstrap(db, provider, monkeypatch):
    original = starters.apply_metadata
    def fail_second(db, game, metadata):
        if game.steam_appid == 730:
            raise ValueError("Invalid metadata")
        original(db, game, metadata)
    monkeypatch.setattr(starters, "apply_metadata", fail_second)
    with pytest.raises(ValueError):
        asyncio.run(starters.populate(db, provider))
    assert db.query(Game).count() == 0
    assert db.query(GameExternalId).count() == 0
