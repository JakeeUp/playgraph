from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central app config, loaded from environment variables / .env.

    Using pydantic-settings instead of raw os.environ so config is validated
    at startup — the app fails fast with a clear error if something required
    (like STEAM_API_KEY) is missing, instead of failing later mid-request.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    steam_api_key: str
    app_base_url: str = "http://localhost:8000"
    jwt_secret: str
    database_url: str
    redis_url: str = "redis://localhost:6379/0"


settings = Settings()
