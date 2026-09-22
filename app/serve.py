"""Container entry point: trust only explicitly configured proxy peers."""
import ipaddress
import os

import uvicorn

from app.config import settings


def proxy_allowlist(value: str, production: bool) -> str:
    if not value.strip():
        if production:
            raise ValueError("Set FORWARDED_ALLOW_IPS to the verified TLS proxy peer IPs or CIDRs")
        return ""  # Local clients cannot supply their own HTTPS scheme or IP.
    peers = [peer.strip() for peer in value.split(",")]
    for peer in peers:
        try:
            network = ipaddress.ip_network(peer, strict=True)
        except ValueError:
            raise ValueError("FORWARDED_ALLOW_IPS must contain IPs or canonical CIDRs, never '*'") from None
        if network.prefixlen == 0:
            raise ValueError("FORWARDED_ALLOW_IPS must not trust the entire Internet")
    return ",".join(peers)


def main():
    peers = proxy_allowlist(os.environ.get("FORWARDED_ALLOW_IPS", ""),
                           settings.environment == "production")
    # A container has to listen on every interface to receive its proxy's
    # traffic. The platform network decides exposure, and the peer allowlist
    # above decides whose forwarded headers are believed.
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000,  # nosec B104
                proxy_headers=True, forwarded_allow_ips=peers, server_header=False)


if __name__ == "__main__":
    main()
