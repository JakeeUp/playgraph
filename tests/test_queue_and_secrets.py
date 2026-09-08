import pickle
from unittest.mock import AsyncMock

import httpx
import pytest
from arq.jobs import deserialize_job, deserialize_result, serialize_job, serialize_result

from app.config import settings
from app.queue_codec import deserialize, serialize
from app.services import steam


def test_queue_job_and_result_roundtrip():
    payload = serialize_job("sync_steam_library", (1,), {}, None, 1000, serializer=serialize)
    assert payload.startswith(b"{")
    job = deserialize_job(payload, deserializer=deserialize)
    assert job.function == "sync_steam_library" and job.args == [1] and job.kwargs == {}
    result = serialize_result("sync_steam_library", (1,), {}, 1, 1000, True,
                              {"games_synced": 4}, 1001, 1002, "ref", "queue", "job", serializer=serialize)
    assert deserialize_result(result, deserializer=deserialize).result == {"games_synced": 4}


def test_queue_exception_is_redacted_and_pickle_is_rejected():
    payload = serialize_result("sync_steam_library", (1,), {}, 1, 1000, False,
                               ValueError("sensitive connection string"), 1001, 1002,
                               "ref", "queue", "job", serializer=serialize)
    result = deserialize_result(payload, deserializer=deserialize)
    assert result.success is False
    assert "sensitive" not in payload.decode()
    with pytest.raises((ValueError, UnicodeDecodeError)):
        deserialize(pickle.dumps({"f": "sync_steam_library"}))


@pytest.mark.asyncio
async def test_steam_key_is_only_sent_in_webapi_header(monkeypatch):
    requests = []
    real_client = httpx.AsyncClient

    async def handle(request):
        requests.append(request)
        if request.url.host == "store.steampowered.com":
            return httpx.Response(200, json={"10": {"success": False}})
        return httpx.Response(200, json={"response": {}, "playerstats": {"success": False}})

    monkeypatch.setattr(steam.httpx, "AsyncClient", lambda **kwargs: real_client(
        transport=httpx.MockTransport(handle), **kwargs))
    await steam.get_owned_games("76561198000000001")
    await steam.get_player_achievements("76561198000000001", 10)
    await steam.get_player_summary("76561198000000001")
    await steam.get_app_genres(10)
    assert len(requests) == 4
    for request in requests:
        assert "key" not in request.url.params
        assert settings.steam_api_key.get_secret_value() not in str(request.url)
        if request.url.host == "api.steampowered.com":
            assert request.headers["x-webapi-key"] == settings.steam_api_key.get_secret_value()
        else:
            assert "x-webapi-key" not in request.headers
