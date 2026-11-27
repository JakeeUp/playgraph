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
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    setattr(item, f"report_{report.when}", report)
    return report


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
        self.context.tracing.start(screenshots=True, snapshots=True, sources=True)
        self.page = self.context.new_page()
        self.errors = []
        self.expected_http_errors = set()
        self.page.on("pageerror", lambda error: self.errors.append(f"page error: {error}"))
        self.page.on("console", self.console_message)
        self.page.on("response", lambda response: self.errors.append(f"{response.status} {response.url}")
                     if response.status >= 400 and response.url.startswith(site) and
                     (response.status, response.url) not in self.expected_http_errors else None)
        self.page.on("requestfailed", lambda request: self.errors.append(f"{request.failure} {request.url}")
                     if request.url.startswith(site) else None)

    def console_message(self, message):
        if message.type != "error":
            return
        status = re.fullmatch(r"Failed to load resource: the server responded with a status of (\d+) \(.*\)", message.text)
        if status and (int(status[1]), message.location.get("url")) in self.expected_http_errors:
            return
        self.errors.append(f"console: {message.text}")

    def open(self, fragment=""):
        self.page.goto(f"{self.site}/index.html{fragment}")
        self.page.wait_for_selector("#page-loading", state="hidden")
        return self.page


def finish(tab, request):
    report = getattr(request.node, "report_call", getattr(request.node, "report_setup", None))
    failed = bool(tab.errors or report and report.failed)
    folder = ROOT / "test-results" / re.sub(r"[^a-zA-Z0-9_.-]", "_", request.node.nodeid)
    try:
        if failed:
            folder.mkdir(parents=True, exist_ok=True)
            try:
                if not tab.page.is_closed():
                    tab.page.screenshot(path=folder / "failure.png", full_page=True, timeout=5000)
            finally:
                tab.context.tracing.stop(path=folder / "trace.zip")
            print(f"Browser failure artifacts: {folder}")
        else:
            tab.context.tracing.stop()
            for name in ("failure.png", "trace.zip"):
                (folder / name).unlink(missing_ok=True)
    finally:
        tab.context.close()
    assert tab.errors == [], tab.errors


@pytest.fixture
def app(browser, site, request):
    tab = Page(browser, site, 1440, 900, False)
    yield tab
    finish(tab, request)


@pytest.fixture
def phone(browser, site, request):
    tab = Page(browser, site, 390, 844, True)
    yield tab
    finish(tab, request)


@pytest.fixture
def backend_app(browser, backend_site, request):
    tab = Page(browser, backend_site, 1440, 900, False)
    # A guest session probe must return 401; every other HTTP failure still fails.
    tab.expected_http_errors.add((401, backend_site + "/auth/session"))
    yield tab
    finish(tab, request)
