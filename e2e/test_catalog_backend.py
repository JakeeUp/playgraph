"""One real HTTP/browser/database journey, opt-in only on isolated CI services."""
import os
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import urlopen

import pytest
from playwright.sync_api import expect

from loadtest.ci_check import ROOT, app_environment, stop_server

pytestmark = [pytest.mark.backend, pytest.mark.skipif(
    os.environ.get("CI") != "true" or not os.environ.get("E2E_DATABASE_URL") or not os.environ.get("E2E_REDIS_URL"),
    reason="Only CI with isolated Postgres and Redis")]


@pytest.fixture
def backend_site(tmp_path):
    database_url = os.environ["E2E_DATABASE_URL"]
    database = urlsplit(database_url)
    redis_url = os.environ["E2E_REDIS_URL"]
    redis = urlsplit(redis_url)
    assert database.scheme == "postgresql+psycopg" and database.hostname in {"localhost", "127.0.0.1"}
    assert database.path == "/playgraph_e2e_test", "Use the dedicated empty browser-test database"
    assert redis.scheme == "redis" and redis.hostname in {"localhost", "127.0.0.1"}
    from sqlalchemy import create_engine, inspect
    engine = create_engine(database_url, hide_parameters=True)
    try:
        assert not inspect(engine).get_table_names(), "Backend journey requires an empty database"
    finally:
        engine.dispose()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    host = f"http://127.0.0.1:{port}"
    env = app_environment(tmp_path, host, database_url, redis_url)
    env["PGTZ"] = "EST5"
    output = ROOT / "test-results" / "backend"
    output.mkdir(parents=True, exist_ok=True)
    with (output / "server.log").open("w", encoding="utf-8") as log:
        for args in (("alembic", "-c", str(ROOT / "alembic.ini"), "upgrade", "head"), ("scripts.seed_demo",)):
            subprocess.run([sys.executable, "-m", *args], cwd=tmp_path, env=env,
                           stdout=log, stderr=subprocess.STDOUT, check=True, timeout=30)
        server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                                   "--port", str(port)], cwd=tmp_path, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                assert server.poll() is None, "Backend exited; see test-results/backend/server.log"
                try:
                    with urlopen(host + "/health", timeout=1) as response:
                        if response.status == 200:
                            break
                except URLError:
                    pass
                time.sleep(0.1)
            else:
                pytest.fail("Backend did not become ready")
            yield host
        finally:
            stop_server(server)


def test_public_catalog_search_and_game_page(backend_app):
    page = backend_app.page
    page.goto(backend_app.site + "/app#view=explore")
    expect(page.locator("#games .game-card").first).to_be_visible()
    with page.expect_response(lambda response: "/games?" in response.url and "q=Hollow" in response.url) as search:
        page.get_by_role("searchbox", name="Search games", exact=True).fill("Hollow")
    assert search.value.status == 200
    expect(page.locator("#games .game-card")).to_have_count(1)
    page.locator("#games .game-card button").first.click()
    expect(page.locator("#game-dialog")).to_be_visible()
    expect(page.locator("#detail-title")).to_have_text("Hollow Ascent")
    assert page.locator("script[src*='demo-api']").count() == 0
