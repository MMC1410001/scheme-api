# Scheme API — QA Suite

Functional, security and performance testing for the Scheme API.
Latest results: [TEST_RESULTS.md](TEST_RESULTS.md). API guide: [../docs/API_GUIDE.md](../docs/API_GUIDE.md).

The API has one endpoint, API 1 — `POST /v1/schemes/current-month` (body `{dealer_code, month, year}`):

| | API 1 — `POST /v1/schemes/current-month` |
|---|---|
| Postman | `postman/Scheme_API1_Current_Month.postman_collection.json` |
| Swagger | `swagger/Scheme_API1_Current_Month.openapi.json` |
| Playwright | `api-tests/tests/api1_current_month.spec.js` (`API1_TC_n`) |
| k6 | `k6 run <script>.js` |

Also in the suite: the Postman environments (`Local`, `Dev`), `api-tests/tests/common.spec.js` (health,
unknown route, HTTPS; `COMMON_TC_n`), `api-tests/tests/helpers.js`, and the security scans.

## QA guide (step by step)

For anyone testing the API. Steps 1–3 are manual testing in Postman; Steps 4–6 are the automated
suites; Step 7 covers testing on Dev/Production; Step 8 is reporting.

### Step 0 — Install the tools (once)

| Tool | Used for | Install on a Mac |
|---|---|---|
| Docker Desktop, Git, uv | Running the API locally | See Step 0 in the [main README](../README.md#getting-started-step-by-step) |
| Postman (desktop app) | Manual testing | Download from postman.com |
| Node.js (LTS) | Playwright tests and newman | `brew install node` |
| k6 | Performance tests | `brew install k6` |

### Step 1 — Start the API locally

Follow Steps 1–6 of the [main README](../README.md#getting-started-step-by-step). For performance
testing, create more test dealers in Step 5:

```bash
uv run python scripts/make_sample_data.py --perf-dealers 20000
uv run python -m ingestion.run schemes
```

Check it's up: `curl http://localhost:8000/health` returns `{"ok":true}`.

### Step 2 — Test manually in Postman

1. Open Postman → **Import** → select the three files in `qa/postman/`: the collection
   `Scheme_API1_Current_Month…` and the two environments (`Local…`, `Dev…`). You get
   **Scheme API 1 - Monthly schemes**, with folders 1. Health, 2. the API's requests,
   3. Negative cases, 4. Security.
2. In the environment dropdown (top right) choose **Scheme API - Local**. It already has
   `baseUrl = http://localhost:8000`, `apiKey = local-test-key`, `dealerCode = 6100000001`,
   `sampleData = true`.
3. Open the collection → **2. …** → a request → **Send**. The API key is added automatically from the
   collection's **Authorization** tab (header `X-API-Key: {{apiKey}}`).
4. Each request's checks are under **Scripts → Post-response**; saved example responses are under
   **Examples** in the sidebar (click the arrow next to the request).
5. To run everything: click the collection → **Run**. Expect 27 requests, 115 checks passed.

API 1 needs a month: its requests send `month` and `year`, which default to the current month (India
time), where the sample data is. To test another month, add `month` and `year` variables to the
environment (e.g. `9` and `2026`).

Test data to use:

| Dealer | API 1 shows |
|---|---|
| `6100000001` | All 8 components in the current month; LUXURY was updated on day 2, so the newer values must appear (Qualification 315.1, GsrB 159292.8) |
| `6100000002` | Negative values (SAP `882.00-` must come back as `-882`) |
| `6200000001`–`6200020000` | Load-test dealers (only if you created them in Step 1) |
| `6199999999` | No data: `200` with `[]` |

### Step 3 — Exploratory checks worth doing by hand

- Dealer code with leading zeros (`0006100000001`) returns the same result as without.
- Dealer code as a number (`6100000001` without quotes) works.
- Invalid codes (`6100-0001`, empty, 21+ characters) return `422` and never echo your input.
- No / wrong API key returns `401`.

- Rows are ordered by `ToDate`: EB SLAB 1, 2, 3 first, EB FULL MONTH last.
- `month` and `year` are required: leave one out, or send 13, 1999, `true` or `"Oct"` → `422`.
- A month without data (e.g. January 2000) returns `200 []`; `"10"` works the same as `10`.
- EB amounts are running totals (SLAB 2 includes SLAB 1); the API never adds rows together.

- With the cache on (`CACHE_ENABLED=true`, Redis running): the first request for a dealer and month
  has `X-Cache: MISS`, a repeat has `X-Cache: HIT` with the same body; stop Redis and requests
  still work (`X-Cache: BYPASS`).

### Step 4 — Run the automated API tests (Playwright)

```bash
cd qa/api-tests
npm install                                                   # first time only
API_KEY=local-test-key AUTH_ENABLED=true npx playwright test  # 69 cases (API 1 + common)
npm run report                                                # opens the HTML report
```

Expected: 68 passed, 1 skipped (COMMON_TC_4_SEC needs an HTTPS URL). Run one file with
`npm run test:api1` (or `test:common`); one category with
`npm run test:security` (or `:functional`, `:validation`, `:edge`, `:negative`); one case with
`npx playwright test -g "API1_TC_12"`. Put `API_KEY=… AUTH_ENABLED=true` in front of `npm run`
too. The full case list is in the index at the end of this file.

### Step 5 — Run the performance tests (k6)

Always run the smoke test first; only continue if it passes.

```bash
cd qa/k6
API_KEY=local-test-key k6 run smoke.js              # 30 s, 1 user
API_KEY=local-test-key k6 run load.js               # ~4 min, 150 users
API_KEY=local-test-key k6 run rate.js               # 2 min at 1,700 requests/s (1 lakh a minute)
```

`rate.js` sends a fixed number of requests per second, like the dealer app does when
it loads: up to 1 lakh a minute. Run it with the cache off and on (see "Response cache" in the
main README) to compare; set `RATE` and `DURATION` to change it.

The last lines of each run say `all thresholds passed` or list what failed. Pass criteria: errors
< 1%, checks > 99%, p95 < 500 ms and p99 < 1 s. `stress.js` and `spike.js` find the limits; run them
when asked. Results are saved to `qa/k6/reports/`.

### Step 6 — Run the security scans

```bash
qa/security/run_scans.sh sast deps                   # code + dependency checks, no server needed
API_KEY=local-test-key qa/security/run_scans.sh zap  # OWASP ZAP against the running API (Docker)
```

Pass = no bandit issues, no known vulnerabilities, and ZAP `FAIL-NEW: 0`. Reports go to
`qa/security/reports/`.

### Step 7 — Test on Dev or Production

- **Postman:** choose **Scheme API - Dev**, set `baseUrl`, `apiKey` (ask the developer) and a
  real `dealerCode` that has data this month. Set `authEnabled` to
  `true` if that server requires a key. Keep `sampleData = false` (skips sample-value checks).
- **Playwright:**
  `API_BASE_URL=https://<host> API_KEY=<key> AUTH_ENABLED=true SAMPLE_DATA=false DEALER_CODE=<real dealer> MONTH=<m> YEAR=<yyyy> npx playwright test`
  (`SAMPLE_DATA=false` skips the checks that only make sense for the local sample data; `MONTH` and
  `YEAR` pick a month in which that dealer has data).
- **k6:** `BASE_URL=https://<host> API_KEY=<key> DEALER_PREFIX=61 DEALER_COUNT=<n> k6 run -e MONTH=<m> -e YEAR=<yyyy> smoke.js`.
  It generates codes `61` + 8 digits; codes that don't exist on that server correctly return `[]`.
  Agree the load level with the team before running load/stress tests on a shared server.

### Step 8 — Report results and bugs

- Record each run in [TEST_RESULTS.md](TEST_RESULTS.md): date, environment, counts per suite,
  k6 numbers, scan results.
- For a bug, include: environment and base URL, the request (method, URL, body, headers without
  the API key), the response (status, body), the time, the dealer code, and expected vs actual.
  In Postman, the request's **Code** button (`</>`) gives a ready-to-paste curl command.

---

# Reference

## Folders

| Folder | Tool | What it covers |
|---|---|---|
| `postman/` | Postman / newman | API 1 collection (27 requests, 115 assertions); also shared with the client |
| `swagger/` | Swagger Editor / Swagger UI | OpenAPI 3.1 spec for API 1, with request examples (valid and `invalid_*`), example responses and every error status; also shared with the client |
| `api-tests/` | Playwright (API only) | 69 test cases: `api1_current_month.spec.js` (65), `common.spec.js` (4) |
| `k6/` | k6 | Smoke, load, stress, spike and fixed-rate (`rate.js`, 1 lakh/min) performance tests of API 1 |
| `security/` | bandit, pip-audit, OWASP ZAP | Static analysis, dependency CVEs, active API scan |
| `../tests/` | pytest | 123 unit tests (plus 13 database tests, run with `TEST_DATABASE_URL`) (no server needed): parsing rules, response shape, middleware, Postman and Swagger coverage, Redis cache, loader (order, limits, reconnect) |

## Running the suites

### Unit tests

```bash
uv run pytest
# Also the database tests (bulk upsert = row-by-row upsert, loader lock), in throwaway schemas:
TEST_DATABASE_URL=postgresql://acme:acme@localhost:5433/acme uv run pytest tests/test_load_db.py
```

### Postman

Headless (same checks as the app's collection runner):

```bash
cd qa/postman
npx newman run Scheme_API1_Current_Month.postman_collection.json -e Local.postman_environment.json
```

In the Postman app: import the collection and an environment, then **Run collection**.

The collection and environments are generated by `postman/build_collection.py`: edit requests and
tests there, run `python3 qa/postman/build_collection.py`, and commit the regenerated JSON.
`tests/test_postman_collection.py` fails if an API endpoint has no request, if a request has no
tests, or if the collection calls another endpoint. Set `authEnabled=false` in the environment when the server has no API key,
and `sampleData=false` when it doesn't hold the sample data.

### Swagger

1. Open https://editor.swagger.io → **File → Import file** → pick the spec from `qa/swagger/`.
   Any OpenAPI tool works too: Postman (**Import**), Insomnia, or a code generator.
2. Choose a server (Local `http://localhost:8000`, or Dev: edit the `host` variable), click
   **Authorize** and enter the API key (`local-test-key` locally; skip when the server has no key).
3. Open the request → **Try it out** → pick an example from the dropdown → **Execute**. Examples
   named `invalid_…` must return 422; the others return 200.

**Execute** from editor.swagger.io is a browser call to another site, which the API refuses (it
sends no CORS headers, on purpose). Reading the spec there always works; to send requests, use
`/docs` on the server (same requests and examples) or import the spec into Postman.

The spec is cut from the app's own OpenAPI by `swagger/build_specs.py`: run
`uv run python qa/swagger/build_specs.py` after changing the endpoint or its examples
(`app/openapi_examples.py`), and commit the JSON. `tests/test_swagger_specs.py` fails if the spec is
stale, holds another endpoint, or has an example that the API would treat differently from its name.

### Playwright API tests

```bash
cd qa/api-tests
npm install
API_KEY=local-test-key AUTH_ENABLED=true npx playwright test     # all
npx playwright test tests/api1_current_month.spec.js              # one file (or npm run test:api1)
npm run test:security                                             # one category
npx playwright test -g "API1_TC_15"                               # one case
npm run report                                                    # HTML report
```

| Variable | Default | Meaning |
|---|---|---|
| `API_BASE_URL` | `http://localhost:8000` | Server under test |
| `API_KEY` | — | Sent as `X-API-Key` |
| `AUTH_ENABLED` | `false` | `true` runs the authentication cases (API1_TC_43–47_SEC) |
| `DEALER_CODE` | `6100000001` | A dealer with data in `MONTH`/`YEAR` |
| `MONTH`, `YEAR` | current month (India time) | Month sent to API 1 |
| `SAMPLE_DATA` | `true` | `false` on real environments: skips cases that assert sample values (API1_TC_16–19) |

### k6

```bash
cd qa/k6
API_KEY=local-test-key k6 run smoke.js     # always first
API_KEY=local-test-key k6 run load.js      # 150 VUs, 3 min hold
API_KEY=local-test-key k6 run stress.js    # steps to 600 VUs
API_KEY=local-test-key k6 run spike.js     # 20 -> 500 VUs burst
API_KEY=local-test-key k6 run rate.js      # 1,700 requests/s for 2 min (1 lakh a minute)
```

| Script | Profile | Overrides |
|---|---|---|
| `smoke.js` | 1 VU, 30 s | — |
| `load.js` | ramp 30 s → 150 VUs for 3 min → ramp down | `TARGET_VUS`, `HOLD` |
| `stress.js` | 25% → 50% → 100% of 600 VUs, 1 min each | `MAX_VUS` |
| `spike.js` | 20 VUs → 500 VUs in 10 s, hold 1 min, recover | `BASE_VUS`, `SPIKE_VUS` |
| `rate.js` | fixed 1,700 requests/s for 2 min, no think time (also fails if > 1% of requests can't be sent) | `RATE`, `DURATION`, `MAX_VUS` |

All scripts send `POST /v1/schemes/current-month` only. Settings: `BASE_URL`, `API_KEY`,
`DEALER_PREFIX` (default `62`), `DEALER_COUNT` (default 20000), `UNKNOWN_RATE` (default 0.05),
`MONTH`/`YEAR` (month sent to API 1; default the current month in India time). 0.25–0.75 s
think time (none in `rate.js`). Thresholds: error rate < 1%,
checks > 99%, p95 < 500 ms and p99 < 1 s. JSON summaries go to `k6/reports/<script>-summary.json`.
For an HTML dashboard add `K6_WEB_DASHBOARD=true K6_WEB_DASHBOARD_EXPORT=reports/load.html`.

### Security scans

```bash
API_KEY=local-test-key qa/security/run_scans.sh          # all (ZAP needs the API running + Docker)
qa/security/run_scans.sh sast deps                        # no server needed
```

Reports go to `security/reports/` (bandit.txt, pip-audit.txt, zap-report.html/.md/.json).

## Test case index (Playwright)

### API 1 — `api1_current_month.spec.js`

Every request sends `month` and `year` (default: the current month in India time, where the sample
data is; override with `MONTH` / `YEAR`).

| TC | Category | Description | Expected |
|---|---|---|---|
| API1_TC_1 | Functional | Dealer with data in the month | 200, non-empty array |
| API1_TC_2 | Functional | Row fields | Exactly the 10 template fields |
| API1_TC_3 | Functional | Components | Only the 8 template components |
| API1_TC_4 | Functional | Ordering | By ToDate, then template order |
| API1_TC_5 | Functional | Customer value | Equals the requested dealer |
| API1_TC_6 | Functional | Dealer code as JSON number | Same result as string |
| API1_TC_7 | Functional | Leading zeros | Same result as without |
| API1_TC_8 | Functional | Surrounding spaces | Trimmed, same result |
| API1_TC_9 | Functional | Unknown dealer | 200 `[]` |
| API1_TC_10 | Functional | Month and year as numeric strings (`"10"`, `"2026"`) | Same result as numbers |
| API1_TC_11 | Functional | Month with no data (January 2000) | 200 `[]` |
| API1_TC_12 | Validation | Date format and range | `YYYY-MM-DD`, FromDate ≤ ToDate |
| API1_TC_13 | Validation | Amount types | Numbers or null, never strings |
| API1_TC_14 | Validation | Component uniqueness | Each component once per dealer |
| API1_TC_15 | Validation | Month | Rows belong to the requested month |
| API1_TC_16 | Validation | [sample] All 8 components | Present for 6100000001 |
| API1_TC_17 | Validation | [sample] Latest update wins | Day-2 LUXURY values replace day 1 (not added) |
| API1_TC_18 | Validation | [sample] SAP trailing-minus numbers | `-882` |
| API1_TC_19 | Validation | [sample] Qualification basis | GSR for EB slabs and Value; EB FULL MONTH = its GSR (GsrB); invoiced qty for Luxury/Premium |
| API1_TC_20 | Negative | Missing `month` | 422 |
| API1_TC_21 | Negative | Missing `year` | 422 |
| API1_TC_22 | Negative | Month 0, 13, -1 | 422 |
| API1_TC_23 | Negative | Year 1999, 2101, 26 | 422 |
| API1_TC_24 | Negative | Boolean month / year (`true` is not 1) | 422 |
| API1_TC_25 | Negative | Month 9.5, `"Sept"`, `""`, `[9]`, `null` | 422 |
| API1_TC_26–65 | Edge, negative, security | Request contract (with month and year in every body) | See the table below |

### Request contract and security — API1_TC_26–65 (`helpers.js`)

These cases check the `dealer_code` field and the security rules; every body also carries
`month` and `year`.

| TC | Category | Description | Expected |
|---|---|---|---|
| API1_TC_26 | Edge | 20-character dealer code (max) | 200 |
| API1_TC_27 | Edge | Alphanumeric dealer code | 200 |
| API1_TC_28 | Edge | Extra unknown body fields | Ignored, same result |
| API1_TC_29 | Edge | All-zero dealer code | 200 `[]` |
| API1_TC_30 | Edge | Repeated requests | Identical results |
| API1_TC_31–34 | Negative | Missing / empty / whitespace / null `dealer_code` | 422 |
| API1_TC_35–36 | Negative | Boolean, array, object `dealer_code` | 422 |
| API1_TC_37 | Negative | 21 characters | 422 |
| API1_TC_38 | Negative | Hyphen or space inside | 422 |
| API1_TC_39–41 | Negative | Invalid JSON, empty body, form-encoded body | 422 |
| API1_TC_42 | Negative | `GET` on the POST endpoint | 405 |
| API1_TC_43_SEC | Authentication | Missing `X-API-Key` | 401 |
| API1_TC_44_SEC | Authentication | Wrong key | 401 |
| API1_TC_45_SEC | Authentication | Empty key | 401 |
| API1_TC_46_SEC | Authentication | Key in query string | 401 (not accepted) |
| API1_TC_47_SEC | Authentication | Altered key (case/padding) | 401 |
| API1_TC_48_SEC | SQL Injection | `' OR '1'='1`, `DROP TABLE`, `UNION SELECT` | 422, not reflected |
| API1_TC_49_SEC | XSS | `<script>`, `onerror=` payloads | 422, not reflected |
| API1_TC_50_SEC | Path Traversal | `../../etc/passwd`, URL-encoded | 422 |
| API1_TC_51_SEC | Null Byte | `\x00` in dealer code | 422 |
| API1_TC_52_SEC | CRLF Injection | `\r\n` header injection | 422, no injected header |
| API1_TC_53_SEC | Unicode | Full-width digits, zero-width space | 422 |
| API1_TC_54_SEC | Oversized Input | 5000-character dealer code | 413/422, never 500 |
| API1_TC_55_SEC | Resource Consumption | 1 MB body | 413 |
| API1_TC_56_SEC | Resource Consumption | Deeply nested JSON | < 500 |
| API1_TC_57_SEC | Method Abuse | PUT / DELETE / PATCH | 405 |
| API1_TC_58_SEC | Method Abuse | TRACE / OPTIONS | 405 |
| API1_TC_59_SEC | Headers (CWE-693) | `X-Content-Type-Options: nosniff` | present |
| API1_TC_60_SEC | Headers (CWE-693) | HSTS `max-age` ≥ 31536000 | present |
| API1_TC_61_SEC | Headers (CWE-693) | `Cache-Control: no-store` (also on errors) | present |
| API1_TC_62_SEC | Headers (CWE-693) | CSP `default-src 'none'`, `X-Frame-Options: DENY` | present |
| API1_TC_63_SEC | Info Disclosure (CWE-200) | No server version, no `X-Powered-By` | absent |
| API1_TC_64_SEC | CORS | Foreign Origin | no `Access-Control-Allow-Origin` |
| API1_TC_65_SEC | Rate Limiting | 50 parallel requests | no 500s; records whether 429 is enforced |

All 422 responses are also checked for leaks: no traceback, file paths, SQL/DB errors or echoed input.

### Common — `common.spec.js`

| TC | Category | Description | Expected |
|---|---|---|---|
| COMMON_TC_1 | Functional | `GET /health` | 200 `{ok: true}` |
| COMMON_TC_2 | Negative | Unknown route | 404 |
| COMMON_TC_3_SEC | Authentication | `/health` without key | 200 (liveness probe stays open) |
| COMMON_TC_4_SEC | Transport | Plain HTTP on deployed URL | redirect or refused (skipped on local http) |

## OWASP API Security Top 10 (2023) coverage

| Risk | Status | How |
|---|---|---|
| API1 Broken Object Level Authorization | By design | One key gives access to every dealer (the client needs all dealers). If per-dealer access is ever needed, keys must be scoped to dealers. |
| API2 Broken Authentication | Covered | `X-API-Key` with constant-time comparison; API1_TC_43–47_SEC, COMMON_TC_3_SEC. Requires `API_KEY` to be set in each environment. |
| API3 Broken Object Property Level Authorization | Covered | Fixed response model; raw SAP columns are never returned (API1_TC_2). |
| API4 Unrestricted Resource Consumption | Partly | 4 KB body limit (API1_TC_54–56_SEC), bounded responses, DB pool limits with 503. **No rate limiting in the app** — set it at the gateway/load balancer. |
| API5 Broken Function Level Authorization | N/A | No admin or write endpoints. |
| API6 Unrestricted Access to Sensitive Business Flows | N/A | Read-only lookups. |
| API7 Server-Side Request Forgery | N/A | No URL inputs. |
| API8 Security Misconfiguration | Covered | Security headers, no stack traces, no server version (API1_TC_59–63_SEC, ZAP). TLS redirect to verify on deployment (COMMON_TC_4_SEC). |
| API9 Improper Inventory Management | Covered | Versioned `/v1`, OpenAPI spec in `docs/openapi.json`. Decide whether `/docs` is public in production (`DOCS_ENABLED`). |
| API10 Unsafe Consumption of APIs | Covered | SAP files are validated: required columns checked, exact component matching, bad rows logged and skipped. |
