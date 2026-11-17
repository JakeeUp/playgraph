from datetime import datetime, timezone

import pytest

from app.models import Game, GameStatus, PlaytimeSnapshot, Review
from app.replay import build_replay
from tests.test_game_status import api, who  # noqa: F401


def date(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


NOW = date("2027-02-01")
GAME = {"id": 1, "name": "Game", "genres": "RPG,Action,RPG", "content_kind": "game"}


def row(when, minutes, *, game=GAME, source="steam", achievements=0, **extra):
    return {"id": extra.pop("id", 1), "game": game, "source": source,
        "captured_at": date(when), "playtime_minutes": minutes,
        "achievements_unlocked": achievements, **extra}


def replay(rows, **kwargs):
    return build_replay(2026, rows, now=NOW, **kwargs)


def test_baseline_last_before_year_and_months_are_deltas_not_lifetime():
    result = replay([row("2025-11-01", 600, achievements=1), row("2025-12-31", 1200, achievements=5),
        row("2026-01-01", 1260, achievements=6), row("2026-03-01", 1500, achievements=9),
        row("2027-01-01", 9000)], first_sync=date("2025-11-01"))
    source = result["sources"][0]
    assert source["total_minutes"] == 300 and source["achievements_earned"] == 4
    assert [m["total_minutes"] for m in source["months"]][:3] == [60, 0, 240]
    assert source["genres"] == [{"genre": "Action", "total_minutes": 300}, {"genre": "RPG", "total_minutes": 300}]
    assert result["partial_year"] is False


def test_midyear_join_and_new_game_start_at_their_first_snapshot():
    result = replay([row("2026-03-01", 10000, achievements=100), row("2026-07-01", 10060, achievements=102),
        row("2026-07-02", 10060, achievements=102)], first_sync=date("2026-03-01"))
    assert result["sources"][0]["total_minutes"] == 60
    assert result["sources"][0]["achievements_earned"] == 2
    assert result["partial_year"] and result["tracked_since"] == date("2026-03-01")


def test_regressions_never_inflate_totals_or_the_month_chart():
    source = replay([row("2025-12-31", 100), row("2026-01-01", 300),
        row("2026-02-01", 80), row("2026-03-01", 250)])["sources"][0]
    assert source["total_minutes"] == 150
    assert sum(m["total_minutes"] for m in source["months"]) == 150
    assert replay([row("2025-12-31", 100), row("2026-01-01", 50)])["sources"][0]["total_minutes"] == 0


def test_sources_and_hidden_psn_hours_stay_separate_from_steam():
    source_rows = [row("2025-12-31", 600), row("2026-02-01", 720),
        row("2025-12-31", None, source="psn", trophies_bronze=3),
        row("2026-02-01", None, source="psn", trophies_bronze=7)]
    sources = {s["source"]: s for s in replay(source_rows)["sources"]}
    assert sources["steam"]["total_minutes"] == 120
    assert sources["psn"]["total_minutes"] is None
    assert sources["psn"]["unknown_playtime_games"] == 1
    assert sources["psn"]["trophies_earned"]["bronze"] == 4
    assert all(m["total_minutes"] is None for m in sources["psn"]["months"])


def test_nulls_never_turn_into_lifetime_hours_when_playtime_becomes_visible():
    sources = replay([row("2025-12-31", None, source="psn"),
        row("2026-01-01", 60000, source="psn"), row("2026-02-01", 60060, source="psn")])["sources"]
    assert sources[0]["total_minutes"] == 60


def test_software_empty_year_and_future_months():
    assert replay([])["has_data"] is False
    assert not replay([row("2026-01-01", 9000, game={**GAME, "content_kind": "software"})])["has_data"]
    result = build_replay(2026, [row("2026-01-01", 1), row("2026-02-01", 2)], now=date("2026-03-01"))
    assert result["partial_year"]
    assert result["sources"][0]["months"][3]["total_minutes"] is None


def test_same_timestamp_breaks_ties_by_snapshot_id_and_top_five_is_bounded():
    rows = [row("2025-12-31", 60, id=2), row("2025-12-31", 50, id=1), row("2026-03-01", 100, id=3)]
    assert replay(rows)["sources"][0]["total_minutes"] == 40
    for i in range(2, 10):
        rows += [row("2025-12-31", 0, game={**GAME, "id": i}), row("2026-03-01", i, game={**GAME, "id": i})]
    assert len(replay(rows)["sources"][0]["top_games"]) == 5


def test_reviews_and_current_completed_statuses_use_year_boundaries():
    reviews = [{"game": GAME, "rating": rating, "created_at": date(when)}
        for when, rating in [("2025-12-31", 1), ("2026-01-01", 4), ("2026-12-31", 5), ("2027-01-01", 1)]]
    done = [{"game": GAME, "completed_at": date("2026-06-01")}]
    result = build_replay(2026, [], reviews, done, now=NOW)
    assert result["reviews"]["count"] == 2 and result["reviews"]["average_rating"] == 4.5
    assert result["reviews"]["highest_rated"]["rating"] == 5 and result["completed_count"] == 1


def test_route_is_owner_only_and_reflects_new_syncs_without_stale_cache(api, db, monkeypatch):
    from app.routers import library
    monkeypatch.setattr(library, "utcnow", lambda: NOW)
    db.add_all([PlaytimeSnapshot(user_id=1, game_id=1, captured_at=date("2025-12-31"), playtime_minutes=100),
        PlaytimeSnapshot(user_id=1, game_id=1, captured_at=date("2026-05-01"), playtime_minutes=160),
        PlaytimeSnapshot(user_id=2, game_id=1, captured_at=date("2026-05-01"), playtime_minutes=10000)])
    db.commit()
    assert api.get("/me/replay?year=2026").status_code == 401
    response = api.get("/me/replay?year=2026&user_id=2", headers=who(api, 1))
    assert response.status_code == 200 and response.json()["sources"][0]["total_minutes"] == 60
    assert api.get("/me/replay?year=2026", headers=who(api, 2)).json()["sources"][0]["total_minutes"] == 0
    db.add(PlaytimeSnapshot(user_id=1, game_id=1, captured_at=date("2026-06-01"), playtime_minutes=220))
    db.commit()
    assert api.get("/me/replay?year=2026", headers=who(api, 1)).json()["sources"][0]["total_minutes"] == 120


@pytest.mark.parametrize("year", ["1999", "9999", "-1", "tomorrow", "2028"])
def test_route_rejects_invalid_years(api, monkeypatch, year):
    from app.routers import library
    monkeypatch.setattr(library, "utcnow", lambda: NOW)
    assert api.get(f"/me/replay?year={year}", headers=who(api, 1)).status_code == 422


def test_route_filters_software_reviews_other_owners_and_future_rows(api, db, monkeypatch):
    from app.routers import library
    monkeypatch.setattr(library, "utcnow", lambda: NOW)
    db.add(Game(id=3, steam_appid=365670, name="Blender", genres="Design & Illustration"))
    db.get(Review, 11).created_at = date("2025-01-01")
    db.flush()
    db.add_all([PlaytimeSnapshot(user_id=1, game_id=3, captured_at=date("2026-06-01"), playtime_minutes=500),
        PlaytimeSnapshot(user_id=1, game_id=1, captured_at=date("2028-01-01"), playtime_minutes=90000),
        GameStatus(user_id=1, game_id=1, status="completed", completed_at=date("2026-06-01")),
        Review(user_id=1, game_id=3, rating=5, created_at=date("2026-06-01")),
        Review(user_id=2, game_id=2, rating=5, created_at=date("2026-06-01"))])
    db.commit()
    result = api.get("/me/replay?year=2026", headers=who(api, 1)).json()
    assert result["sources"] == [] and result["completed_count"] == 1 and result["reviews"]["count"] == 0
