import json
import os
from functools import lru_cache
from ipaddress import ip_network
from typing import Annotated

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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
    # OAuth2 client credentials (app/security.py). JSON of client_id -> SHA-256 hex of its client_secret,
    # e.g. AUTH_CLIENTS={"partner": "<64 hex>"}; create one with `python -m app.security new-client partner`.
    # Required outside APP_ENV=local (the API will not start without it and JWT_SECRET).
    auth_clients: Annotated[dict[str, str], NoDecode] = {}
    # HS256 signing key for access tokens, 32+ characters (`python -m app.security new-jwt-secret`).
    jwt_secret: SecretStr | None = None
    # The old signing key while rotating; tokens it signed stay valid until they expire.
    jwt_previous_secret: SecretStr | None = None
    # Access token lifetime.
    jwt_ttl_seconds: int = 3600
    # Interactive docs at /docs and /openapi.json. Consider turning off in production.
    docs_enabled: bool = True
    # Per-IP rate limit (app/ratelimit.py): requests per second from one client IP before 429.
    # Counted in Redis when REDIS_URL is set (across workers), else per worker.
    rate_limit_enabled: bool = True
    rate_limit_per_second: int = 2500
    # POST /v1/auth/token per client IP per minute, on top of the above. A client needs one token an hour.
    token_rate_limit_per_minute: int = 30
    # Proxies (nginx, load balancer) whose X-Forwarded-For is believed, as IPs or CIDRs separated by
    # commas, e.g. TRUSTED_PROXIES=10.0.0.5,172.18.0.0/16. Empty: X-Forwarded-For is ignored and the
    # client IP is the connection's address. Start the API with `python -m app` (app/__main__.py),
    # which turns off uvicorn's own X-Forwarded-For handling so this setting decides.
    trusted_proxies: Annotated[list[str], NoDecode] = []
    # Requests only carry a dealer code, so anything bigger is rejected (413).
    max_body_bytes: int = 4096
    # The loader's default month (`ingestion.run schemes` without --year/--month) is the current
    # month in this timezone, not the server's. The API always takes the month from the request.
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


    @field_validator("auth_clients", mode="before")
    @classmethod
    def _parse_auth_clients(cls, value):
        """JSON from the environment; an empty AUTH_CLIENTS= means no clients rather than a parse error."""
        if isinstance(value, str):
            return json.loads(value) if value.strip() else {}
        return value

    @field_validator("trusted_proxies", mode="before")
    @classmethod
    def _parse_trusted_proxies(cls, value):
        """Comma-separated IPs/CIDRs; each must be valid, so a typo stops the API instead of trusting nothing."""
        if isinstance(value, str):
            value = [part.strip() for part in value.split(",") if part.strip()]
        for entry in value:
            ip_network(entry, strict=False)  # ValueError names the bad entry
        return value

    @field_validator("jwt_secret", "jwt_previous_secret", mode="before")
    @classmethod
    def _empty_secret_is_unset(cls, value):
        return None if isinstance(value, str) and not value.strip() else value


@lru_cache
def get_settings() -> Settings:
    return Settings()
