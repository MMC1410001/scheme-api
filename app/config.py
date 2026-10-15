import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


# Which settings file to read: .env.local (default), .env.dev, .env.uat or .env.prod.
# Environment variables always win over the file, so a platform can also set everything directly.
APP_ENV = os.environ.get("APP_ENV", "local")
ENV_FILE = f".env.{APP_ENV}"


class Settings(BaseSettings):
    """All configuration comes from environment variables or the APP_ENV's file (ENV_FILE)."""

    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    database_url: str = "postgresql://acme:acme@localhost:5433/acme"
    # Per worker process. Total connections = workers x db_pool_max.
    db_pool_min: int = 2
    db_pool_max: int = 10
    db_pool_timeout: float = 5.0
    # Redis response cache (app/cache.py). On only when CACHE_ENABLED=true and REDIS_URL is set,
    # e.g. redis://localhost:6379/0; CACHE_ENABLED=false turns it off without removing the URL.
    cache_enabled: bool = False
    redis_url: str | None = None
    # Upper bound on how long a cached response lives; new data replaces it sooner (data version).
    cache_ttl_seconds: int = 900
    # Optional. When set, callers must send it in the X-API-Key header.
    api_key: str | None = None
    # Interactive docs at /docs and /openapi.json. Consider turning off in production.
    docs_enabled: bool = True
    # Requests only carry a dealer code, so anything bigger is rejected (413).
    max_body_bytes: int = 4096
    # "Current month" is decided in this timezone, not the server's.
    timezone: str = "Asia/Kolkata"

    gcs_bucket: str = "acme-scheme-files"
    # Optional Google Cloud project ID for the GCS client (not needed just to read the bucket).
    gcs_project: str | None = None
    scheme_prefix_template: str = "sap-exports/{year}/{month:02d}/"
    # Read files from this local folder instead of GCS (development only).
    local_data_dir: str | None = None

    scheme_header_rows: int = 1
    # Files the loader downloads and parses ahead, in parallel, while it saves the current one.
    loader_workers: int = 4


@lru_cache
def get_settings() -> Settings:
    return Settings()
