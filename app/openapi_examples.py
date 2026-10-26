"""Request and response examples shown in Swagger (/docs) and in the qa/swagger specs.

Values are made up (they match scripts/make_sample_data.py), never client data. Request examples
whose key starts with "invalid_" must be rejected with 422; tests/test_swagger_specs.py checks both.
"""
from app.schemas import ErrorMessage, OAuthErrorResponse, TokenResponse, ValidationErrorResponse

API1_REQUESTS = {
    "valid": {"summary": "Dealer and month", "value": {"dealer_code": "6100000001", "month": 10, "year": 2026}},
    "numbers_as_strings": {
        "summary": "Dealer code as a number, month and year as strings",
        "value": {"dealer_code": 6100000001, "month": "10", "year": "2026"},
    },
    "leading_zeros": {
        "summary": "Leading zeros and spaces are ignored",
        "value": {"dealer_code": "  0006100000001 ", "month": 10, "year": 2026},
    },
    "invalid_month": {"summary": "Month 13 (422)", "value": {"dealer_code": "6100000001", "month": 13, "year": 2026}},
    "invalid_missing_year": {"summary": "Year missing (422)", "value": {"dealer_code": "6100000001", "month": 10}},
}


def _row(component, to_date, qualification, gsr_b, rate, clear_cut=0.0, shortfall=0.0, shortfall_pct=0.0, next_rate=0.0):
    return {"Customer": "6100000001", "FromDate": "2026-10-01", "ToDate": to_date, "ComponentDescription": component,
            "Qualification": qualification, "GsrB": gsr_b, "RebateRate": rate, "ClearCut": clear_cut,
            "ShortfallAmount": shortfall, "ShortfallPct": shortfall_pct, "NextSlabRate": next_rate}


API1_ROWS = {
    "rows": {
        "summary": "Dealer with data, by ClearCut highest first (3 of the 10 components and two others shown)",
        "value": [
            # Outside the 10: QualificationBasis says what Qualification holds; InvoicedQty is sent too.
            {**_row("ROLLER", "2026-10-31", 11350.0, 11350.0, 26.0, 2951.0), "QualificationBasis": "GSR",
             "InvoicedQty": 50.0},
            _row("LUXURY", "2026-10-31", 315.1, 159292.8, 1.0, 1592.93, 84.9, 21.23, 1.5),
            _row("EB SLAB 1", "2026-10-11", 405232.86, 405232.86, 0.75, next_rate=1.0),
            _row("EB FULL MONTH", "2026-10-31", 405232.86, 405232.86, 2.25),
            {**_row("ALWD EB2", "2026-10-31", 12.5, 405232.86, 1.0), "QualificationBasis": "INVOICE_QTY",
             "InvoicedQty": 12.5},
        ],
    },
    "empty": {"summary": "No data for this dealer or month", "value": []},
}


def _error(description, detail, **extra):
    return {"model": ErrorMessage, "description": description,
            "content": {"application/json": {"example": {"detail": detail}}}, **extra}


ERRORS = {
    401: _error("Bearer token missing, invalid or expired (get a new one from /v1/auth/token)", "Token expired",
                headers={"WWW-Authenticate": {"description": "RFC 6750 challenge", "schema": {"type": "string"}}}),
    403: _error("The token lacks the schemes:read scope", "Insufficient scope"),
    413: _error("Request body larger than MAX_BODY_BYTES", "Request body too large"),
    422: {
        "model": ValidationErrorResponse,
        "description": "The body failed validation",
        "content": {"application/json": {"example": {"detail": [
            {"loc": ["body", "dealer_code"], "msg": "Field required", "type": "missing"}]}}},
    },
    429: _error("More than RATE_LIMIT_PER_SECOND requests from this IP in one second; retry after Retry-After",
                "Too many requests",
                headers={"Retry-After": {"description": "Seconds to wait", "schema": {"type": "integer"}}}),
    500: _error("Unexpected server error; details are logged, not returned", "Internal server error"),
    503: _error("No free database connection; retry after the Retry-After seconds", "Service busy, please retry",
                headers={"Retry-After": {"description": "Seconds to wait", "schema": {"type": "integer"}}}),
}


HEALTH_RESPONSES = {
    200: {"description": "The API is up and its database answers",
          "content": {"application/json": {"example": {"ok": True}}}},
    503: {"description": "The database did not answer within 2 seconds",
          "content": {"application/json": {"example": {"ok": False, "detail": "Database unreachable"}}},
          "headers": {"Retry-After": {"description": "Seconds to wait", "schema": {"type": "integer"}}}},
}


def ok(examples):
    return {200: {"description": "The dealer's rows (empty list when there are none)",
                  "content": {"application/json": {"examples": examples}}}}


_TOKEN_FIELDS = {
    "grant_type": {"type": "string", "enum": ["client_credentials"]},
    "client_id": {"type": "string", "description": "Omit when sent with HTTP Basic auth"},
    "client_secret": {"type": "string", "format": "password", "description": "Omit when sent with HTTP Basic auth"},
    "scope": {"type": "string", "enum": ["schemes:read"], "description": "Optional; the only scope"},
}
_TOKEN_SCHEMA = {"type": "object", "required": ["grant_type"], "properties": _TOKEN_FIELDS}
TOKEN_REQUEST_BODY = {
    "required": True,
    "content": {
        "application/x-www-form-urlencoded": {"schema": _TOKEN_SCHEMA},
        "application/json": {"schema": _TOKEN_SCHEMA, "example": {
            "grant_type": "client_credentials", "client_id": "partner", "client_secret": "<client secret>"}},
    },
}
TOKEN_RESPONSE = {200: {"description": "Access token; reuse it until expires_in seconds have passed",
                        "content": {"application/json": {"example": {
                            "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...", "token_type": "Bearer",
                            "expires_in": 3600, "scope": "schemes:read"}}}}}


def _oauth_error(description, error, error_description):
    return {"model": OAuthErrorResponse, "description": description, "content": {"application/json": {
        "example": {"error": error, "error_description": error_description}}}}


TOKEN_ERRORS = {
    400: _oauth_error("Malformed request, wrong grant_type or scope (RFC 6749 errors)",
                      "unsupported_grant_type", "Only client_credentials is supported"),
    401: _oauth_error("Unknown client or wrong secret", "invalid_client", "Client authentication failed"),
    413: ERRORS[413],
    429: _error("More than TOKEN_RATE_LIMIT_PER_MINUTE token requests from this IP in a minute", "Too many requests",
                headers={"Retry-After": {"description": "Seconds to wait", "schema": {"type": "integer"}}}),
    500: ERRORS[500],
}
