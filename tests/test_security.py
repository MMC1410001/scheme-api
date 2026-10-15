import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app
from app.middleware import SECURITY_HEADERS
from app.repository import get_repository
from tests.test_api import FakeRepository

URL = "/v1/schemes/current-month"
MONTH = {"month": 9, "year": 2026}


@pytest.fixture
def client():
    app.dependency_overrides[get_repository] = lambda: FakeRepository()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_security_headers_on_api_responses(client):
    headers = client.post(URL, json={"dealer_code": "6100009999", **MONTH}).headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["cross-origin-resource-policy"] == "same-origin"
    assert headers["cache-control"] == "no-store"
    assert "max-age=31536000" in headers["strict-transport-security"]
    assert headers["content-security-policy"].startswith("default-src 'none'")


def test_security_headers_on_errors_too(client):
    headers = client.post(URL, json={}).headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "payload",
    ["<script>alert(1)</script>", "1' OR '1'='1", "../../etc/passwd", "61000\x0099", "6100%0d%0aX: y", "A" * 21],
)
def test_malicious_dealer_codes_are_rejected_without_echoing_them(client, payload):
    response = client.post(URL, json={"dealer_code": payload, **MONTH})
    assert response.status_code == 422
    assert payload not in response.text
    assert "input" not in response.json()["detail"][0]


def test_numeric_dealer_code_is_accepted(client):
    assert client.post(URL, json={"dealer_code": 6100009999, **MONTH}).status_code == 200


def test_oversized_body_is_rejected(client):
    response = client.post(URL, content=b'{"dealer_code": "' + b"1" * 5000 + b'"}',
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 413


def test_oversized_chunked_body_is_rejected(client):
    def chunks():
        yield b'{"dealer_code": "'
        for _ in range(10):
            yield b"1" * 1000
        yield b'"}'

    response = client.post(URL, content=chunks(), headers={"Content-Type": "application/json"})
    assert response.status_code == 413


def test_invalid_json_does_not_leak_details(client):
    response = client.post(URL, content=b"{not json", headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert "Traceback" not in response.text


@pytest.mark.parametrize("method", ["get", "put", "delete", "patch"])
def test_other_methods_are_not_allowed(client, method):
    assert getattr(client, method)(URL).status_code == 405


def test_unexpected_errors_return_a_generic_500(client):
    class BrokenRepository(FakeRepository):
        def month_schemes(self, *args):
            raise RuntimeError("connection to server at 10.0.0.5 failed")

    app.dependency_overrides[get_repository] = lambda: BrokenRepository()
    response = TestClient(app, raise_server_exceptions=False).post(URL, json={"dealer_code": "1", **MONTH})
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert "10.0.0.5" not in response.text
    for name, value in SECURITY_HEADERS.items():
        assert response.headers[name] == value, name


@pytest.mark.parametrize("value", ["k\xe9y", "\xff" * 14, "eyJ\xe9.x.y"])
def test_non_ascii_credentials_return_401_not_500(client, value):
    from tests.test_auth import SETTINGS

    app.dependency_overrides[get_settings] = lambda: SETTINGS
    bearer = client.post(URL, json={"dealer_code": "1", **MONTH}, headers={"Authorization": f"Bearer {value}".encode("latin-1")})
    assert bearer.status_code == 401
    assert bearer.json() == {"detail": "Invalid token"}
    basic = client.post("/v1/auth/token", data={"grant_type": "client_credentials"},
                        headers={"Authorization": f"Basic {value}".encode("latin-1")})
    assert basic.status_code == 401
    assert basic.json()["error"] == "invalid_client"


def test_database_busy_returns_503_with_retry_after(client):
    from psycopg_pool import PoolTimeout

    class BusyRepository(FakeRepository):
        def month_schemes(self, *args):
            raise PoolTimeout("couldn't get a connection after 5.00 sec")

    app.dependency_overrides[get_repository] = lambda: BusyRepository()
    response = client.post(URL, json={"dealer_code": "1", **MONTH})
    assert response.status_code == 503
    assert response.headers["retry-after"] == "2"
