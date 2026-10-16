"""Redis response cache (app/cache.py): hits skip the database, new data replaces cached answers,
and a broken Redis never breaks the API."""
import asyncio
import json

import pytest
import redis
from fastapi.testclient import TestClient

from app import cache as cache_module
from app.cache import VERSION_KEY, ResponseCache, get_cache
from app.main import app
from app.repository import get_repository
from tests.test_api import MONTH, FakeRepository

API1 = "/v1/schemes/current-month"


class FakeRedis:
    """The few Redis commands the cache uses, in memory. broken=True makes every call fail."""

    def __init__(self):
        self.data, self.ttls, self.broken = {}, {}, False

    def _check(self):
        if self.broken:
            raise redis.ConnectionError("Connection refused")

    def get(self, key):
        self._check()
        return self.data.get(key)

    def set(self, key, value, ex=None):
        self._check()
        self.data[key], self.ttls[key] = value, ex

    def incr(self, key):
        self._check()
        self.data[key] = str(int(self.data.get(key, 0)) + 1).encode()
        return int(self.data[key])

    def close(self):
        pass


@pytest.fixture
def repo():
    fake = FakeRepository()
    app.dependency_overrides[get_repository] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_repository, None)


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def response_cache(fake_redis):
    cache = ResponseCache(fake_redis, ttl_seconds=900, version_check_seconds=0)
    app.dependency_overrides[get_cache] = lambda: cache
    yield cache
    app.dependency_overrides.pop(get_cache, None)


@pytest.fixture
def client():
    return TestClient(app)


def api1(client, dealer="6100009999", **month):
    return client.post(API1, json={"dealer_code": dealer, **(month or MONTH)})


def test_cache_off_by_default_sends_no_header(client, repo):
    response = api1(client)
    assert response.status_code == 200
    assert "X-Cache" not in response.headers


def test_second_request_is_served_from_redis(client, repo, response_cache):
    first, second = api1(client), api1(client)
    assert (first.headers["X-Cache"], second.headers["X-Cache"]) == ("MISS", "HIT")
    assert first.content == second.content
    assert len(repo.calls) == 1  # the database was asked once


def test_cached_body_is_identical_to_the_uncached_one(client, repo, response_cache):
    cached = [api1(client).json(), api1(client).json()]
    app.dependency_overrides.pop(get_cache)
    assert cached == [api1(client).json()] * 2
    assert cached[0][0]["Customer"] == "6100009999" and cached[0][0]["Qualification"] == 250.5


def test_key_includes_dealer_month_and_year(client, repo, response_cache):
    api1(client)
    assert api1(client, month=10, year=2026).headers["X-Cache"] == "MISS"
    assert api1(client, dealer="6100000001").headers["X-Cache"] == "MISS"
    assert api1(client, dealer="0006100009999").headers["X-Cache"] == "HIT"  # same dealer after normalising


def test_empty_answers_are_cached(client, repo, response_cache, monkeypatch):
    monkeypatch.setattr(repo, "month_schemes", lambda *a: [])
    api1(client)
    response = api1(client)
    assert (response.headers["X-Cache"], response.json()) == ("HIT", [])


def test_new_data_version_replaces_cached_answers(client, repo, response_cache, fake_redis):
    api1(client)
    fake_redis.incr(VERSION_KEY)  # what the loader does after loading
    assert api1(client).headers["X-Cache"] == "MISS"
    assert len(repo.calls) == 2


def test_entries_expire_after_the_ttl(client, repo, response_cache, fake_redis):
    api1(client)
    assert set(fake_redis.ttls.values()) == {900}


def test_broken_redis_falls_back_to_the_database(client, repo, response_cache, fake_redis):
    fake_redis.broken = True
    response = api1(client)
    assert (response.status_code, response.headers["X-Cache"]) == (200, "BYPASS")
    assert response.json()[0]["Customer"] == "6100009999"
    fake_redis.broken = False
    assert api1(client).headers["X-Cache"] == "BYPASS"  # not retried straight away


def test_errors_are_not_cached(client, repo, response_cache, fake_redis):
    client.post(API1, json={"dealer_code": "6100009999", "month": 13, "year": 2026})
    assert not [k for k in fake_redis.data if k != VERSION_KEY]


@pytest.mark.parametrize(("enabled", "url", "on"), [
    (True, "redis://localhost:6379/0", True),
    (False, "redis://localhost:6379/0", False),  # switched off, URL kept
    (True, None, False),
    (False, None, False),
])
def test_cache_switch(monkeypatch, enabled, url, on):
    monkeypatch.setattr(cache_module, "connect", lambda url: FakeRedis())
    cache_module.open_cache(enabled, url, 900)
    try:
        assert asyncio.run(cache_module.get_cache()).enabled is on
    finally:
        cache_module.open_cache(False, None, 0)


def test_loader_bumps_the_data_version(monkeypatch, fake_redis):
    monkeypatch.setattr(cache_module, "connect", lambda url: _Closing(fake_redis))
    cache_module.bump_data_version("redis://example")
    cache_module.bump_data_version("redis://example")
    assert int(fake_redis.data[VERSION_KEY]) == 2


def test_loader_carries_on_when_redis_is_down(monkeypatch, fake_redis):
    fake_redis.broken = True
    monkeypatch.setattr(cache_module, "connect", lambda url: _Closing(fake_redis))
    cache_module.bump_data_version("redis://example")  # logs a warning, doesn't raise


class _Closing:
    def __init__(self, client):
        self.client = client

    def __enter__(self):
        return self.client

    def __exit__(self, *exc):
        return False


def test_cached_value_is_the_json_body(client, repo, response_cache, fake_redis):
    api1(client)
    (key,) = [k for k in fake_redis.data if k != VERSION_KEY]
    assert key == "acme:v2:0:api1:6100009999:2026:9"
    assert json.loads(fake_redis.data[key])[0]["ComponentDescription"] == "LUXURY"


@pytest.mark.parametrize("version", [b"abc", b"1.5", b""])
def test_a_corrupt_data_version_bypasses_the_cache_instead_of_failing(client, repo, response_cache, fake_redis, version):
    """QA 2026-10-16 #10: int() of a bad version raised ValueError and every cached request got 500."""
    fake_redis.data[VERSION_KEY] = version
    response = api1(client)
    assert response.status_code == 200
    expected = "MISS" if version == b"" else "BYPASS"  # empty reads as version 0, like a missing key
    assert response.headers["X-Cache"] == expected
    assert response.json()[0]["Customer"] == "6100009999"
