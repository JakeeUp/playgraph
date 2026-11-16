import base64
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def fly_private_host(host: str | None) -> bool:
    """Hosts reachable only over Fly's private network.

    Fly's Upstash Redis answers only plain redis:// there, with no TLS. That
    network is WireGuard-encrypted between machines, so the traffic is still
    encrypted in transit, just not by Redis. Anything else needs rediss://.
    """
    return bool(host) and bool(re.fullmatch(r"fly-[a-z0-9-]+\.upstash\.io|[a-z0-9.-]+\.(internal|flycast)", host))


class Settings(BaseSettings):
    """Central app config, loaded from environment variables / .env.

    Using pydantic-settings instead of raw os.environ so config is validated
    at startup. The app fails fast with a clear error if something required
    (like STEAM_API_KEY) is missing, instead of failing later mid-request.
    """

    model_config = SettingsConfigDict(env_file=(".env.clerk", ".env"), extra="ignore", hide_input_in_errors=True)

    # Required, except in demo mode where no Steam call is ever made.
    steam_api_key: SecretStr = SecretStr("")
    app_base_url: str = "http://localhost:8000"
    jwt_secret: SecretStr
    # Deployed connection URLs carry passwords, so they get the same masking as
    # the keys: hidden from repr, str and model_dump(), unwrapped only at use.
    database_url: SecretStr
    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")
    environment: Literal["development", "production"] = "development"
    session_minutes: int = Field(default=30, ge=5, le=60)
    # A read-only instance holding only synthetic data. Real sign-in, linking,
    # external provider calls, writes, Redis and the worker are all off.
    demo_mode: bool = False
    clerk_enabled: bool = False
    clerk_publishable_key: str = Field(default="", repr=False)
    clerk_secret_key: SecretStr = SecretStr("")
    # Optional. Without it the For You page says news isn't set up yet.
    apitube_api_key: SecretStr = SecretStr("")
    # Optional. The NPSSO of ONE server-owned PlayStation account, used to read
    # players' public PSN profiles and trophies. Empty turns PlayStation off.
    # Users never hand PlayGraph their own PSN credentials or tokens.
    psn_npsso: SecretStr = SecretStr("")
    # Sony request pacing, per process: at most psn_request_budget requests per
    # rolling psn_budget_window_seconds, each start at least
    # psn_min_request_interval seconds apart. PSNAWP self-limits to 300/15 min.
    # An existing PSN_MIN_REQUEST_INTERVAL setting is still honored as the gap.
    psn_min_request_interval: float = Field(default=0.5, ge=0.0, le=60.0)
    psn_request_budget: int = Field(default=200, ge=1, le=300)
    psn_budget_window_seconds: float = Field(default=900.0, ge=60.0, le=3600.0)
    # Optional. A Twitch developer application's Client ID and Secret, used for
    # IGDB game metadata and search. Both empty turns IGDB off.
    igdb_client_id: str = Field(default="", repr=False)
    igdb_client_secret: SecretStr = SecretStr("")

    # Optional. Encrypted off-site backups to Amazon S3, see docs/AWS_BACKUPS.md.
    # An empty bucket turns them off. AWS credentials are never settings: boto3
    # reads them from the standard chain (environment, profile or role).
    # Retention is an S3 lifecycle rule on the bucket, not code.
    aws_backup_bucket: str = ""
    aws_region: str = ""
    aws_backup_kms_key_id: str = ""

    @property
    def aws_backups_enabled(self) -> bool:
        return not self.demo_mode and bool(self.aws_backup_bucket.strip())

    # Private beta. When on, only these Steam IDs (SteamID64) or verified
    # Clerk primary emails may create a NEW user. Comma separated. Existing
    # users are never locked out by this list.
    beta_invite_only: bool = False
    beta_steam_ids: str = Field(default="", repr=False)
    beta_emails: str = Field(default="", repr=False)
    # User IDs (comma separated) that can no longer sign in or act.
    suspended_user_ids: str = ""

    # Moderation. Comma-separated PlayGraph user IDs that may read and close
    # player reports. Empty means nobody, which is the default.
    owner_user_ids: str = ""

    @property
    def owners(self) -> frozenset[int]:
        return frozenset(int(part) for part in self.owner_user_ids.split(",") if part.strip().isdigit())

    @property
    def igdb_enabled(self) -> bool:
        return not self.demo_mode and bool(self.igdb_client_id.strip() and self.igdb_client_secret.get_secret_value().strip())

    @property
    def psn_enabled(self) -> bool:
        """Safe to show anyone: whether a server NPSSO is set, never its value."""
        return not self.demo_mode and bool(self.psn_npsso.get_secret_value().strip())

    @property
    def clerk_origin(self) -> str:
        """Only a validated configuration key determines the trusted issuer."""
        key = self.clerk_publishable_key
        if not re.fullmatch(r"pk_(test|live)_[A-Za-z0-9+/=_-]+", key):
            raise ValueError("Invalid Clerk publishable key")
        try:
            encoded = key.split("_", 2)[2]
            host = base64.b64decode(encoded + "=" * (-len(encoded) % 4), validate=True).decode()
        except (ValueError, UnicodeError):
            raise ValueError("Invalid Clerk publishable key") from None
        if not host.endswith("$") or not re.fullmatch(r"[a-z0-9]+(?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}\$", host):
            raise ValueError("Invalid Clerk frontend domain")
        return "https://" + host[:-1]

    @model_validator(mode="after")
    def demo_configuration(self):
        """Demo mode drops every provider credential, even one inherited from a .env file."""
        if self.demo_mode:
            self.clerk_enabled = False
            self.clerk_publishable_key = ""
            self.clerk_secret_key = SecretStr("")
            self.psn_npsso = SecretStr("")
            self.igdb_client_id = ""
            self.igdb_client_secret = SecretStr("")
            self.apitube_api_key = SecretStr("")
            self.steam_api_key = SecretStr("")
        elif not self.steam_api_key.get_secret_value():
            raise ValueError("STEAM_API_KEY is required")
        return self

    @model_validator(mode="after")
    def clerk_configuration(self):
        if self.clerk_enabled:
            _ = self.clerk_origin
            mode = "live" if self.environment == "production" else "test"
            if (not self.clerk_publishable_key.startswith(f"pk_{mode}_")
                    or not self.clerk_secret_key.get_secret_value().startswith(f"sk_{mode}_")):
                raise ValueError("Clerk keys must match the application environment")
        return self

    @field_validator("jwt_secret")
    @classmethod
    def strong_signing_key(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value().encode()) < 32:
            raise ValueError("JWT_SECRET must contain at least 32 random bytes of text")
        return value

    @field_validator("app_base_url")
    @classmethod
    def origin_only(cls, value: str) -> str:
        value = value.rstrip("/")
        url = urlsplit(value)
        if (url.scheme not in {"http", "https"} or not url.hostname or url.username
                or url.password or url.path or url.query or url.fragment):
            raise ValueError("APP_BASE_URL must be an HTTP(S) origin without a path")
        if url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Non-local APP_BASE_URL requires HTTPS")
        return value

    @field_validator("redis_url")
    @classmethod
    def valid_redis(cls, value: SecretStr) -> SecretStr:
        if urlsplit(value.get_secret_value()).scheme not in {"redis", "rediss"}:
            raise ValueError("REDIS_URL must use redis:// or rediss://")
        return value

    @model_validator(mode="after")
    def production_transport(self):
        if self.environment == "production":
            if not self.app_base_url.startswith("https://"):
                raise ValueError("Production APP_BASE_URL requires HTTPS")
            redis = urlsplit(self.redis_url.get_secret_value())
            if self.demo_mode:
                return self  # The demo keeps its short-lived state in memory, no Redis.
            if not redis.password or not (redis.scheme == "rediss" or fly_private_host(redis.hostname)):
                raise ValueError("Production Redis requires TLS and authentication")
            # Real users only through the invite list until the PHASES.md
            # release gates are met. Turning this off is a code change, not a flag.
            if not self.beta_invite_only:
                raise ValueError("A production instance with real users must run with BETA_INVITE_ONLY=true")
        elif self.app_base_url.startswith("https://"):
            raise ValueError("An HTTPS APP_BASE_URL requires ENVIRONMENT=production")
        return self


settings = Settings()
