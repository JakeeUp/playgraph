"""An in-process stand-in for the Redis calls the app makes, for demo mode only.

The demo runs as ONE machine with no Redis and no worker. Sessions, rate
limits and the public cache all live here, so a restart signs everyone out and
a second machine would not share any of it. That is acceptable for a demo of
synthetic data and wrong for anything else, which is why main.py only builds
this when DEMO_MODE is on.
"""

import math
import time

MAX_KEYS = 50000


class MemoryStore:
    def __init__(self):
        self.data: dict[str, bytes] = {}
        self.expires: dict[str, float] = {}

    def _read(self, key):
        if self.expires.get(key, math.inf) <= time.monotonic():
            self.data.pop(key, None)
            self.expires.pop(key, None)
        return self.data.get(key)

    def _write(self, key, value, ex=None):
        if len(self.data) >= MAX_KEYS and key not in self.data:
            now = time.monotonic()
            for name in [n for n, t in self.expires.items() if t <= now]:
                self.data.pop(name, None)
                self.expires.pop(name, None)
            while len(self.data) >= MAX_KEYS:  # still full: drop the oldest entries
                oldest = next(iter(self.data))
                self.data.pop(oldest)
                self.expires.pop(oldest, None)
        self.data[key] = value if isinstance(value, bytes) else str(value).encode()
        if ex is not None:
            self.expires[key] = time.monotonic() + ex
        else:
            self.expires.pop(key, None)

    async def get(self, key):
        return self._read(key)

    async def set(self, key, value, ex=None, nx=False):
        if nx and self._read(key) is not None:
            return None
        self._write(key, value, ex)
        return True

    async def getdel(self, key):
        value = self._read(key)
        self.data.pop(key, None)
        self.expires.pop(key, None)
        return value

    async def delete(self, key):
        existed = self._read(key) is not None
        self.data.pop(key, None)
        self.expires.pop(key, None)
        return int(existed)

    async def incr(self, key):
        value = int(self._read(key) or 0) + 1
        ttl = self.expires.get(key)
        self._write(key, str(value))
        if ttl is not None:
            self.expires[key] = ttl
        return value

    async def eval(self, script, key_count, key, seconds, limit):
        """The sliding-window rate script from app.security, on this process's clock."""
        window, limit = int(seconds), int(limit)
        now = int(time.time())
        slot, elapsed = divmod(now, window)
        current = f"{key}:{slot}"
        carried = int(self._read(f"{key}:{slot - 1}") or 0) * (window - elapsed) // window
        count = int(self._read(current) or 0)
        if carried + count >= limit:
            return [0, window - elapsed, 1]
        count += 1
        ttl = self.expires.get(current)
        self._write(current, str(count))
        self.expires[current] = ttl if ttl is not None else time.monotonic() + window * 2
        return [limit - carried - count, window - elapsed, 0]

    async def aclose(self):
        self.data.clear()
        self.expires.clear()
