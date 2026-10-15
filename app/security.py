"""OAuth2 client credentials (RFC 6749 section 4.4) with short-lived JWT bearer tokens (RFC 6750).

A caller (the the client servers) holds a client_id and client_secret. It exchanges them at
POST /v1/auth/token for an access token valid for JWT_TTL_SECONDS (1 hour), sends the token as
`Authorization: Bearer <token>` on every /v1/schemes call, and gets a new one shortly before it
expires (or after a 401).

- Client secrets are stored only as SHA-256 hashes (AUTH_CLIENTS). They are 256-bit random values,
  so a fast hash is enough (no bcrypt needed): a leaked settings file does not leak the secrets.
- Tokens are signed with HS256 and JWT_SECRET. Checking one pins the algorithm, requires
  iss/aud/sub/iat/nbf/exp/jti and the schemes:read scope, allows LEEWAY_SECONDS of clock skew, and
  needs no database or Redis call (microseconds per request).
- A token's client must still be in AUTH_CLIENTS, so removing a client revokes its tokens at once.
- Rotating the signing key without downtime: move JWT_SECRET to JWT_PREVIOUS_SECRET, set a new
  JWT_SECRET, and remove JWT_PREVIOUS_SECRET after JWT_TTL_SECONDS.

Create a client:      python -m app.security new-client <client_id>
Create a signing key: python -m app.security new-jwt-secret
"""
import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import sys
import time
import uuid
from typing import Annotated
from urllib.parse import parse_qsl, unquote_plus

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Security, status
from fastapi.openapi.models import OAuthFlowClientCredentials, OAuthFlows
from fastapi.security import OAuth2
from fastapi.security.utils import get_authorization_scheme_param
from starlette.responses import JSONResponse

from app.config import Settings, get_settings
from app.openapi_examples import TOKEN_ERRORS, TOKEN_REQUEST_BODY, TOKEN_RESPONSE
from app.schemas import TokenResponse

log = logging.getLogger("app.auth")

TOKEN_URL = "/v1/auth/token"
ISSUER = AUDIENCE = "scheme-api"
SCOPE = "schemes:read"
ALGORITHM = "HS256"
LEEWAY_SECONDS = 30
REQUIRED_CLAIMS = ["iss", "aud", "sub", "iat", "nbf", "exp", "jti"]
MIN_JWT_SECRET_LENGTH = 32
MIN_TTL_SECONDS, MAX_TTL_SECONDS = 60, 86_400
CLIENT_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
SECRET_HASH = re.compile(r"^[0-9a-f]{64}$")
REALM = 'realm="scheme-api"'
# The published local values in .env.example; refused anywhere but APP_ENV=local.
LOCAL_EXAMPLE_SECRET_HASH = hashlib.sha256(b"local-test-secret").hexdigest()
LOCAL_EXAMPLE_JWT_MARKER = "local-only"
# Compared against when the client_id is unknown, so a wrong id takes as long as a wrong secret.
_UNKNOWN_CLIENT_HASH = hashlib.sha256(b"unknown client").hexdigest()

oauth2_scheme = OAuth2(
    flows=OAuthFlows(clientCredentials=OAuthFlowClientCredentials(
        tokenUrl=TOKEN_URL, scopes={SCOPE: "Read dealer scheme rows"})),
    scheme_name="OAuth2ClientCredentials",
    description="Get a token from POST /v1/auth/token with your client_id and client_secret, then send "
                "`Authorization: Bearer <token>`. Tokens last 1 hour; reuse one until it expires.",
    auto_error=False,
)


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def auth_enabled(settings: Settings) -> bool:
    return bool(settings.auth_clients and settings.jwt_secret)


def config_problems(settings: Settings, app_env: str = "local") -> list[str]:
    """What is wrong with the auth settings; empty when they are usable."""
    problems = []
    if app_env != "local":
        if LOCAL_EXAMPLE_SECRET_HASH in settings.auth_clients.values():
            problems.append("AUTH_CLIENTS contains the local example client from .env.example")
        if settings.jwt_secret and LOCAL_EXAMPLE_JWT_MARKER in settings.jwt_secret.get_secret_value():
            problems.append("JWT_SECRET is the local example key from .env.example")
    if not settings.auth_clients:
        problems.append("AUTH_CLIENTS is empty")
    for client_id, secret_hash in settings.auth_clients.items():
        if not CLIENT_ID.match(client_id):
            problems.append(f"AUTH_CLIENTS: client_id {client_id!r} must be 1-64 of A-Z a-z 0-9 . _ -")
        if not SECRET_HASH.match(secret_hash):
            problems.append(f"AUTH_CLIENTS: the value for {client_id!r} must be the SHA-256 hex of its secret")
    for name, value in (("JWT_SECRET", settings.jwt_secret), ("JWT_PREVIOUS_SECRET", settings.jwt_previous_secret)):
        if value is None:
            if name == "JWT_SECRET":
                problems.append("JWT_SECRET is not set")
        elif len(value.get_secret_value()) < MIN_JWT_SECRET_LENGTH:
            problems.append(f"{name} must be at least {MIN_JWT_SECRET_LENGTH} characters")
    if not MIN_TTL_SECONDS <= settings.jwt_ttl_seconds <= MAX_TTL_SECONDS:
        problems.append(f"JWT_TTL_SECONDS must be {MIN_TTL_SECONDS}-{MAX_TTL_SECONDS}")
    return problems


# --- Issuing tokens ----------------------------------------------------------------------------

def issue_token(client_id: str, settings: Settings) -> str:
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": client_id, "scope": SCOPE,
              "iat": now, "nbf": now, "exp": now + settings.jwt_ttl_seconds, "jti": uuid.uuid4().hex}
    return jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm=ALGORITHM)


class OAuthError(Exception):
    """An RFC 6749 section 5.2 error from the token endpoint."""

    def __init__(self, error: str, description: str, status_code: int = status.HTTP_400_BAD_REQUEST) -> None:
        self.error, self.description, self.status_code = error, description, status_code

    def response(self) -> JSONResponse:
        headers = {"Pragma": "no-cache"}
        if self.status_code == status.HTTP_401_UNAUTHORIZED:
            headers["WWW-Authenticate"] = f"Basic {REALM}"
        return JSONResponse({"error": self.error, "error_description": self.description},
                            status_code=self.status_code, headers=headers)


def _invalid_request(description: str) -> OAuthError:
    return OAuthError("invalid_request", description)


def _invalid_client() -> OAuthError:
    return OAuthError("invalid_client", "Client authentication failed", status.HTTP_401_UNAUTHORIZED)


async def _token_params(request: Request) -> dict[str, str]:
    """The token request's parameters: form-encoded as RFC 6749 requires, or a JSON object."""
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    body = await request.body()
    try:
        if content_type == "application/x-www-form-urlencoded":
            pairs = parse_qsl(body.decode(), keep_blank_values=True)
        elif content_type == "application/json":
            data = json.loads(body)
            if not isinstance(data, dict) or not all(isinstance(v, str) for v in data.values()):
                raise _invalid_request("The JSON body must be an object of strings")
            pairs = list(data.items())
        else:
            raise _invalid_request("Send application/x-www-form-urlencoded or application/json")
    except (UnicodeDecodeError, ValueError):
        raise _invalid_request("The body could not be parsed") from None
    try:
        for name, value in pairs:
            (name + value).encode()
    except UnicodeEncodeError:  # e.g. a lone surrogate written as \ud800 in JSON
        raise _invalid_request("Parameters must be valid UTF-8") from None
    params = dict(pairs)
    if len(params) != len(pairs):
        raise _invalid_request("A parameter was sent more than once")
    return params


def _basic_credentials(request: Request) -> tuple[str, str] | None:
    scheme, value = get_authorization_scheme_param(request.headers.get("Authorization"))
    if not scheme:
        return None
    if scheme.lower() != "basic":
        raise _invalid_client()
    try:
        client_id, sep, secret = base64.b64decode(value, validate=True).decode().partition(":")
    except ValueError:  # bad base64 (binascii.Error), non-ASCII header or non-UTF-8 credentials
        raise _invalid_client() from None
    if not sep:
        raise _invalid_client()
    # RFC 6749 section 2.3.1: both parts are form-encoded before Basic encoding.
    return unquote_plus(client_id), unquote_plus(secret)


def _authenticate(client_id: str, secret: str, settings: Settings) -> bool:
    stored = settings.auth_clients.get(client_id) if auth_enabled(settings) else None
    matches = hmac.compare_digest(hash_secret(secret), stored or _UNKNOWN_CLIENT_HASH)
    return matches and stored is not None


auth_router = APIRouter(prefix="/v1/auth", tags=["auth"])


@auth_router.post(
    "/token",
    summary="Get an access token (OAuth2 client credentials)",
    response_model=TokenResponse,
    responses={**TOKEN_RESPONSE, **TOKEN_ERRORS},
    openapi_extra={"requestBody": TOKEN_REQUEST_BODY},
)
async def token(request: Request, settings: Annotated[Settings, Depends(get_settings)]) -> JSONResponse:
    """Exchange client_id and client_secret for a Bearer token valid for 1 hour.

    Send `grant_type=client_credentials` form-encoded (RFC 6749) or as JSON, with the client's
    credentials in HTTP Basic auth or as `client_id` and `client_secret` in the body (not both).
    Reuse the token until it expires; this endpoint has its own, much lower, rate limit.
    """
    try:
        params = await _token_params(request)
        basic = _basic_credentials(request)
        if basic and ("client_secret" in params or "client_id" in params):
            raise _invalid_request("Send the client credentials in one place: Basic auth or the body")
        grant_type = params.get("grant_type")
        if not grant_type:
            raise _invalid_request("grant_type is required")
        if grant_type != "client_credentials":
            raise OAuthError("unsupported_grant_type", "Only client_credentials is supported")
        client_id, secret = basic or (params.get("client_id", ""), params.get("client_secret", ""))
        if not client_id or not secret:
            raise _invalid_client()
        requested = set(params.get("scope", "").split()) or {SCOPE}  # absent or blank: the default scope
        if not requested <= {SCOPE}:
            raise OAuthError("invalid_scope", f"The only scope is {SCOPE}")
        if not _authenticate(client_id, secret, settings):
            log.warning("Token refused: bad client credentials (client_id=%r)", client_id[:64])
            raise _invalid_client()
    except OAuthError as exc:
        return exc.response()
    log.info("Token issued to client %s", client_id)
    body = {"access_token": issue_token(client_id, settings), "token_type": "Bearer",
            "expires_in": settings.jwt_ttl_seconds, "scope": SCOPE}
    return JSONResponse(body, headers={"Pragma": "no-cache"})


# --- Checking tokens ---------------------------------------------------------------------------

def _unauthorized(detail: str, error: str | None = None) -> HTTPException:
    challenge = f'Bearer {REALM}' + (f', error="{error}", error_description="{detail}"' if error else "")
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail=detail, headers={"WWW-Authenticate": challenge})


def verify_token(token: str, settings: Settings) -> dict:
    """The token's claims, or 401 (bad or expired token) / 403 (missing scope)."""
    keys = [k.get_secret_value() for k in (settings.jwt_secret, settings.jwt_previous_secret) if k]
    for key in keys:
        try:
            claims = jwt.decode(token, key, algorithms=[ALGORITHM], audience=AUDIENCE, issuer=ISSUER,
                                leeway=LEEWAY_SECONDS, options={"require": REQUIRED_CLAIMS})
        except jwt.InvalidSignatureError:
            continue  # try the previous key while rotating
        except jwt.ExpiredSignatureError:
            raise _unauthorized("Token expired", "invalid_token") from None
        except jwt.InvalidTokenError:
            raise _unauthorized("Invalid token", "invalid_token") from None
        break
    else:
        raise _unauthorized("Invalid token", "invalid_token")
    if claims["sub"] not in settings.auth_clients:
        raise _unauthorized("Invalid token", "invalid_token")
    if SCOPE not in str(claims.get("scope", "")).split():
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Insufficient scope", headers={
            "WWW-Authenticate": f'Bearer {REALM}, error="insufficient_scope", scope="{SCOPE}"'})
    return claims


async def require_token(
    authorization: Annotated[str | None, Security(oauth2_scheme)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> str | None:
    """The calling client's id. Auth is off only on a local machine with no auth settings (see app.main)."""
    if not auth_enabled(settings):
        return None
    scheme, token = get_authorization_scheme_param(authorization)
    if scheme.lower() != "bearer" or not token:
        raise _unauthorized("Not authenticated")
    return verify_token(token, settings)["sub"]


# --- CLI ---------------------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "new-client" and CLIENT_ID.match(argv[1]):
        secret = secrets.token_urlsafe(32)
        print(f"client_id:     {argv[1]}\nclient_secret: {secret}   <- give to the caller over a secure channel")
        print(f"AUTH_CLIENTS entry (server side; merge with existing clients):\n  {json.dumps({argv[1]: hash_secret(secret)})}")
        return 0
    if argv == ["new-jwt-secret"]:
        print(secrets.token_urlsafe(48))
        return 0
    print("usage: python -m app.security new-client <client_id>   (1-64 of A-Z a-z 0-9 . _ -)\n"
          "       python -m app.security new-jwt-secret", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
