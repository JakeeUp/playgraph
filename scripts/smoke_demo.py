"""Smoke-check a deployed demo instance over plain HTTP.

    python -m scripts.smoke_demo https://demo.example

Stdlib only. Each check prints PASS or FAIL. The exit code is 0 only when
every check passes. The script never prints cookie or CSRF token values.

The browser-only checks use the same headers the real sign-in page sends:
the Origin must equal the base URL, and X-PlayGraph-Auth must be 1.
"""

import json
import sys
import urllib.error
import urllib.request

TIMEOUT = 15
DEMO_ERROR = "The demo is read-only."


class NoRedirects(urllib.request.HTTPRedirectHandler):
    """Report a 3xx as it is, so a redirect is a checked result, not a followed one."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(NoRedirects)


class Failures:
    count = 0


def report(name, ok, detail=""):
    if not ok:
        Failures.count += 1
    suffix = f"  ({detail})" if detail else ""
    print(f"{'PASS' if ok else 'FAIL'}  {name}{suffix}")
    return ok


def call(method, url, headers=None, body=None):
    request = urllib.request.Request(url, method=method, headers=headers or {}, data=body)
    try:
        with OPENER.open(request, timeout=TIMEOUT) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read()


def json_body(raw):
    try:
        return json.loads(raw)
    except ValueError:
        return None


def session_cookie(headers, name):
    """Return (value, attributes) for the named Set-Cookie, or (None, set())."""
    for raw in headers.get_all("Set-Cookie") or []:
        first, *attributes = [part.strip() for part in raw.split(";")]
        cookie_name, _, value = first.partition("=")
        if cookie_name == name and value:
            return value, {part.split("=")[0].lower() for part in attributes}
    return None, set()


def main(argv):
    if len(argv) != 2 or not argv[1].startswith(("http://", "https://")):
        print("usage: python -m scripts.smoke_demo BASE_URL   (for example https://demo.example)", file=sys.stderr)
        return 2
    base = argv[1].rstrip("/")
    secure = base.startswith("https://")
    # Deployed HTTPS gets the __Host- cookie. Local HTTP cannot, by design (app/security.py).
    cookie_name = "__Host-playgraph-session" if secure else "playgraph-session"
    browser = {"Origin": base, "X-PlayGraph-Auth": "1"}

    try:
        status, _, raw = call("GET", f"{base}/health")
        report("GET /health is 200", status == 200 and json_body(raw) == {"status": "ok"}, f"status {status}")

        status, headers, _ = call("GET", f"{base}/app")
        csp = headers.get("Content-Security-Policy", "")
        report("GET /app is 200 with a Content-Security-Policy", status == 200 and "default-src 'none'" in csp,
               f"status {status}")

        status, _, raw = call("GET", f"{base}/auth/config")
        report("GET /auth/config reports demo true", status == 200 and (json_body(raw) or {}).get("demo") is True,
               f"status {status}")

        status, headers, _ = call("POST", f"{base}/demo/login", headers=browser)
        value, attributes = session_cookie(headers, cookie_name)
        cookie_ok = value is not None and (not secure or {"secure", "path"} <= attributes)
        report("POST /demo/login is 204 and sets the session cookie",
               status == 204 and cookie_ok, f"status {status}, cookie {'set' if value else 'missing'}")
        if not cookie_ok:
            # Without a session, the checks below cannot mean anything.
            print("FAIL  remaining signed-in checks skipped: no session cookie")
            Failures.count += 1
            return finish()

        auth = {"Cookie": f"{cookie_name}={value}"}
        status, _, raw = call("GET", f"{base}/auth/session", headers=auth)
        session = json_body(raw) or {}
        report("GET /auth/session is 200 for the demo player",
               status == 200 and session.get("user", {}).get("display_name") == "Demo Player",
               f"status {status}")
        csrf = session.get("csrf_token", "")

        status, _, raw = call("GET", f"{base}/me/library", headers=auth)
        items = json_body(raw)
        report("GET /me/library returns items", status == 200 and isinstance(items, list) and len(items) > 0,
               f"status {status}, {len(items) if isinstance(items, list) else 0} items")

        status, _, raw = call("POST", f"{base}/games/1/reviews", body=json.dumps({"rating": 5, "body": "smoke"}).encode(),
                              headers={**auth, **browser, "Content-Type": "application/json", "X-CSRF-Token": csrf})
        detail = (json_body(raw) or {}).get("detail")
        report("POST a review is refused as read-only (403)", status == 403 and detail == DEMO_ERROR,
               f"status {status}")

        status, _, _ = call("GET", f"{base}/auth/steam/login")
        report("GET /auth/steam/login is 404 on the demo", status == 404, f"status {status}")

        status, _, _ = call("POST", f"{base}/auth/logout", headers={**auth, **browser, "X-CSRF-Token": csrf})
        report("POST /auth/logout is 204", status == 204, f"status {status}")

        status, _, _ = call("GET", f"{base}/auth/session", headers=auth)
        report("the session is gone after logout (401)", status == 401, f"status {status}")
    except (urllib.error.URLError, OSError) as error:
        print(f"FAIL  could not reach {base}: {error.__class__.__name__}")
        Failures.count += 1
    return finish()


def finish():
    total_failed = Failures.count
    print("All checks passed." if total_failed == 0 else f"{total_failed} check(s) failed.")
    return 0 if total_failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
