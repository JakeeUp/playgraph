"""Rate-limit identities: IPv6 by /64 so one customer cannot rotate addresses."""
from types import SimpleNamespace

import pytest

from app.security import client_key


def key(host):
    return client_key(SimpleNamespace(client=SimpleNamespace(host=host) if host else None))


@pytest.mark.parametrize("host,expected", [
    ("203.0.113.7", "203.0.113.7"),
    ("2001:db8:1:2:aaaa::1", "2001:db8:1:2::/64"),
    ("2001:db8:1:2:ffff:ffff:ffff:ffff", "2001:db8:1:2::/64"),
    ("::ffff:203.0.113.7", "203.0.113.7"),
    ("testclient", "testclient"),
    (None, "unknown"),
])
def test_client_key(host, expected):
    assert key(host) == expected


def test_neighbouring_ipv6_networks_stay_separate():
    assert key("2001:db8:1:2::1") != key("2001:db8:1:3::1")
