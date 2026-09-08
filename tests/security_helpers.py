"""A small expiring store double. Production Redis behavior is tested separately."""

import time
from datetime import timedelta
import secrets

import jwt

from app.config import settings
from app.models import utcnow
from app.security import AUDIENCE, ISSUER, state_key


class MemoryRedis:
    def __init__(self):
        self.data = {}
        self.expires = {}
        self.now = time.monotonic()

    def read(self, key):
        if self.expires.get(key, float("inf")) <= self.now:
            self.data.pop(key, None)
        return self.data.get(key)

    async def get(self, key):
        return self.read(key)

    async def set(self, key, value, ex=None, nx=False):
        if nx and self.read(key) is not None:
            return None
        self.data[key] = str(value).encode() if not isinstance(value, bytes) else value
        if ex is not None:
            self.expires[key] = self.now + ex
        return True

    async def getdel(self, key):
        value = self.read(key)
        self.data.pop(key, None)
        return value

    async def delete(self, key):
        return int(self.data.pop(key, None) is not None)

    async def eval(self, script, key_count, key, seconds):
        count = int(self.read(key) or 0) + 1
        self.data[key] = str(count).encode()
        if count == 1:
            self.expires[key] = self.now + seconds
        return count, int(self.expires[key] - self.now)


def auth_headers(store, user_id=1, **overrides):
    now = utcnow()
    session_id = secrets.token_urlsafe(32)
    claims = {"sub": str(user_id), "iat": now, "nbf": now,
              "exp": now + timedelta(minutes=settings.session_minutes),
              "iss": ISSUER, "aud": AUDIENCE, "jti": session_id, **overrides}
    key = state_key("session", session_id)
    store.data[key] = str(user_id).encode()
    store.expires[key] = store.now + settings.session_minutes * 60
    token = jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}
