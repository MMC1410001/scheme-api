"""Plain HTTP behaviour of the routes: /health (with its database check) for load balancers, and no
slash redirects."""
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg_pool import PoolTimeout

from app.main import app
from app.repository import get_repository
from tests.test_api import FakeRepository


@pytest.fixture
def client():
    app.dependency_overrides[get_repository] = lambda: FakeRepository()
    yield TestClient(app)
    app.dependency_overrides.pop(get_repository, None)


def test_health_answers_head_for_load_balancer_probes(client):
    response = client.head("/health")
    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["x-content-type-options"] == "nosniff"


class UnreachableRepository(FakeRepository):
    def __init__(self, error):
        super().__init__()
        self.error = error

    def ping(self, timeout):
        raise self.error


@pytest.mark.parametrize("error", [PoolTimeout("no connection"), psycopg.OperationalError("connection refused")])
def test_health_is_503_when_the_database_is_unreachable(error):
    app.dependency_overrides[get_repository] = lambda: UnreachableRepository(error)
    try:
        client = TestClient(app)
        response = client.get("/health")
        head = client.head("/health")
    finally:
        app.dependency_overrides.pop(get_repository, None)
    assert response.status_code == 503
    assert response.json() == {"ok": False, "detail": "Database unreachable"}
    assert response.headers["retry-after"] == "5"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert head.status_code == 503


def test_health_is_200_with_a_database(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_head_on_health_is_not_in_the_openapi_spec(client):
    assert list(client.get("/openapi.json").json()["paths"]["/health"]) == ["get"]


@pytest.mark.parametrize("path", ["/v1/schemes/current-month/", "/health/"])
def test_a_trailing_slash_is_not_redirected(client, path):
    response = client.post(path, json={"dealer_code": "1", "month": 9, "year": 2026}, follow_redirects=False)
    assert response.status_code == 404
    assert "location" not in response.headers
