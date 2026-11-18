"""The unlisted mock-data demo at /mocktest/: built from the real demo, served
with the app's policy, kept out of search results and away from the real API."""
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import main
from app.middleware import APP_CSP, SecurityMiddleware, security_headers
from tests.security_helpers import MemoryRedis

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("mocktest") / "mocktest"
    subprocess.run([sys.executable, str(ROOT / "demo" / "build.py"), str(out), "--base", "/mocktest/"],
                   check=True, capture_output=True)
    return out


@pytest.fixture
def site(built):
    app = FastAPI()
    app.add_middleware(SecurityMiddleware)
    app.state.arq_pool = MemoryRedis()
    assert main.mount_mocktest(app, built) is True
    return TestClient(app, base_url="http://localhost:8000")


def test_the_demo_is_served_with_its_sample_data_and_stand_in_api(site):
    page = site.get("/mocktest/")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    # Same app, wired to sample data that never leaves the browser.
    assert "assets/demo-data.js" in page.text and "assets/demo-api.js" in page.text
    assert "Sample data" in page.text
    for asset in ("assets/app.js", "assets/demo-api.js", "assets/demo-data.js", "assets/styles.css", "assets/mark.svg"):
        assert site.get("/mocktest/" + asset).status_code == 200
    assert site.get("/mocktest/account.html").status_code == 200


def test_the_bare_path_redirects_to_the_folder(site):
    response = site.get("/mocktest", follow_redirects=False)
    assert response.status_code == 308 and response.headers["location"] == "/mocktest/"


def test_unlisted_pages_are_kept_out_of_search_and_use_the_app_policy(site):
    for path in ("/mocktest/", "/mocktest/assets/app.js"):
        headers = site.get(path).headers
        assert headers["x-robots-tag"] == "noindex, nofollow, noarchive"
        assert headers["content-security-policy"] == APP_CSP
        assert headers["cache-control"] == "no-cache"
    # The real app and API are unchanged.
    assert "X-Robots-Tag" not in security_headers("/app", 200, "text/html")
    assert security_headers("/me/library", 200, "application/json")["Cache-Control"] == "no-store"


def test_missing_files_are_not_found(site):
    assert site.get("/mocktest/nothing-here.js").status_code == 404


def test_a_plain_checkout_has_no_mocktest(tmp_path):
    app = FastAPI()
    assert main.mount_mocktest(app, tmp_path / "missing") is False
    assert TestClient(app).get("/mocktest/").status_code == 404


def test_nothing_on_the_real_site_links_to_it():
    for page in ("index.html", "account.html", "404.html"):
        assert "mocktest" not in (ROOT / "app" / "static" / page).read_text(encoding="utf-8")
    for script in (ROOT / "app" / "static").glob("*.js"):
        assert "mocktest" not in script.read_text(encoding="utf-8"), script.name


def test_the_image_builds_it():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY demo ./demo" in dockerfile and "demo/build.py mocktest --base /mocktest/" in dockerfile


def test_a_page_load_does_not_spend_the_visitor_request_budget(site):
    # One load pulls in about thirty files; counting them would 429 a viewer.
    for _ in range(130):
        response = site.get("/mocktest/assets/styles.css")
        assert response.status_code == 200 and "ratelimit-limit" not in response.headers
