"""Redis payloads are data, never executable Python objects."""

import json

QUEUE_NAME = "playgraph:queue:json-v1"


def serialize(data: dict) -> bytes:
    if isinstance(data.get("r"), BaseException):
        data = {**data, "r": {"status": "error", "detail": "Sync failed"}}
    return json.dumps(data, allow_nan=False).encode()


def deserialize(data: bytes) -> dict:
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("Queue payload must be an object")
    return value
