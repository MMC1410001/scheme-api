# Scheme API — Integration & Testing Guide

Version 0.1.0 · Base path `/v1`

This guide explains what the Scheme API returns, how to call it, and how to test it.
Part 1 is for anyone integrating with the API; Part 2 is for QA.

Also in this repository:

| File | Use |
|---|---|
| `qa/postman/Scheme_API1_Current_Month.postman_collection.json` | Postman collection for API 1: ready-made requests, tests and example responses |
| `qa/postman/*.postman_environment.json` | Postman environments (Local, Dev) for the collection |
| `qa/swagger/Scheme_API1_Current_Month.openapi.json` | Swagger (OpenAPI 3.1) spec for API 1, with request examples, example responses and error codes |
| `docs/openapi.json` | OpenAPI 3 specification for the API (import into Swagger UI, Postman, or a code generator) |
| `/docs` on a running server | Interactive Swagger UI, when enabled for that environment |

---

## Part 1 — Using the API

### 1.1 What the API provides

Scheme data, per dealer. API 1 serves the month you ask for from the SAP scheme exports,
refreshed automatically as new exports arrive (several times a day).

| API | Endpoint | Purpose |
|---|---|---|
| Auth | `POST /v1/auth/token` | Get an access token (OAuth2 client credentials) |
| API 1 | `POST /v1/schemes/current-month` | A dealer's scheme rows for a given month and year |
| Health | `GET /health` | Liveness check |

### 1.2 Environments

| Environment | Base URL |
|---|---|
| Dev | *to be confirmed* |
| Production | *to be confirmed* |

All production traffic must use HTTPS.

### 1.3 Authentication

The API uses OAuth2 client credentials (RFC 6749) with short-lived JWT Bearer tokens (RFC 6750).
You receive a `client_id` and a `client_secret` per environment through a secure channel. Keep the
secret in a secret store; never put it in URLs, logs or source code. `/health` needs no token.

**Step 1 — get a token.** `POST /v1/auth/token` with `grant_type=client_credentials`, form-encoded
(the OAuth2 standard) or as JSON. Send the credentials either as HTTP Basic auth or as `client_id`
and `client_secret` in the body, not both.

```bash
curl -X POST "$BASE_URL/v1/auth/token" -u "$CLIENT_ID:$CLIENT_SECRET" \
  -d grant_type=client_credentials
```

```json
{"access_token": "eyJhbGciOiJIUzI1NiIs...", "token_type": "Bearer", "expires_in": 3600, "scope": "schemes:read"}
```

`scope` is optional; the only scope is `schemes:read`.

**Step 2 — call the API** with the token in the `Authorization` header:

```
Authorization: Bearer <access_token>
```

**Reuse the token.** A token is valid for `expires_in` seconds (1 hour). Keep it in memory and send
it on every call until shortly before it expires (about 60 seconds before), then get a new one.
Do not request a token per call: the token endpoint allows only 30 requests a minute per IP. If a
call returns `401` with `"detail": "Token expired"`, get a new token and retry that call once.

**Authentication errors**

| Endpoint | Status | Body | What to do |
|---|---|---|---|
| `/v1/auth/token` | `400` | `{"error": "invalid_request", "error_description": "..."}` (missing `grant_type`, unreadable body, credentials in both Basic and body, repeated (form field or JSON key) parameter) | Fix the request |
| `/v1/auth/token` | `400` | `{"error": "unsupported_grant_type", ...}` | Send `grant_type=client_credentials` |
| `/v1/auth/token` | `400` | `{"error": "invalid_scope", ...}` | Omit `scope` or send `schemes:read` |
| `/v1/auth/token` | `401` | `{"error": "invalid_client", "error_description": "Client authentication failed"}` | Check `client_id` and `client_secret` (a wrong id and a wrong secret look the same) |
| `/v1/auth/token` | `429` | `{"detail": "Too many requests"}` + `Retry-After` | More than 30 token requests a minute from your IP: cache the token |
| `/v1/schemes/...` | `401` | `{"detail": "Not authenticated"}` | Send `Authorization: Bearer <token>` |
| `/v1/schemes/...` | `401` | `{"detail": "Invalid token"}` | Get a new token; if that fails with `invalid_client`, contact us |
| `/v1/schemes/...` | `401` | `{"detail": "Token expired"}` | Get a new token and retry once |
| `/v1/schemes/...` | `403` | `{"detail": "Insufficient scope"}` | The token lacks `schemes:read`; get a token without a `scope` parameter |

`401` and `403` responses also carry a `WWW-Authenticate: Bearer ...` header (RFC 6750). Token
responses are never cached (`Cache-Control: no-store`).

### 1.4 Request format

API 1 takes the dealer code and the month:

```json
{ "dealer_code": "6100000001", "month": 10, "year": 2026 }
```

| Field | Type | Rules |
|---|---|---|
| `dealer_code` | string or number | Required. SAP customer code, letters and digits only, 1–20 characters. Leading zeros and surrounding spaces are ignored, so `"0006100000001"`, `" 6100000001 "` and `6100000001` are the same dealer. |
| `month` | integer (or numeric string) | Required. 1–12. One or two digits: `9`, `09`, `"9"`, `"09"`, `10` are accepted; `-9`, `9.5`, `0`, `009`, `010`, `0010`, `true` or `"Oct"` are rejected. |
| `year` | integer (or numeric string) | Required. Four digits, 2000–2100: `2026` or `"2026"`; `02026`, `"26"`, `-2026`, `2026.0` or `" 2026 "` are rejected. |

Send `Content-Type: application/json`. Bodies larger than 4 KB are rejected.

### 1.5 API 1 — Monthly schemes

`POST /v1/schemes/current-month`

Returns one row per scheme component for the dealer, for the month and year in the request.
Only these 8 components are returned:

`EB SLAB 1`, `EB SLAB 2`, `EB SLAB 3`, `VALUE`, `LUXURY`, `PREMIUM 01`, `PREMIUM 02`, `EB FULL MONTH`

**Example request**

```bash
curl -X POST "$BASE_URL/v1/schemes/current-month" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"dealer_code": "6100000001", "month": 10, "year": 2026}'
```

**Example response** `200 OK` (shortened to 2 of the 8 rows)

```json
[
  {
    "Customer": "6100000001",
    "FromDate": "2026-10-01",
    "ToDate": "2026-10-11",
    "ComponentDescription": "EB SLAB 1",
    "Qualification": 405232.86,
    "GsrB": 405232.86,
    "RebateRate": 0.75,
    "ClearCut": 0.0,
    "ShortfallAmount": 0.0,
    "ShortfallPct": 0.0,
    "NextSlabRate": 1.0
  },
  {
    "Customer": "6100000001",
    "FromDate": "2026-10-01",
    "ToDate": "2026-10-31",
    "ComponentDescription": "LUXURY",
    "Qualification": 315.1,
    "GsrB": 159292.8,
    "RebateRate": 1.0,
    "ClearCut": 1592.93,
    "ShortfallAmount": 84.9,
    "ShortfallPct": 21.23,
    "NextSlabRate": 1.5
  }
]
```

**Response fields**

| Field | Type | Meaning | SAP source column |
|---|---|---|---|
| `Customer` | string | Dealer (SAP customer) code | Customer |
| `FromDate` | date `YYYY-MM-DD` | Start of the component's validity | Valid from |
| `ToDate` | date `YYYY-MM-DD` | End of the component's validity | To |
| `ComponentDescription` | string | One of the 8 components above | Comp desc |
| `Qualification` | number | **GSR** for EB slabs, EB Full Month and Value; **invoiced quantity** for Luxury and Premium | GSR (Q); for EB Full Month GSR (B), the same GSR the slabs show; Invoiced Quantity(Q) |
| `GsrB` | number | Gross Sales Revenue used for the benefit | GSR (B) |
| `RebateRate` | number | Rebate rate | Rebate Rate |
| `ClearCut` | number | Clear-cut credit note amount | Clear Cut CN |
| `ShortfallAmount` | number | Shortfall to the next slab | Short Fall |
| `ShortfallPct` | number | Shortfall as a percentage | Short Fall % |
| `NextSlabRate` | number | Rebate rate of the next slab | Next Slab Rate |

Amounts are JSON numbers and can be negative (for example after returns). Any amount may be
`null` if SAP left it blank.

**Ordering:** by `ToDate` (earliest first), then in the component order listed above. So EB
SLAB 1, 2 and 3 (which end mid-month) come first and EB FULL MONTH comes last.

**Freshness:** each row shows SAP's most recent update for that component. A component appears
once SAP has exported it for the month, so early in the month a dealer may have fewer than 8 rows.

**Months:** a row belongs to every month its validity period (`FromDate`–`ToDate`) overlaps, not to
the SAP export folder it came from: SAP keeps updating last month's schemes in the first days of the
new month's folder, and those rows stay in last month. If an agreement was updated in two folders, the
newest update is returned. Rows without dates fall back to their folder's month. A new month's data
appears once its first SAP export has been loaded (usually within hours of the 1st); until then
that month returns `[]`. Earlier months stay available.

**Cumulative amounts:** EB amounts are running totals from the 1st. If EB SLAB 1 (1–10 Oct) shows
10 lakh and EB SLAB 2 (1–15 Oct) shows 12 lakh, the total is 12 lakh, not 22. Do not add the slabs.

**No data:** a dealer with no rows in that month (or an unknown dealer code) returns `200 OK` with `[]`.

### 1.6 Errors

Errors are JSON with a `detail` field (the token endpoint uses the OAuth2 `error` /
`error_description` format instead, see 1.3). They never contain stack traces or echo your input back.

| Status | When | Example body |
|---|---|---|
| `401` | Missing, invalid or expired Bearer token (see 1.3) | `{"detail": "Token expired"}` |
| `403` | Token without the `schemes:read` scope | `{"detail": "Insufficient scope"}` |
| `404` | Unknown path | `{"detail": "Not Found"}` |
| `405` | Wrong HTTP method (e.g. `GET` on a `POST` endpoint) | `{"detail": "Method Not Allowed"}` |
| `413` | Body larger than 4 KB | `{"detail": "Request body too large"}` |
| `422` | Invalid body: missing/empty `dealer_code`, invalid characters, too long; missing or out-of-range `month`/`year`; invalid JSON | `{"detail": [{"loc": ["body", "dealer_code"], "msg": "String should match pattern '^[A-Za-z0-9]+$'", "type": "string_pattern_mismatch"}]}` |
| `429` | More than 2,500 requests in one second from your IP. Retry after the `Retry-After` seconds (1). | `{"detail": "Too many requests"}` |
| `503` | Temporarily overloaded. Retry after the `Retry-After` seconds. | `{"detail": "Service busy, please retry"}` |
| `500` | Unexpected server error. Please report it with the time of the request. | `{"detail": "Internal server error"}` |

**Retry advice:** retry `429` and `503` (and network timeouts) with backoff, starting from `Retry-After`.
On `401 Token expired`, get a new token and retry once. Do not retry other `4xx` errors without
fixing the request.

### 1.7 Using Postman

1. In Postman, **Import** the API 1 collection from `qa/postman/` and an environment
   file (`Dev.postman_environment.json`).
2. Select the environment, then set `baseUrl`, `clientId`, `clientSecret` and `dealerCode` (a
   dealer with data). The collection's pre-request script gets a token from `/v1/auth/token` and
   reuses it until shortly before it expires, so you never handle the token yourself.
3. Open the collection, then **2. Monthly schemes**,
   pick a request and click **Send**. Each request has saved example responses under **Examples**.
4. To run every check, open the collection and click **Run**. Each request carries automatic
   tests (status, fields, ordering, headers).

### 1.8 Code examples

Both examples keep one token in memory, get a new one about 60 seconds before it expires, and
retry a call once after `401 Token expired`. `BASE_URL`, `CLIENT_ID` and `CLIENT_SECRET` come from
your configuration or secret store. In a multi-threaded or multi-process service, share the token
(and refresh it under a lock) rather than fetching one per thread.

**Python (requests)**

```python
import time
import requests

session = requests.Session()
_token = {"value": None, "expires_at": 0.0}


def get_token(force: bool = False) -> str:
    if force or time.time() >= _token["expires_at"] - 60:
        r = session.post(f"{BASE_URL}/v1/auth/token", data={"grant_type": "client_credentials"},
                         auth=(CLIENT_ID, CLIENT_SECRET), timeout=15)
        r.raise_for_status()
        body = r.json()
        _token.update(value=body["access_token"], expires_at=time.time() + body["expires_in"])
    return _token["value"]


def current_month(dealer_code: str, month: int, year: int) -> list[dict]:
    for attempt in range(2):
        r = session.post(f"{BASE_URL}/v1/schemes/current-month",
                         json={"dealer_code": dealer_code, "month": month, "year": year},
                         headers={"Authorization": f"Bearer {get_token(force=attempt > 0)}"},
                         timeout=15)
        if r.status_code == 401 and attempt == 0 and r.json().get("detail") == "Token expired":
            continue  # get a new token and retry once
        r.raise_for_status()
        return r.json()


for row in current_month("6100000001", 10, 2026):
    print(row["ComponentDescription"], row["Qualification"], row["GsrB"])
```

**JavaScript (Node 18+, fetch)**

```javascript
let token = null;
let expiresAt = 0;

async function getToken(force = false) {
  if (force || Date.now() >= expiresAt - 60_000) {
    const res = await fetch(`${BASE_URL}/v1/auth/token`, {
      method: 'POST',
      body: new URLSearchParams({
        grant_type: 'client_credentials', client_id: CLIENT_ID, client_secret: CLIENT_SECRET,
      }),
    });
    if (!res.ok) throw new Error(`Token request failed: HTTP ${res.status}`);
    const body = await res.json();
    token = body.access_token;
    expiresAt = Date.now() + body.expires_in * 1000;
  }
  return token;
}

async function currentMonth(dealerCode, month, year) {
  for (let attempt = 0; attempt < 2; attempt++) {
    const res = await fetch(`${BASE_URL}/v1/schemes/current-month`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${await getToken(attempt > 0)}`,
      },
      body: JSON.stringify({ dealer_code: dealerCode, month, year }),
    });
    if (res.status === 401 && attempt === 0) {
      const { detail } = await res.json();
      if (detail === 'Token expired') continue; // get a new token and retry once
      throw new Error(`HTTP 401: ${detail}`);
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }
}

const rows = await currentMonth('6100000001', 10, 2026);
```

### 1.9 FAQ

**Why do I get `[]`?** The dealer has no scheme data for that month, or the code is wrong. Check the
code against SAP (leading zeros don't matter) and the month and year. A new month returns `[]`
until its first SAP export has been loaded.

**Why fewer than 8 rows?** A component appears after SAP exports it for the month. Early in the
month some components may not have been exported yet.

**Can I ask for a previous month?** Yes: send its `month` and `year`.

**Is the data real-time?** It updates as SAP exports arrive (several times a day), not instantly.

**Can I send the dealer code as a number?** Yes. `6100000001` and `"6100000001"` both work.

**Why is EB SLAB 2 larger than EB SLAB 1?** EB amounts are running totals from the 1st of the month.
If EB SLAB 1 (1–10 Oct) shows 10 lakh and EB SLAB 2 (1–15 Oct) shows 12 lakh, 2 lakh were added
between the 10th and the 15th; the total is 12 lakh, not 22. Do not add the slabs together.

---

## Part 2 — Testing the API (QA)

Everything lives under `qa/`. Start with the step-by-step **QA guide** at the top of
`qa/README.md` (local setup, Postman, automated suites, testing on Dev, reporting bugs); the test
case index and full commands follow it. Latest results: `qa/TEST_RESULTS.md`.

| Layer | Tool | Location | What it covers |
|---|---|---|---|
| Unit | pytest | `tests/` | 123 tests (+ 13 database tests): parsing rules, response shape, security middleware, Redis cache, Postman and Swagger coverage (no server needed) |
| Functional & negative | Postman / newman | `qa/postman/` | API 1 collection: 27 requests / 115 assertions |
| Full API suite | Playwright | `qa/api-tests/` | 69 cases: `api1_current_month.spec.js` (65), `common.spec.js` (4) |
| Performance | k6 | `qa/k6/` | Smoke, load (150 VUs), stress (600 VUs), spike (500 VUs), fixed rate (`rate.js`, 1,700 requests/s = 1 lakh a minute); API 1 |
| Security scans | bandit, pip-audit, OWASP ZAP | `qa/security/` | Static analysis, dependency CVEs, active API scan |

### 2.1 Test data

Locally, `uv run python scripts/make_sample_data.py --perf-dealers 20000` creates made-up SAP
exports. Dealers to use:

| Dealer | API 1 |
|---|---|
| `6100000001` | All 8 components; LUXURY updated on day 2, so the newer values must be returned |
| `6100000002` | Negative SAP values (`882.00-` in the file becomes `-882`) |
| `6200000001`–`6200020000` | Load-test dealers |
| `6199999999` | No data (expects `[]`) |

On Dev/Production use real dealer codes, set `SAMPLE_DATA=false` for Playwright so the
sample-value checks are skipped, and `MONTH`/`YEAR` to a month with data (locally they default to
the current month, where the sample data is).

### 2.2 Quick start

```bash
# Postman (headless)
cd qa/postman
npx newman run Scheme_API1_Current_Month.postman_collection.json -e Local.postman_environment.json

# Playwright (all, or one file with npm run test:api1 / test:common)
cd qa/api-tests && npm install && CLIENT_ID=<id> CLIENT_SECRET=<secret> AUTH_ENABLED=true npx playwright test

# k6
cd qa/k6 && CLIENT_ID=<id> CLIENT_SECRET=<secret> k6 run smoke.js && CLIENT_ID=<id> CLIENT_SECRET=<secret> k6 run load.js

# Security scans
CLIENT_ID=<id> CLIENT_SECRET=<secret> qa/security/run_scans.sh
```

Locally the client is `CLIENT_ID=local-client CLIENT_SECRET=local-test-secret`. The suites get a
token from `/v1/auth/token` themselves.

### 2.3 Acceptance criteria

| Area | Criterion |
|---|---|
| Functional | All Postman assertions and Playwright tests pass |
| Performance | Error rate < 1%, checks > 99%, p95 < 500 ms and p99 < 1 s per endpoint at expected load |
| Security | No bandit findings, no known dependency CVEs, no ZAP FAIL alerts |
