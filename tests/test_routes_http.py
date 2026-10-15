"""Plain HTTP behaviour of the routes: HEAD on /health for load balancers, and no slash redirects."""
import pytest
from fastapi.testclient import TestClient

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


def test_head_on_health_is_not_in_the_openapi_spec(client):
    assert list(client.get("/openapi.json").json()["paths"]["/health"]) == ["get"]


@pytest.mark.parametrize("path", ["/v1/schemes/current-month/", "/health/"])
def test_a_trailing_slash_is_not_redirected(client, path):
    response = client.post(path, json={"dealer_code": "1", "month": 9, "year": 2026}, follow_redirects=False)
    assert response.status_code == 404
    assert "location" not in response.headers
