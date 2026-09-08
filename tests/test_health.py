from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, base_url="http://localhost:8000")


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
