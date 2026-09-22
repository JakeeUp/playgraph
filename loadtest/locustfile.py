"""Load test for PlayGraph's public read traffic.

Point it at a local or staging instance, never at production unannounced:

    pip install -r requirements-loadtest.txt
    locust -f loadtest/locustfile.py --host http://localhost:8000

Each simulated visitor gets a stable address from 198.18.0.0/15, the range
reserved for benchmarking (RFC 2544), sent as X-Forwarded-For. The server must
trust the load generator as its proxy, for example by starting it with
FORWARDED_ALLOW_IPS=127.0.0.1, so every visitor gets its own rate-limit bucket
the way real visitors do behind a TLS proxy. Without that, all visitors share
one address and the per-address limit correctly turns most of the load into 429s.

Signed-in traffic is not covered: it needs real Steam sessions.
"""

import ipaddress
import itertools
import random
import string

from locust import HttpUser, between, task

BENCHMARK_RANGE = ipaddress.ip_network("198.18.0.0/15")
_next_address = itertools.count(1)


class Visitor(HttpUser):
    """An anonymous visitor browsing the catalog, game reviews and the public feed."""

    wait_time = between(1, 4)

    def on_start(self):
        self.client.headers["X-Forwarded-For"] = str(BENCHMARK_RANGE[next(_next_address)])
        games = self.client.get("/games?limit=48", name="/games").json().get("games", [])
        self.game_ids = [game["id"] for game in games] or [1]
        items = self.client.get("/feed?limit=20", name="/feed").json().get("items", [])
        self.review_ids = [item["review"]["id"] for item in items]

    @task(1)
    def open_app(self):
        self.client.get("/app", name="/app")

    @task(3)
    def browse_catalog(self):
        self.client.get(f"/games?limit=48&offset={random.choice([0, 0, 48])}", name="/games")

    @task(2)
    def search_catalog(self):
        self.client.get(f"/games?q={random.choice(string.ascii_lowercase)}", name="/games?q=")

    @task(2)
    def game_details(self):
        self.client.get(f"/games/{random.choice(self.game_ids)}", name="/games/{id}")

    @task(4)
    def game_reviews(self):
        self.client.get(f"/games/{random.choice(self.game_ids)}/reviews", name="/games/{id}/reviews")

    @task(3)
    def latest_reviews(self):
        self.client.get("/feed?limit=20", name="/feed")

    @task(1)
    def review_discussion(self):
        if self.review_ids:
            self.client.get(f"/reviews/{random.choice(self.review_ids)}/comments", name="/reviews/{id}/comments")
