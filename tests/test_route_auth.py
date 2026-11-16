"""Every state-changing route refuses a caller with no session.

Walks the real route table, so a new write route that forgets its auth
dependency fails here without anyone having to remember to add a test.
"""
import re

from tests.test_web_app import web  # noqa: F401  (fixture)

from app.main import app

# Routes that exist to START a session, or end one, and are guarded otherwise
# (Origin, provider tokens, one-time state).
PUBLIC_WRITES = {("POST", "/auth/clerk/session"), ("POST", "/auth/logout"), ("POST", "/demo/login")}


def write_routes():
    # The OpenAPI document is FastAPI's public view of every included route.
    for path, operations in app.openapi()["paths"].items():
        for method in operations:
            method = method.upper()
            if method in {"POST", "PUT", "PATCH", "DELETE"} and (method, path) not in PUBLIC_WRITES:
                yield method, re.sub(r"\{[^}]+\}", "1", path)


def test_route_table_has_writes():
    assert len(list(write_routes())) > 15


def test_no_write_route_accepts_an_anonymous_caller(web):  # noqa: F811
    allowed = []
    for method, path in write_routes():
        response = web.request(method, path, json={}, headers={"Origin": "http://localhost:8000"})
        if response.status_code < 400 or response.status_code >= 500:
            allowed.append((method, path, response.status_code))
    assert allowed == []
