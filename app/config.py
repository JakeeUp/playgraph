from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central app config, loaded from environment variables / .env.

    Using pydantic-settings instead of raw os.environ so config is validated
    at startup. The app fails fast with a clear error if something required
    (like STEAM_API_KEY) is missing, instead of failing later mid-request.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    steam_api_key: SecretStr
    app_base_url: str = "http://localhost:8000"
    jwt_secret: SecretStr
    # Deployed connection URLs carry passwords, so they get the same masking as
    # the keys: hidden from repr, str and model_dump(), unwrapped only at use.
    database_url: SecretStr
    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")
    environment: Literal["development", "production"] = "development"
    session_minutes: int = Field(default=30, ge=5, le=60)

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
            if redis.scheme != "rediss" or not redis.password:
                raise ValueError("Production Redis requires TLS and authentication")
        return self


settings = Settings()
