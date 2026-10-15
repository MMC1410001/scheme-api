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
| API 1 | `POST /v1/schemes/current-month` | A dealer's scheme rows for a given month and year |
| Health | `GET /health` | Liveness check |

### 1.2 Environments

| Environment | Base URL |
|---|---|
| Dev | *to be confirmed* |
| Production | *to be confirmed* |

All production traffic must use HTTPS.

### 1.3 Authentication

Send your API key in the `X-API-Key` header on every `/v1` request. The key is shared with you
separately; never put it in URLs, logs or source code. `/health` does not need a key.

```
X-API-Key: <your key>
```

A missing or wrong key returns `401 Unauthorized`.

### 1.4 Request format

API 1 takes the dealer code and the month:

```json
{ "dealer_code": "6100000001", "month": 10, "year": 2026 }
```

| Field | Type | Rules |
|---|---|---|
| `dealer_code` | string or number | Required. SAP customer code, letters and digits only, 1–20 characters. Leading zeros and surrounding spaces are ignored, so `"0006100000001"`, `" 6100000001 "` and `6100000001` are the same dealer. |
| `month` | integer (or numeric string) | Required. 1–12. `10` and `"10"` are both accepted; `true`, `9.5` or `"Oct"` are rejected. |
| `year` | integer (or numeric string) | Required. Four digits, 2000–2100. |

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
  -H "X-API-Key: $API_KEY" \
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
    "ShortfallPct": 0.0
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
    "ShortfallPct": 21.23
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

Amounts are JSON numbers and can be negative (for example after returns). Any amount may be
`null` if SAP left it blank.

**Ordering:** by `ToDate` (earliest first), then in the component order listed above. So EB
SLAB 1, 2 and 3 (which end mid-month) come first and EB FULL MONTH comes last.

**Freshness:** each row shows SAP's most recent update for that component. A component appears
once SAP has exported it for the month, so early in the month a dealer may have fewer than 8 rows.

**Months:** a row belongs to the month of the SAP export folder it came from. A new month's data
appears once its first SAP export has been loaded (usually within hours of the 1st); until then
that month returns `[]`. Earlier months stay available.

**Cumulative amounts:** EB amounts are running totals from the 1st. If EB SLAB 1 (1–10 Oct) shows
10 lakh and EB SLAB 2 (1–15 Oct) shows 12 lakh, the total is 12 lakh, not 22. Do not add the slabs.

**No data:** a dealer with no rows in that month (or an unknown dealer code) returns `200 OK` with `[]`.

### 1.6 Errors

Errors are JSON with a `detail` field. They never contain stack traces or echo your input back.

| Status | When | Example body |
|---|---|---|
| `401` | Missing or wrong `X-API-Key` | `{"detail": "Invalid or missing API key"}` |
| `404` | Unknown path | `{"detail": "Not Found"}` |
| `405` | Wrong HTTP method (e.g. `GET` on a `POST` endpoint) | `{"detail": "Method Not Allowed"}` |
| `413` | Body larger than 4 KB | `{"detail": "Request body too large"}` |
| `422` | Invalid body: missing/empty `dealer_code`, invalid characters, too long; missing or out-of-range `month`/`year`; invalid JSON | `{"detail": [{"loc": ["body", "dealer_code"], "msg": "String should match pattern '^[A-Za-z0-9]+$'", "type": "string_pattern_mismatch"}]}` |
| `503` | Temporarily overloaded. Retry after the `Retry-After` seconds. | `{"detail": "Service busy, please retry"}` |
| `500` | Unexpected server error. Please report it with the time of the request. | `{"detail": "Internal server error"}` |

**Retry advice:** retry `503` (and network timeouts) with backoff, starting from `Retry-After`.
Do not retry `4xx` errors without fixing the request.

### 1.7 Using Postman

1. In Postman, **Import** the API 1 collection from `qa/postman/` and an environment
   file (`Dev.postman_environment.json`).
2. Select the environment, then set `baseUrl`, `apiKey` and `dealerCode` (a dealer with data).
3. Open the collection, then **2. Monthly schemes**,
   pick a request and click **Send**. Each request has saved example responses under **Examples**.
4. To run every check, open the collection and click **Run**. Each request carries automatic
   tests (status, fields, ordering, headers).

### 1.8 Code examples

**Python**

```python
import requests

response = requests.post(
    f"{BASE_URL}/v1/schemes/current-month",
    json={"dealer_code": "6100000001", "month": 10, "year": 2026},
    headers={"X-API-Key": API_KEY},
    timeout=15,
)
response.raise_for_status()
for row in response.json():
    print(row["ComponentDescription"], row["Qualification"], row["GsrB"])
```

**JavaScript (Node 18+)**

```javascript
const response = await fetch(`${BASE_URL}/v1/schemes/current-month`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', 'X-API-Key': API_KEY },
  body: JSON.stringify({ dealer_code: '6100000001', month: 10, year: 2026 }),
});
if (!response.ok) throw new Error(`HTTP ${response.status}`);
const rows = await response.json();
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
cd qa/api-tests && npm install && API_KEY=<key> AUTH_ENABLED=true npx playwright test

# k6
cd qa/k6 && API_KEY=<key> k6 run smoke.js && API_KEY=<key> k6 run load.js

# Security scans
API_KEY=<key> qa/security/run_scans.sh
```

### 2.3 Acceptance criteria

| Area | Criterion |
|---|---|
| Functional | All Postman assertions and Playwright tests pass |
| Performance | Error rate < 1%, checks > 99%, p95 < 500 ms and p99 < 1 s per endpoint at expected load |
| Security | No bandit findings, no known dependency CVEs, no ZAP FAIL alerts |
