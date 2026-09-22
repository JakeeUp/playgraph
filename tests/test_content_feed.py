from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.content import content_kind
from app.database import get_db
from app.deps import get_current_user
from app.models import Comment, Game, PlaytimeSnapshot, Review, User, utcnow
from app.routers import catalog, feed, library


@pytest.fixture
def fixture(db):
    app = FastAPI()
    for router in (catalog.router, feed.router, library.router):
        app.include_router(router)
    user = User(display_name="Viewer")
    author = User(display_name="Reviewer")
    db.add_all([user, author]); db.flush()
    games = [Game(steam_appid=431960, name="Wallpaper Engine", genres="Casual,Indie"),
             Game(steam_appid=400040, name="ShareX", genres=None),
             Game(steam_appid=1812620, name="DSX", genres="Action,Indie"),
             Game(steam_appid=999, name="Editor", genres=" Indie ,\t Design & Illustration\n"),
             Game(steam_appid=1000, name="Adventure", genres="Adventure,RPG"),
             Game(steam_appid=1001, name="New RPG", genres="RPG"),
             Game(steam_appid=1002, name="Racing", genres="Racing")]
    db.add_all(games); db.flush()
    db.add_all([PlaytimeSnapshot(user_id=user.id, game_id=games[0].id, playtime_minutes=99999),
                PlaytimeSnapshot(user_id=user.id, game_id=games[4].id, playtime_minutes=120)])
    now = utcnow()
    reviews = [Review(user_id=author.id, game_id=game.id, rating=3.5, body="Review text", created_at=now - timedelta(minutes=index))
               for index, game in enumerate(games)]
    db.add_all(reviews); db.flush()
    db.add(Comment(user_id=user.id, review_id=reviews[4].id, body="A real comment"))
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app), user, games, reviews


def test_software_classification_agrees_in_python_and_sql(fixture, db):
    client, _, games, _ = fixture
    assert [game.content_kind for game in games] == ["software"] * 4 + ["game"] * 3
    assert client.get("/games?kind=software").json()["total"] == 4
    assert client.get("/games").json()["total"] == 3
    assert client.get("/games?kind=all").json()["total"] == 7
    assert client.get("/games?kind=software&limit=1&offset=3").json()["games"][0]["name"] == "Wallpaper Engine"
    assert client.get("/games?kind=software&q=Adventure").json()["total"] == 0
    assert client.get("/games?kind=invalid").status_code == 422
    # A game's name alone is not a software classifier.
    assert content_kind(123, "Indie,Simulation,Casual") == "game"


def test_classification_follows_genre_updates(db):
    # The worker fills genres in after first creating a game, so the stored
    # kind has to follow an update, not just the insert.
    game = Game(steam_appid=777, name="Changes shelf", genres=None)
    db.add(game); db.commit()
    assert game.content_kind == "game"
    game.genres = "Utilities,Photo Editing"; db.commit()
    assert db.query(Game).filter(Game.content_kind == "software").one().id == game.id


def test_game_stats_exclude_software_without_deleting_its_records(fixture):
    client, _, _, _ = fixture
    games = client.get("/me/library?kind=game").json()
    software = client.get("/me/library?kind=software").json()
    assert len(games) == len(software) == 1
    assert games[0]["playtime_minutes"] == 120
    assert software[0]["playtime_minutes"] == 99999
    assert len(client.get("/me/library").json()) == 2
    genres = client.get("/me/genres").json()
    assert {row["genre"] for row in genres} == {"Adventure", "RPG"}
    assert all(row["total_minutes"] == 120 for row in genres)
    assert {row["genre"] for row in client.get("/me/genres?kind=software").json()} == {"Casual", "Indie"}


def test_equal_timestamp_snapshots_cannot_double_count_games(fixture, db):
    client, user, games, _ = fixture
    old = db.query(PlaytimeSnapshot).filter_by(user_id=user.id, game_id=games[4].id).one()
    db.add(PlaytimeSnapshot(user_id=user.id, game_id=games[4].id, playtime_minutes=180, captured_at=old.captured_at))
    db.commit()
    response = client.get("/me/library?kind=game").json()
    assert len(response) == 1 and response[0]["playtime_minutes"] == 180


def test_feed_uses_real_comments_and_excludes_software(fixture):
    client, _, _, _ = fixture
    result = client.get("/feed").json()
    assert result["total"] == 3
    assert all(item["game"]["content_kind"] == "game" for item in result["items"])
    assert result["items"][0]["comment_count"] == 1
    assert result["items"][0]["review"]["author_name"] == "Reviewer"
    assert "playtime_minutes" not in result["items"][0]["game"]


def test_personal_feed_explains_library_and_genre_matches(fixture, db):
    client, user, games, _ = fixture
    own = Review(user_id=user.id, game_id=games[5].id, rating=5, body="My own review")
    db.add(own); db.commit()
    items = client.get("/me/feed").json()["items"]
    assert [item["game"]["name"] for item in items] == ["Adventure", "New RPG", "Racing"]
    assert items[0]["reason"] == "In your game library"
    assert items[1]["reason"] == "Because you play RPG games"
    assert all(item["review"]["user_id"] != user.id for item in items)
    assert client.get("/feed").json()["total"] == 4


def test_new_reviews_do_not_shift_existing_feed_pages(fixture, db):
    client, _, games, _ = fixture
    first = client.get("/feed?limit=1").json()
    author = User(display_name="New author"); db.add(author); db.flush()
    db.add(Review(user_id=author.id, game_id=games[4].id, rating=4, body="New")); db.commit()
    next_page = client.get(f"/feed?limit=2&offset=1&before={first['before']}").json()
    assert next_page["total"] == 3
    assert first["items"][0]["review"]["id"] not in [item["review"]["id"] for item in next_page["items"]]
    assert client.get("/feed").json()["total"] == 4


def test_review_thread_and_feed_validation(fixture):
    client, _, _, reviews = fixture
    thread = client.get(f"/reviews/{reviews[4].id}")
    assert thread.status_code == 200
    assert thread.json()["comment_count"] == 1
    assert client.get("/reviews/99999").status_code == 404
    for route in ["/feed?limit=51", "/me/feed?offset=501", "/feed?before=-1", "/feed?q=" + "x" * 121,
                  "/feed?before=9223372036854775808", "/me/feed?before=9223372036854775808",
                  "/me/feed?library_before=-1", "/me/feed?library_before=9223372036854775808",
                  "/reviews/9223372036854775808", "/reviews/0"]:
        assert client.get(route).status_code == 422


def test_personal_pages_keep_library_and_reviews_from_first_request(fixture, db):
    client, user, games, _ = fixture
    first = client.get("/me/feed?limit=2").json()
    assert [row["game"]["name"] for row in first["items"]] == ["Adventure", "New RPG"]
    db.add(PlaytimeSnapshot(user_id=user.id, game_id=games[6].id, playtime_minutes=9999))
    author = User(display_name="New author"); db.add(author); db.flush()
    db.add(Review(user_id=author.id, game_id=games[6].id, rating=5))
    db.commit()
    page = client.get("/me/feed", params={"offset": 2, "before": first["before"],
                      "library_before": first["library_before"]}).json()
    assert page["total"] == 3
    assert [row["game"]["name"] for row in page["items"]] == ["Racing"]
    refreshed = client.get("/me/feed").json()
    assert refreshed["total"] == 4
    names = [row["game"]["name"] for row in refreshed["items"]]
    assert names.index("Racing") < names.index("New RPG")
    assert refreshed["library_before"] > first["library_before"]


def test_personal_feed_caps_candidates_and_pages_at_500(fixture, db):
    client, _, games, _ = fixture
    for index in range(501):
        author = User(display_name=f"Player {index}"); db.add(author); db.flush()
        db.add(Review(user_id=author.id, game_id=games[6].id, rating=3))
    db.commit()
    result = client.get("/me/feed?offset=499&limit=50").json()
    assert result["total"] == result["candidate_limit"] == 500
    assert len(result["items"]) == 1
    assert client.get("/me/feed?offset=500").json()["items"] == []


def test_personal_feed_requires_authentication(fixture):
    client, _, _, _ = fixture
    del client.app.dependency_overrides[get_current_user]
    assert client.get("/me/feed").status_code == 401
    assert client.get("/feed").status_code == 200
