import pytest
import redis
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app import ratelimit
from app.ratelimit import RateLimitMiddleware


def ok(request):
    return PlainTextResponse("ok")


def make_client(per_second=3, redis_client=None, ip="10.0.0.1", monkeypatch=None):
    app = Starlette(routes=[Route("/v1/x", ok, methods=["GET", "POST"]), Route("/health", ok)])
    limited = RateLimitMiddleware(app, limit=per_second, redis_url="redis://fake" if redis_client else None)
    if redis_client is not None:
        monkeypatch.setattr(ratelimit.aioredis.Redis, "from_url", lambda *a, **k: redis_client)
    return TestClient(limited, client=(ip, 50000)), limited


class FakePipeline:
    def __init__(self, store):
        self.store, self.ops = store, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def incr(self, key):
        self.ops.append(("incr", key))
        return self

    def expire(self, key, seconds):
        self.ops.append(("expire", key))
        return self

    async def execute(self):
        if self.store.down:
            raise redis.ConnectionError("down")
        results = []
        for op, key in self.ops:
            if op == "incr":
                self.store.data[key] = self.store.data.get(key, 0) + 1
                results.append(self.store.data[key])
            else:
                self.store.expiring.add(key)
                results.append(True)
        return results


class FakeAsyncRedis:
    def __init__(self, down=False):
        self.data, self.expiring, self.down = {}, set(), down

    def pipeline(self, transaction=True):
        return FakePipeline(self)


def freeze_time(monkeypatch, second=1_000):
    monkeypatch.setattr(ratelimit.time, "time", lambda: second + 0.5)


def test_requests_over_the_limit_get_429_with_retry_after(monkeypatch):
    freeze_time(monkeypatch)
    client, _ = make_client(per_second=3)
    assert [client.get("/v1/x").status_code for _ in range(4)] == [200, 200, 200, 429]
    response = client.get("/v1/x")
    assert response.json() == {"detail": "Too many requests"}
    assert response.headers["retry-after"] == "1"


def test_the_count_starts_again_each_second(monkeypatch):
    freeze_time(monkeypatch, 1_000)
    client, _ = make_client(per_second=1)
    assert [client.get("/v1/x").status_code for _ in range(2)] == [200, 429]
    freeze_time(monkeypatch, 1_001)
    assert client.get("/v1/x").status_code == 200


def test_each_ip_has_its_own_limit(monkeypatch):
    freeze_time(monkeypatch)
    client, limited = make_client(per_second=1, ip="10.0.0.1")
    other = TestClient(limited, client=("10.0.0.2", 50000))
    assert client.get("/v1/x").status_code == 200
    assert client.get("/v1/x").status_code == 429
    assert other.get("/v1/x").status_code == 200


def test_health_is_never_limited(monkeypatch):
    freeze_time(monkeypatch)
    client, _ = make_client(per_second=1)
    assert {client.get("/health").status_code for _ in range(5)} == {200}


def test_counts_are_kept_in_redis_per_ip_and_second(monkeypatch):
    freeze_time(monkeypatch, 1_000)
    fake = FakeAsyncRedis()
    client, _ = make_client(per_second=2, redis_client=fake, monkeypatch=monkeypatch)
    assert [client.get("/v1/x").status_code for _ in range(3)] == [200, 200, 429]
    assert fake.data == {"acme:rate:10.0.0.1:1000": 3}
    assert fake.expiring == {"acme:rate:10.0.0.1:1000"}  # keys expire on their own


def test_redis_down_falls_back_to_counting_in_memory(monkeypatch):
    freeze_time(monkeypatch)
    client, limited = make_client(per_second=2, redis_client=FakeAsyncRedis(down=True), monkeypatch=monkeypatch)
    assert [client.get("/v1/x").status_code for _ in range(3)] == [200, 200, 429]
    assert limited._down_until > 0  # Redis is not retried on every request


def test_429_from_the_api_carries_the_security_headers(monkeypatch):
    from app.main import app

    with TestClient(app) as client:
        client.get("/health")  # builds the middleware stack
    limiter = app.middleware_stack
    while not isinstance(limiter, RateLimitMiddleware):
        limiter = limiter.app
    monkeypatch.setattr(limiter, "limit", 0)
    monkeypatch.setattr(limiter, "redis_url", None)
    with TestClient(app) as client:
        response = client.post("/v1/schemes/current-month", json={"dealer_code": "1", "month": 9, "year": 2026})
    assert response.status_code == 429
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


def test_a_path_limit_counts_only_its_paths_per_minute(monkeypatch):
    freeze_time(monkeypatch, 1_000)  # 1,000.5 s: 19.5 s left in the minute that started at 960
    app = Starlette(routes=[Route("/v1/x", ok), Route("/v1/auth/token", ok, methods=["POST"])])
    client = TestClient(RateLimitMiddleware(app, limit=2, window_seconds=60, paths={"/v1/auth/token"}, name="token"))
    assert [client.post("/v1/auth/token").status_code for _ in range(3)] == [200, 200, 429]
    assert client.post("/v1/auth/token").headers["retry-after"] == "20"
    assert {client.get("/v1/x").status_code for _ in range(5)} == {200}
    freeze_time(monkeypatch, 1_020)
    assert client.post("/v1/auth/token").status_code == 200


def test_named_limits_keep_their_own_redis_keys(monkeypatch):
    freeze_time(monkeypatch, 1_000)
    fake = FakeAsyncRedis()
    monkeypatch.setattr(ratelimit.aioredis.Redis, "from_url", lambda *a, **k: fake)
    app = Starlette(routes=[Route("/v1/auth/token", ok, methods=["POST"])])
    client = TestClient(RateLimitMiddleware(app, limit=5, redis_url="redis://fake", window_seconds=60, name="token"),
                        client=("10.0.0.1", 50000))
    client.post("/v1/auth/token")
    assert fake.data == {"acme:rate:token:10.0.0.1:16": 1}


# --- Client IP behind a proxy (QA 2026-10-16 #1) ---------------------------------------------------

def scope_from(peer: str, *forwarded: str) -> dict:
    return {"client": (peer, 50000), "headers": [(b"x-forwarded-for", value.encode()) for value in forwarded]}


NGINX = ratelimit.trusted_networks(["10.0.0.5", "172.18.0.0/16"])


@pytest.mark.parametrize(("scope", "trusted", "expected"), [
    (scope_from("203.0.113.9", "1.2.3.4"), (), "203.0.113.9"),                # no trusted proxies: header ignored
    (scope_from("203.0.113.9", "1.2.3.4"), NGINX, "203.0.113.9"),             # caller is not a proxy: ignored
    (scope_from("10.0.0.5", "198.51.100.7"), NGINX, "198.51.100.7"),          # nginx says who called it
    (scope_from("10.0.0.5", "1.2.3.4, 198.51.100.7"), NGINX, "198.51.100.7"),  # caller's own entry is skipped
    (scope_from("10.0.0.5", "1.2.3.4", "198.51.100.7"), NGINX, "198.51.100.7"),  # two headers read as one list
    (scope_from("10.0.0.5", "198.51.100.7, 172.18.0.3"), NGINX, "198.51.100.7"),  # through two trusted proxies
    (scope_from("10.0.0.5", "1.2.3.4, not-an-ip"), NGINX, "10.0.0.5"),        # unreadable entry: the proxy
    (scope_from("10.0.0.5"), NGINX, "10.0.0.5"),                              # no header
    (scope_from("10.0.0.5", "172.18.0.9"), NGINX, "172.18.0.9"),              # only proxies: the farthest one
    (scope_from("::1", "198.51.100.7"), ratelimit.trusted_networks(["::1"]), "198.51.100.7"),
    ({"headers": []}, NGINX, "unknown"),
])
def test_client_ip_believes_x_forwarded_for_only_from_trusted_proxies(scope, trusted, expected):
    assert ratelimit.client_ip(scope, trusted) == expected


def test_a_spoofed_x_forwarded_for_does_not_get_a_fresh_limit(monkeypatch):
    """Locally, uvicorn trusted 127.0.0.1 and let any X-Forwarded-For pick a new IP: 429 became 200."""
    freeze_time(monkeypatch)
    client, _ = make_client(per_second=1, ip="127.0.0.1")
    assert client.get("/v1/x").status_code == 200
    assert client.get("/v1/x", headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 429
    assert client.get("/v1/x", headers={"X-Forwarded-For": "5.6.7.8"}).status_code == 429


def test_callers_behind_a_trusted_proxy_each_get_their_own_limit(monkeypatch):
    freeze_time(monkeypatch)
    app = Starlette(routes=[Route("/v1/x", ok)])
    limited = RateLimitMiddleware(app, limit=1, trusted_proxies=["10.0.0.5"])
    nginx = TestClient(limited, client=("10.0.0.5", 50000))
    first = {"X-Forwarded-For": "198.51.100.7"}
    assert nginx.get("/v1/x", headers=first).status_code == 200
    assert nginx.get("/v1/x", headers=first).status_code == 429
    assert nginx.get("/v1/x", headers={"X-Forwarded-For": "198.51.100.8"}).status_code == 200
    # the first caller can't escape by adding its own entry: nginx appends the real address last
    assert nginx.get("/v1/x", headers={"X-Forwarded-For": "9.9.9.9, 198.51.100.7"}).status_code == 429


def test_launcher_turns_off_uvicorns_proxy_headers(monkeypatch):
    from app import __main__ as launcher
    calls = []
    monkeypatch.setattr(launcher.uvicorn, "run", lambda *a, **k: calls.append((a, k)))
    launcher.main(["--port", "9001", "--workers", "2"])
    (args, kwargs), = calls
    assert args == ("app.main:app",)
    assert kwargs["proxy_headers"] is False and kwargs["server_header"] is False
    assert (kwargs["port"], kwargs["workers"], kwargs["host"]) == (9001, 2, "127.0.0.1")
