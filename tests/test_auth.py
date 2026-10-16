"""OAuth2 client credentials: POST /v1/auth/token issues 1-hour JWTs; /v1/schemes needs a valid one."""
import base64
import logging
import time

import jwt
import pytest
from fastapi.testclient import TestClient

from app import security
from app.config import Settings, get_settings
from app.main import app
from app.ratelimit import RateLimitMiddleware
from app.repository import get_repository
from app.security import AUDIENCE, ISSUER, SCOPE, config_problems, hash_secret, issue_token
from tests.test_api import FakeRepository

TOKEN_URL = "/v1/auth/token"
DATA_URL = "/v1/schemes/current-month"
BODY = {"dealer_code": "1001", "month": 9, "year": 2026}
SECRET = "partner-client-secret-for-tests"
KEY = "k" * 40
SETTINGS = Settings(_env_file=None, auth_clients={"partner": hash_secret(SECRET)}, jwt_secret=KEY)


def bearer(settings: Settings = SETTINGS, client_id: str = "partner") -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_token(client_id, settings)}"}


def signed(key: str = KEY, **changes) -> dict[str, str]:
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": "partner", "scope": SCOPE, "iat": now, "nbf": now,
              "exp": now + 3600, "jti": "x", **changes}
    claims = {k: v for k, v in claims.items() if v is not None}
    return {"Authorization": f"Bearer {jwt.encode(claims, key, algorithm='HS256')}"}


def basic(client_id: str, secret: str) -> dict[str, str]:
    return {"Authorization": "Basic " + base64.b64encode(f"{client_id}:{secret}".encode()).decode()}


@pytest.fixture
def client(monkeypatch):
    app.dependency_overrides[get_repository] = lambda: FakeRepository()
    app.dependency_overrides[get_settings] = lambda: SETTINGS
    test_client = TestClient(app)
    layer = app.middleware_stack  # built by conftest's isolated_rate_limits
    while layer is not None:  # these tests ask for many tokens; the token limit has its own test
        if isinstance(layer, RateLimitMiddleware):
            monkeypatch.setattr(layer, "limit", 10**9)
        layer = getattr(layer, "app", None)
    yield test_client
    app.dependency_overrides.clear()


def form(**params) -> dict:
    return {"data": {"grant_type": "client_credentials", **params}}


# --- Token endpoint ------------------------------------------------------------------------------

def test_form_request_returns_a_bearer_token_for_one_hour(client):
    response = client.post(TOKEN_URL, **form(client_id="partner", client_secret=SECRET))
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "Bearer" and body["expires_in"] == 3600 and body["scope"] == SCOPE
    assert response.headers["cache-control"] == "no-store" and response.headers["pragma"] == "no-cache"
    claims = jwt.decode(body["access_token"], KEY, algorithms=["HS256"], audience=AUDIENCE)
    assert claims["sub"] == "partner" and claims["iss"] == ISSUER and claims["exp"] - claims["iat"] == 3600
    assert client.post(DATA_URL, json=BODY, headers={"Authorization": f"Bearer {body['access_token']}"}).status_code == 200


def test_json_body_and_basic_auth_are_accepted(client):
    by_json = client.post(TOKEN_URL, json={"grant_type": "client_credentials", "client_id": "partner", "client_secret": SECRET})
    by_basic = client.post(TOKEN_URL, data={"grant_type": "client_credentials", "scope": SCOPE}, headers=basic("partner", SECRET))
    assert by_json.status_code == by_basic.status_code == 200


@pytest.mark.parametrize("scope", ["", " ", SCOPE])
def test_blank_or_default_scope_is_accepted(client, scope):
    # Swagger UI's Authorize sends scope= when no scope is ticked.
    response = client.post(TOKEN_URL, **form(client_id="partner", client_secret=SECRET, scope=scope))
    assert response.status_code == 200 and response.json()["scope"] == SCOPE


def test_each_token_is_unique(client):
    tokens = {client.post(TOKEN_URL, **form(client_id="partner", client_secret=SECRET)).json()["access_token"] for _ in range(3)}
    assert len(tokens) == 3


@pytest.mark.parametrize("credentials", [{"client_id": "partner", "client_secret": "wrong"},
                                         {"client_id": "nobody", "client_secret": SECRET},
                                         {"client_id": "partner"}, {}])
def test_bad_client_credentials_get_401_invalid_client(client, credentials):
    response = client.post(TOKEN_URL, **form(**credentials))
    assert response.status_code == 401
    assert response.json() == {"error": "invalid_client", "error_description": "Client authentication failed"}
    assert response.headers["www-authenticate"].startswith("Basic")


def test_wrong_basic_auth_gets_401(client):
    assert client.post(TOKEN_URL, **form(), headers=basic("partner", "wrong")).status_code == 401
    assert client.post(TOKEN_URL, **form(), headers={"Authorization": "Basic !!!"}).status_code == 401


@pytest.mark.parametrize("request_kwargs, error", [
    ({"data": {"client_id": "partner", "client_secret": SECRET}}, "invalid_request"),                  # no grant_type
    (form(grant_type="password", client_id="partner", client_secret=SECRET), "unsupported_grant_type"),
    (form(client_id="partner", client_secret=SECRET, scope="schemes:write"), "invalid_scope"),
    ({"json": ["grant_type"]}, "invalid_request"),
    ({"json": {"grant_type": "client_credentials", "client_id": 1}}, "invalid_request"),
    ({"content": b"{not json", "headers": {"Content-Type": "application/json"}}, "invalid_request"),
    ({"content": b"grant_type=client_credentials", "headers": {"Content-Type": "text/plain"}}, "invalid_request"),
    ({"content": b"grant_type=client_credentials&grant_type=client_credentials&client_id=partner&client_secret=x",
      "headers": {"Content-Type": "application/x-www-form-urlencoded"}}, "invalid_request"),
])
def test_malformed_token_requests_get_400(client, request_kwargs, error):
    response = client.post(TOKEN_URL, **request_kwargs)
    assert response.status_code == 400
    assert response.json()["error"] == error


@pytest.mark.parametrize("raw", [
    '{"grant_type": "client_credentials", "client_id": "partner", "client_secret": "bad", "client_secret": "%s"}',
    '{"grant_type": "client_credentials", "client_id": "x", "client_id": "partner", "client_secret": "%s"}',
    '{"grant_type": "password", "grant_type": "client_credentials", "client_id": "partner", "client_secret": "%s"}',
])
def test_a_json_parameter_sent_twice_gets_400_like_a_form_one(client, raw):
    """QA 2026-10-16 #9: json.loads keeps the last repeated key, which let a second secret win."""
    response = client.post(TOKEN_URL, content=raw % SECRET, headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    assert response.json() == {"error": "invalid_request", "error_description": "A parameter was sent more than once"}


def test_secret_that_is_not_valid_utf8_gets_400_not_500(client):
    raw = b'{"grant_type": "client_credentials", "client_id": "partner", "client_secret": "\\ud800"}'
    response = client.post(TOKEN_URL, content=raw, headers={"Content-Type": "application/json"})
    assert response.status_code == 400 and response.json()["error"] == "invalid_request"


def test_credentials_in_basic_auth_and_body_together_are_refused(client):
    response = client.post(TOKEN_URL, **form(client_id="partner", client_secret=SECRET), headers=basic("partner", SECRET))
    assert response.status_code == 400 and response.json()["error"] == "invalid_request"


def test_secret_is_never_logged(client, caplog):
    caplog.set_level(logging.INFO, logger="app.auth")
    client.post(TOKEN_URL, **form(client_id="partner", client_secret="wrong-secret-value"))
    client.post(TOKEN_URL, **form(client_id="partner", client_secret=SECRET))
    assert "wrong-secret-value" not in caplog.text and SECRET not in caplog.text
    assert "bad client credentials" in caplog.text and "Token issued to client partner" in caplog.text


@pytest.mark.parametrize("method", ["get", "put", "delete"])
def test_token_endpoint_only_accepts_post(client, method):
    assert getattr(client, method)(TOKEN_URL).status_code == 405


# --- Protected endpoint --------------------------------------------------------------------------

def test_missing_token_gets_401_with_bearer_challenge(client):
    response = client.post(DATA_URL, json=BODY)
    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}
    assert response.headers["www-authenticate"] == 'Bearer realm="scheme-api"'


def test_expired_token_says_so(client):
    response = client.post(DATA_URL, json=BODY, headers=signed(exp=int(time.time()) - 60))
    assert response.status_code == 401
    assert response.json() == {"detail": "Token expired"}
    assert 'error="invalid_token"' in response.headers["www-authenticate"]


def test_clock_skew_within_leeway_is_allowed(client):
    assert client.post(DATA_URL, json=BODY, headers=signed(exp=int(time.time()) - 10)).status_code == 200


@pytest.mark.parametrize("headers", [
    signed(key="another-key-of-at-least-32-characters"),   # forged signature
    signed(iss="someone-else"), signed(aud="another-api"),
    signed(jti=None), signed(exp=None), signed(nbf=int(time.time()) + 600),
    signed(sub="removed-client"),                           # client no longer configured
    {"Authorization": "Bearer not.a.jwt"},
    {"Authorization": f"Basic {base64.b64encode(f'partner:{SECRET}'.encode()).decode()}"},  # wrong scheme
    {"Authorization": "Bearer"},
])
def test_invalid_tokens_get_401(client, headers):
    response = client.post(DATA_URL, json=BODY, headers=headers)
    assert response.status_code == 401
    assert response.json()["detail"] in {"Invalid token", "Not authenticated"}


def test_alg_none_and_other_algorithms_are_refused(client):
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": "partner", "scope": SCOPE, "iat": now, "nbf": now, "exp": now + 60, "jti": "x"}
    unsigned = jwt.encode(claims, None, algorithm="none")
    hs512 = jwt.encode(claims, KEY, algorithm="HS512")
    for token in (unsigned, hs512):
        assert client.post(DATA_URL, json=BODY, headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_token_without_the_scope_gets_403(client):
    response = client.post(DATA_URL, json=BODY, headers=signed(scope="other"))
    assert response.status_code == 403
    assert 'error="insufficient_scope"' in response.headers["www-authenticate"]


def test_lowercase_bearer_is_accepted(client):
    token = issue_token("partner", SETTINGS)
    assert client.post(DATA_URL, json=BODY, headers={"Authorization": f"bearer {token}"}).status_code == 200


def test_tokens_from_the_previous_key_work_during_rotation(client):
    old_token = bearer()
    rotating = SETTINGS.model_copy(update={"jwt_secret": Settings(jwt_secret="n" * 40).jwt_secret,
                                           "jwt_previous_secret": SETTINGS.jwt_secret})
    app.dependency_overrides[get_settings] = lambda: rotating
    assert client.post(DATA_URL, json=BODY, headers=old_token).status_code == 200
    assert client.post(DATA_URL, json=BODY, headers=bearer(rotating)).status_code == 200
    rotated = rotating.model_copy(update={"jwt_previous_secret": None})
    app.dependency_overrides[get_settings] = lambda: rotated
    assert client.post(DATA_URL, json=BODY, headers=old_token).status_code == 401


def test_health_needs_no_token(client):
    assert client.get("/health").status_code == 200


# --- Settings, docs, CLI -------------------------------------------------------------------------

def test_signing_key_is_hidden_from_repr():
    assert KEY not in repr(SETTINGS) and KEY not in str(SETTINGS.model_dump())


@pytest.mark.parametrize("env", [{"AUTH_CLIENTS": ""}, {"AUTH_CLIENTS": " "}, {"JWT_SECRET": ""}, {"JWT_PREVIOUS_SECRET": ""}])
def test_empty_auth_values_mean_unset(monkeypatch, env):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    settings = Settings(_env_file=None)
    assert settings.auth_clients == {} and settings.jwt_secret is None and settings.jwt_previous_secret is None


def test_auth_clients_is_read_as_json_from_the_environment(monkeypatch):
    monkeypatch.setenv("AUTH_CLIENTS", '{"partner": "%s"}' % ("a" * 64))
    assert Settings(_env_file=None).auth_clients == {"partner": "a" * 64}


def test_config_problems():
    assert config_problems(SETTINGS) == []
    assert config_problems(Settings(_env_file=None)) == ["AUTH_CLIENTS is empty", "JWT_SECRET is not set"]
    weak = SETTINGS.model_copy(update={"jwt_ttl_seconds": 10, "auth_clients": {"bad id!": hash_secret("x")}})
    assert len(config_problems(weak)) == 2


def test_openapi_documents_the_client_credentials_flow():
    spec = TestClient(app).get("/openapi.json").json()
    scheme = spec["components"]["securitySchemes"]["OAuth2ClientCredentials"]
    assert scheme["flows"]["clientCredentials"]["tokenUrl"] == TOKEN_URL
    assert spec["paths"][DATA_URL]["post"]["security"] == [{"OAuth2ClientCredentials": [SCOPE]}]
    assert "security" not in spec["paths"][TOKEN_URL]["post"]


def test_cli_creates_a_client_whose_hash_matches(capsys):
    assert security._main(["new-client", "partner"]) == 0
    out = capsys.readouterr().out
    secret = out.split("client_secret: ")[1].split()[0]
    assert hash_secret(secret) in out and len(secret) >= 43
    assert security._main(["new-client", "bad id"]) == 2
