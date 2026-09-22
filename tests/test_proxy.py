"""Exercise the real Uvicorn proxy boundary ahead of application security."""
import httpx
import pytest
from fastapi import FastAPI, Request
from starlette.middleware.trustedhost import TrustedHostMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.config import settings
from app.middleware import SecurityMiddleware
from app.security import state_key
from app.serve import proxy_allowlist, main
from tests.security_helpers import MemoryRedis


@pytest.mark.parametrize("value", ["", " ", "*", "0.0.0.0/0", "::/0", "10.0.0.4,", "proxy.example", "10.0.0.4/24"])
def test_production_requires_explicit_bounded_trust(value):
    with pytest.raises(ValueError):
        proxy_allowlist(value, True)


def test_entrypoint_passes_verified_allowlist(monkeypatch):
    from app import serve
    assert proxy_allowlist("", False) == ""
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "10.0.0.4, fd00:1234::/64")
    captured = {}
    monkeypatch.setattr(serve.uvicorn, "run", lambda app, **kwargs: captured.update(kwargs))
    main()
    assert captured["proxy_headers"] is True
    assert captured["forwarded_allow_ips"] == "10.0.0.4,fd00:1234::/64"
    monkeypatch.delenv("FORWARDED_ALLOW_IPS")
    with pytest.raises(ValueError):
        main()


@pytest.mark.asyncio
async def test_proxy_https_hosts_and_rate_limit_identity(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    app = FastAPI()
    app.state.arq_pool = MemoryRedis()
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["playgraph-demo.fly.dev"])
    app.add_middleware(SecurityMiddleware)

    @app.get("/health")
    @app.get("/probe")
    def probe(request: Request):
        return {"scheme": request.url.scheme, "client": request.client.host}

    wrapped = ProxyHeadersMiddleware(app, trusted_hosts=proxy_allowlist("10.0.0.4", True))
    headers = {"X-Forwarded-Proto": "https", "X-Forwarded-For": "198.51.100.1"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=wrapped, client=("10.0.0.4", 1234)),
                                base_url="http://playgraph-demo.fly.dev") as client:
        response = await client.get("/health", headers=headers)
        assert response.status_code == 200
        assert response.json() == {"scheme": "https", "client": "198.51.100.1"}
        for address in ("198.51.100.1", "198.51.100.2"):
            assert (await client.get("/probe", headers={**headers, "X-Forwarded-For": address})).status_code == 200
            assert app.state.arq_pool.read(state_key("rate:requests", address)) == b"1"
        # An attacker-prepended address cannot override the last untrusted hop.
        response = await client.get("/probe", headers={**headers, "X-Forwarded-For": "203.0.113.99, 198.51.100.2"})
        assert response.json()["client"] == "198.51.100.2"
        assert (await client.get("/health")).status_code == 400
        assert (await client.get("/health", headers={**headers, "Host": "attacker.invalid"})).status_code == 400
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=wrapped, client=("203.0.113.9", 1234)),
                                base_url="http://playgraph-demo.fly.dev") as client:
        assert (await client.get("/health", headers=headers)).status_code == 400
        response = await client.get("https://playgraph-demo.fly.dev/probe", headers=headers)
        assert response.status_code == 200
        assert response.json()["client"] == "203.0.113.9"
