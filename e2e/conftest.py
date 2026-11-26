"""End-to-end browser tests: the real front end in real Chromium.

They run against the demo build (demo/build.py): the app's own pages and
scripts with sample data and a stand-in API that answers in the browser. It
needs no Steam key, database, Redis or network, so every push can run the same
user journeys the same way. The fast suite in tests/ covers the server.

    python -m pip install -r requirements-e2e.txt
    python -m playwright install chromium
    python -m pytest e2e -q
"""
import functools
import http.server
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def site(tmp_path_factory):
    """Build the demo once and serve it on a free local port."""
    out = tmp_path_factory.mktemp("demo") / "site"
    subprocess.run([sys.executable, str(ROOT / "demo" / "build.py"), str(out), "--base", "/"],
                   check=True, capture_output=True)
    handler = functools.partial(QuietHandler, directory=str(out))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    thread.join()


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as playwright:
        chromium = playwright.chromium.launch()
        yield chromium
        chromium.close()


class Page:
    """A fresh browser tab that records script errors and HTTP failures.

    Each test gets a new context, so the demo's tab storage starts clean.
    Steam's image CDN is answered with a blank image: the tests check the
    app, not Steam, and must not fail when the runner is offline.
    """

    def __init__(self, browser, site, width, height, touch):
        self.site = site
        self.context = browser.new_context(viewport={"width": width, "height": height}, has_touch=touch)
        self.context.route("https://*.steamstatic.com/**", lambda route: route.fulfill(
            status=200, content_type="image/gif",
            body=b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,"
                 b"\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"))
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(f"page error: {error}"))
        self.page.on("console", lambda message: self.errors.append(f"console: {message.text}")
                     if message.type == "error" else None)
        self.page.on("response", lambda response: self.errors.append(f"{response.status} {response.url}")
                     if response.status >= 400 and response.url.startswith(site) else None)
        self.page.on("requestfailed", lambda request: self.errors.append(f"{request.failure} {request.url}")
                     if request.url.startswith(site) else None)

    def open(self, fragment=""):
        self.page.goto(f"{self.site}/index.html{fragment}")
        self.page.wait_for_selector("#page-loading", state="hidden")
        return self.page


@pytest.fixture
def app(browser, site):
    tab = Page(browser, site, 1440, 900, False)
    yield tab
    tab.context.close()
    assert tab.errors == [], tab.errors


@pytest.fixture
def phone(browser, site):
    tab = Page(browser, site, 390, 844, True)
    yield tab
    tab.context.close()
    assert tab.errors == [], tab.errors
