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
