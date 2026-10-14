"""Optional Redis cache for API responses: on when CACHE_ENABLED=true and REDIS_URL is set.

The dealer app can send far more requests than expected (up to 1 lakh a minute), while the data only
changes when the loader runs. So each response's JSON is kept in Redis, keyed by API and request,
and served from there until the loader loads new data.

Fresh data: every key includes a data version, which the loader bumps after loading anything
(bump_data_version). Old keys are then never read again and expire after CACHE_TTL_SECONDS.

Redis is never required: if it is down or slow, requests are answered from Postgres as usual, and
Redis is not tried again for RETRY_AFTER_SECONDS so a dead Redis doesn't slow every request.
"""
import logging
import time
from collections.abc import Callable

import redis

log = logging.getLogger("app.cache")

KEY_PREFIX = "acme:v1"
VERSION_KEY = "acme:data-version"
TIMEOUT_SECONDS = 0.25
RETRY_AFTER_SECONDS = 30.0
# How long a worker reuses the data version it read, instead of asking Redis on every request.
VERSION_CHECK_SECONDS = 5.0

HIT, MISS, BYPASS = "HIT", "MISS", "BYPASS"


class ResponseCache:
    def __init__(self, client: redis.Redis | None, ttl_seconds: int, version_check_seconds: float = VERSION_CHECK_SECONDS):
        self.client = client
        self.ttl_seconds = ttl_seconds
        self.version_check_seconds = version_check_seconds
        self._version: int | None = None
        self._version_read_at = 0.0
        self._down_until = 0.0

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def get_or_build(self, key: str, build: Callable[[], bytes]) -> tuple[bytes, str | None]:
        """The cached body for key, or build() and cache it. Also returns HIT, MISS, BYPASS or None (off)."""
        if self.client is None:
            return build(), None
        if time.monotonic() < self._down_until:
            return build(), BYPASS
        try:
            full_key = f"{KEY_PREFIX}:{self._data_version()}:{key}"
            cached = self.client.get(full_key)
        except redis.RedisError as exc:
            self._failed(exc)
            return build(), BYPASS
        if cached is not None:
            return cached, HIT
        body = build()
        try:
            self.client.set(full_key, body, ex=self.ttl_seconds)
        except redis.RedisError as exc:
            self._failed(exc)
            return body, BYPASS
        return body, MISS

    def _data_version(self) -> int:
        now = time.monotonic()
        if self._version is None or now - self._version_read_at >= self.version_check_seconds:
            self._version = int(self.client.get(VERSION_KEY) or 0)
            self._version_read_at = now
        return self._version

    def _failed(self, exc: Exception) -> None:
        self._down_until = time.monotonic() + RETRY_AFTER_SECONDS
        log.warning("Redis unavailable (%s); answering from the database for %ds", exc, RETRY_AFTER_SECONDS)

    def close(self) -> None:
        if self.client is not None:
            self.client.close()


def connect(redis_url: str) -> redis.Redis:
    return redis.Redis.from_url(redis_url, socket_timeout=TIMEOUT_SECONDS, socket_connect_timeout=TIMEOUT_SECONDS)


def bump_data_version(redis_url: str) -> None:
    """Called by the loader after it loads new data, so cached responses are replaced."""
    try:
        with connect(redis_url) as client:
            version = client.incr(VERSION_KEY)
        log.info("Cache data version is now %d", version)
    except redis.RedisError as exc:
        log.warning("Could not update the cache data version (%s); cached responses expire within the TTL", exc)


_cache = ResponseCache(None, 0)


def open_cache(enabled: bool, redis_url: str | None, ttl_seconds: int) -> None:
    global _cache
    on = enabled and bool(redis_url)
    _cache = ResponseCache(connect(redis_url) if on else None, ttl_seconds)
    if on:
        log.info("Response cache on (TTL %ds)", ttl_seconds)
    else:
        log.info("Response cache off (%s)", "REDIS_URL not set" if enabled else "CACHE_ENABLED is false")


def close_cache() -> None:
    _cache.close()


async def get_cache() -> ResponseCache:
    return _cache
