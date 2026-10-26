import base64
import binascii
import json
import os
from datetime import datetime
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

    # hide_input_in_errors: a bad secret (GCS_CREDENTIALS_JSON, JWT_SECRET) must not be printed in the
    # start-up error; the message still names the setting.
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore", hide_input_in_errors=True)

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
    # Also write the log to daily files (app/logfile.py): /var/log/scheme-api/scheme-api.log
    # gives scheme-api-2026-10-23.log in that folder (a day in TIMEZONE). Empty: console only.
    log_file: str | None = None
    # Days of log files kept, today included; older ones are deleted. 0 keeps every file.
    log_file_keep_days: int = 14

    gcs_bucket: str = "acme-scheme-files"
    # Optional Google Cloud project ID for the GCS client (not needed just to read the bucket).
    gcs_project: str | None = None
    # The GCS service-account key itself, so no key file has to be mounted: the key's JSON file as
    # base64 on one line (`base64 -w0 key.json`; on macOS `base64 -i key.json`), or the JSON on one line.
    # Wins over GOOGLE_APPLICATION_CREDENTIALS (a key file path). Empty: the key file, else the server's
    # own service account.
    gcs_credentials_json: SecretStr | None = None
    scheme_prefix_template: str = "sap-exports/{year}/{month:02d}/"
    # Read files from this local folder instead of GCS (development only).
    local_data_dir: str | None = None

    scheme_header_rows: int = 1
    # Files the loader downloads and parses ahead, in parallel, while it saves the current one.
    loader_workers: int = 4
    # The API runs the loader on a schedule (app/scheduler.py), so no separate cron job is needed.
    # Unset: on everywhere except APP_ENV=local. Set false where a cron job runs the loader instead.
    loader_schedule_enabled: bool | None = None
    # Daily run time, HH:MM in TIMEZONE. SAP's files land around 04:30-04:51 India time.
    loader_daily_at: str = "05:00"
    # A failed run is tried again this many times, this many minutes apart.
    loader_retries: int = 3
    loader_retry_minutes: int = 30
    # A run taking longer is stopped (and counts as failed); a retry carries on from the files loaded.
    loader_timeout_minutes: int = 120
    # In the first days of a month the previous month is loaded too (late files of its last day).
    loader_catch_up_days: int = 3
    # Months of schemes kept, the current one included: 3 in October keeps October, September and
    # August. A scheme goes once its period ended before the oldest kept month. 0 keeps everything.
    loader_retention_months: int = 3

    @property
    def loader_schedule_on(self) -> bool:
        return APP_ENV != "local" if self.loader_schedule_enabled is None else self.loader_schedule_enabled

    @property
    def gcs_credentials_info(self) -> dict | None:
        """GCS_CREDENTIALS_JSON as the key's JSON object, or None when it is not set."""
        return None if self.gcs_credentials_json is None else _service_account_info(
            self.gcs_credentials_json.get_secret_value())


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


    @field_validator("loader_schedule_enabled", mode="before")
    @classmethod
    def _empty_is_default(cls, value):
        """LOADER_SCHEDULE_ENABLED= (as in .env.example) means the default, not an invalid boolean."""
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("loader_daily_at")
    @classmethod
    def _parse_daily_at(cls, value: str) -> str:
        """HH:MM, checked at start-up so a typo stops the API instead of silently never loading."""
        parsed = datetime.strptime(value.strip(), "%H:%M").time()
        return f"{parsed.hour:02d}:{parsed.minute:02d}"

    @field_validator("jwt_secret", "jwt_previous_secret", "gcs_credentials_json", mode="before")
    @classmethod
    def _empty_secret_is_unset(cls, value):
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("gcs_credentials_json")
    @classmethod
    def _check_gcs_credentials(cls, value: SecretStr | None) -> SecretStr | None:
        """Checked at start-up so a bad key stops the API, not the 05:00 load."""
        if value is not None:
            _service_account_info(value.get_secret_value())
        return value


def _service_account_info(text: str) -> dict:
    """The key's JSON object from base64 or plain JSON; ValueError (without the key) if it is neither."""
    text = text.strip()
    try:
        info = json.loads(text if text.startswith("{") else base64.b64decode(text, validate=True))
    except (ValueError, binascii.Error):
        raise ValueError("GCS_CREDENTIALS_JSON is neither base64 of the key file nor its JSON") from None
    if not isinstance(info, dict) or not {"client_email", "private_key"} <= info.keys():
        raise ValueError("GCS_CREDENTIALS_JSON is not a service-account key (client_email, private_key missing)")
    return info


@lru_cache
def get_settings() -> Settings:
    return Settings()
