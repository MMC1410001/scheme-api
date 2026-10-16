"""Per-IP rate limit: at most `limit` requests per client IP in each window, then 429 + Retry-After.

app.main adds two: RATE_LIMIT_PER_SECOND on every path, and TOKEN_RATE_LIMIT_PER_MINUTE on
POST /v1/auth/token only (a client needs one token an hour, so a burst there is a misbehaving client).

The default (2,500/s = 1.5 lakh a minute) sits above the busiest load seen from the dealer app
(1 lakh a minute), which may all arrive from one the client server, so real traffic is never refused; it
stops a single address from flooding the API beyond what it can serve.

Counts live in Redis (REDIS_URL, the same server as the cache) so the limit holds across workers and
servers. If REDIS_URL is not set, or Redis is down, each worker counts in memory (the limit then
applies per worker) and Redis is tried again after RETRY_AFTER_SECONDS. /health is never limited.

The client IP is the connection's address (client_ip). Behind nginx or a load balancer, list the
proxy in TRUSTED_PROXIES so the caller's IP is taken from X-Forwarded-For; otherwise every request
appears to come from the proxy and the limit applies to all callers together. X-Forwarded-For from
any other address is ignored, so a caller cannot pick its own IP. Start the API with `python -m app`:
uvicorn's own X-Forwarded-For handling (on by default, trusting 127.0.0.1) would otherwise rewrite
the address before this middleware sees it.
"""
import asyncio
import logging
import math
import time
from collections.abc import Iterable
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network

import redis
import redis.asyncio as aioredis
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

log = logging.getLogger("app.ratelimit")

KEY_PREFIX = "acme:rate"
TIMEOUT_SECONDS = 0.1
RETRY_AFTER_SECONDS = 30.0
EXEMPT_PATHS = {"/health"}

Networks = tuple[IPv4Network | IPv6Network, ...]


def trusted_networks(entries: Iterable[str]) -> Networks:
    return tuple(ip_network(entry, strict=False) for entry in entries)


def _is_trusted(address: str, trusted: Networks) -> bool:
    try:
        ip = ip_address(address)
    except ValueError:
        return False
    return any(ip in network for network in trusted)


def client_ip(scope: Scope, trusted: Networks = ()) -> str:
    """The caller's IP: the connection's address, unless that is a trusted proxy.

    From a trusted proxy, X-Forwarded-For is read right to left and the first address that is not
    itself a trusted proxy is the caller (each proxy appends the address it received from, so only
    the right-hand entries can be believed; anything to their left was written by the caller).
    If an entry is not an IP, the proxy's address is used rather than a value the caller chose.
    """
    peer = scope["client"][0] if scope.get("client") else "unknown"
    if not trusted or not _is_trusted(peer, trusted):
        return peer
    forwarded = [value.decode("latin-1") for name, value in scope.get("headers", []) if name == b"x-forwarded-for"]
    hops = [hop.strip() for hop in ",".join(forwarded).split(",") if hop.strip()]
    for hop in reversed(hops):
        try:
            ip_address(hop)
        except ValueError:
            return peer
        if not _is_trusted(hop, trusted):
            return hop
    return hops[0] if hops else peer  # every hop is a trusted proxy


class RateLimitMiddleware:
    def __init__(self, app: ASGIApp, limit: int, redis_url: str | None = None, window_seconds: int = 1,
                 paths: set[str] | None = None, name: str | None = None,
                 trusted_proxies: Iterable[str] = ()) -> None:
        """paths: only these paths are limited (default: all but EXEMPT_PATHS). name keeps its Redis counts apart.
        trusted_proxies: IPs/CIDRs whose X-Forwarded-For is believed (client_ip)."""
        self.app = app
        self.limit = limit
        self.redis_url = redis_url
        self.window_seconds = window_seconds
        self.paths = paths
        self.trusted = trusted_networks(trusted_proxies)
        self.key_prefix = f"{KEY_PREFIX}:{name}" if name else KEY_PREFIX
        self._client: aioredis.Redis | None = None
        self._client_loop: asyncio.AbstractEventLoop | None = None
        self._down_until = 0.0
        self._window = 0
        self._counts: dict[str, int] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope["path"] if scope["type"] == "http" else None
        if path is None or path in EXEMPT_PATHS or (self.paths is not None and path not in self.paths):
            await self.app(scope, receive, send)
            return
        ip = client_ip(scope, self.trusted)
        now = time.time()
        window = int(now // self.window_seconds)
        if await self._count(ip, window) > self.limit:
            retry_after = max(1, math.ceil((window + 1) * self.window_seconds - now))
            response = JSONResponse({"detail": "Too many requests"}, status_code=429,
                                    headers={"Retry-After": str(retry_after)})
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)

    async def _count(self, ip: str, window: int) -> int:
        """This IP's requests in the current window, this one included."""
        if self.redis_url and time.monotonic() >= self._down_until:
            try:
                loop = asyncio.get_running_loop()
                if self._client is None or self._client_loop is not loop:  # a client belongs to one event loop
                    self._client = aioredis.Redis.from_url(
                        self.redis_url, socket_timeout=TIMEOUT_SECONDS, socket_connect_timeout=TIMEOUT_SECONDS)
                    self._client_loop = loop
                key = f"{self.key_prefix}:{ip}:{window}"
                async with self._client.pipeline(transaction=False) as pipe:
                    count, _ = await pipe.incr(key).expire(key, self.window_seconds + 1).execute()
                return count
            except (redis.RedisError, OSError) as exc:
                self._down_until = time.monotonic() + RETRY_AFTER_SECONDS
                log.warning("Redis unavailable for rate limiting (%s); counting per worker for %ds", exc, RETRY_AFTER_SECONDS)
        if window != self._window:
            self._window, self._counts = window, {}
        self._counts[ip] = self._counts.get(ip, 0) + 1
        return self._counts[ip]
