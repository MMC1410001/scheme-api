# Scheme API — QA Suite

Functional, security and performance testing for the Scheme API.
Latest results: [TEST_RESULTS.md](TEST_RESULTS.md). API guide: [../docs/API_GUIDE.md](../docs/API_GUIDE.md).

The API has one endpoint, API 1 — `POST /v1/schemes/current-month` (body `{dealer_code, month, year}`).
Callers authenticate with OAuth2 client credentials: `POST /v1/auth/token` with a `client_id` and
`client_secret` returns a Bearer token (1 hour), sent as `Authorization: Bearer <token>` on API 1.
Locally the client is `local-client` / `local-test-secret` (from `.env.example`). Every suite fetches
**one** token and reuses it: the token endpoint allows only `TOKEN_RATE_LIMIT_PER_MINUTE` (default 30)
requests per IP per minute.

| | API 1 — `POST /v1/schemes/current-month` |
|---|---|
| Postman | `postman/Scheme_API1_Current_Month.postman_collection.json` |
| Swagger | `swagger/Scheme_API1_Current_Month.openapi.json` |
| Playwright | `api-tests/tests/api1_current_month.spec.js` (`API1_TC_n`) |
| k6 | `k6 run <script>.js` |

Also in the suite: the Postman environments (`Local`, `Dev`), `api-tests/tests/common.spec.js` (health,
unknown route, HTTPS, the token endpoint; `COMMON_TC_n`), `api-tests/tests/helpers.js`, and the security scans.

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
   3. Negative cases, 4. Security, 5. Auth (the token endpoint).
2. In the environment dropdown (top right) choose **Scheme API - Local**. It already has
   `baseUrl = http://localhost:8000`, `clientId = local-client`, `clientSecret = local-test-secret`,
   `authEnabled = true`, `dealerCode = 6100000001`, `sampleData = true`.
3. Open the collection → **2. …** → a request → **Send**. The token is handled for you: the
   collection's pre-request script gets one from `/v1/auth/token` with `clientId`/`clientSecret`,
   stores it in the collection variable `accessToken` (and `accessTokenExpiresAt`), reuses it until a
   minute before it expires, and the collection's **Authorization** tab sends `Bearer {{accessToken}}`.
   To force a new token, clear `accessToken` in the collection's **Variables** tab.
4. Each request's checks are under **Scripts → Post-response**; saved example responses are under
   **Examples** in the sidebar (click the arrow next to the request).
5. To run everything: click the collection → **Run**. Expect 65 requests, 309 checks passed (Local,
   auth on). The run makes 13 token requests; wait a minute between full runs (token rate limit).

API 1 needs a month: its requests send `month` and `year`, which default to the current month (India
time), where the sample data is. To test another month, add `month` and `year` variables to the
environment (e.g. `9` and `2026`).

Test data to use:

| Dealer | API 1 shows |
|---|---|
| `6100000001` | All 10 components in the current month; LUXURY was updated on day 2, so the newer values must appear (Qualification 315.1, GsrB 159292.8) |
| `6100000002` | Negative values (SAP `882.00-` must come back as `-882`) |
| `6200000001`–`6200020000` | Load-test dealers (only if you created them in Step 1) |
| `6199999999` | No data: `200` with `[]` |

### Step 3 — Exploratory checks worth doing by hand

- Dealer code with leading zeros (`0006100000001`) returns the same result as without.
- Dealer code as a number (`6100000001` without quotes) works.
- Invalid codes (`6100-0001`, empty, 21+ characters) return `422` and never echo your input.
- No token, a made-up token, or a token with one character changed returns `401` with a
  `WWW-Authenticate: Bearer …` header; the old `X-API-Key` header alone returns `401`.
- A token request with a wrong secret returns `401 {"error": "invalid_client", …}`; the body never
  contains the secret. After ~30 token requests in a minute you get `429` with `Retry-After`.

- Rows are ordered by `ToDate`: EB SLAB 1, 2, 3 first, EB FULL MONTH last.
- A month returns the rows valid in that month: late SAP updates of last month's schemes (found in
  this month's folder) show under last month, never twice under this month.
- `month` and `year` are required: leave one out, or send 13, 1999, `true` or `"Oct"` → `422`.
- A month without data (e.g. January 2000) returns `200 []`; `"10"` works the same as `10`.
- Month is 1–12 as one or two digits: `9`, `09`, `"9"` and `"09"` all work; `-9`, `9.0`, `0`, `009` → `422`.
  Year is four digits, 2000–2100: `2026` or `"2026"`; `02026`, `"+2026"`, `2026.0` → `422`.
- EB amounts are running totals (SLAB 2 includes SLAB 1); the API never adds rows together.

- Start-up safety (stop your running API first; run from the repo folder with a `.env.local`
  that has no `AUTH_CLIENTS`/`JWT_SECRET`, or move it aside): `uv run python -m app` must
  refuse to start with `APP_ENV is not set and there are no auth settings`;
  `APP_ENV=local uv run python -m app` starts and logs `Auth is OFF`;
  `APP_ENV=prod AUTH_CLIENTS='{"x":"0b1f90b824765bd860b58a3ebc0396a4f6f4c4ad8f1eed10135c0cb6cf3bd171"}' JWT_SECRET=local-only-jwt-signing-key-not-for-servers-0001 uv run python -m app`
  must refuse the example values, and so must the same command without `APP_ENV=prod` (APP_ENV unset).

- With the cache on (`CACHE_ENABLED=true`, Redis running): the first request for a dealer and month
  has `X-Cache: MISS`, a repeat has `X-Cache: HIT` with the same body; stop Redis and requests
  still work (`X-Cache: BYPASS`).

### Step 4 — Run the automated API tests (Playwright)

```bash
cd qa/api-tests
npm install                                                   # first time only
AUTH_ENABLED=true npx playwright test                         # 100 cases (API 1 + common)
npm run report                                                # opens the HTML report
```

On localhost the suite uses `local-client` / `local-test-secret` unless `CLIENT_ID` /
`CLIENT_SECRET` are set; each worker gets one token. Expected: 97 passed, 3 skipped
(COMMON_TC_4_SEC needs an HTTPS URL; API1_TC_69_SEC and COMMON_TC_19_SEC need `RATE_LIMIT` /
`TOKEN_RATE_LIMIT`). A run makes about 25 token requests (one per worker plus the token-endpoint cases), so wait a minute before running it (or
newman) again, or the token requests get `429`. Run one file with
`npm run test:api1` (or `test:common`); one category with
`npm run test:security` (or `:functional`, `:validation`, `:edge`, `:negative`, `:auth`); one case with
`npx playwright test -g "API1_TC_12"`. Put `AUTH_ENABLED=true` in front of `npm run`
too. The full case list is in the index at the end of this file.

### Step 5 — Run the performance tests (k6)

Always run the smoke test first; only continue if it passes.

```bash
cd qa/k6
k6 run smoke.js              # 30 s, 1 user
k6 run load.js               # ~4 min, 150 users
k6 run rate.js               # 2 min at 1,700 requests/s (1 lakh a minute)
```

Each script's `setup()` gets one token (on localhost with `local-client` / `local-test-secret`;
elsewhere pass `CLIENT_ID` and `CLIENT_SECRET`) and every virtual user sends it as
`Authorization: Bearer`. Keep runs under an hour (the token's lifetime).

`rate.js` sends a fixed number of requests per second, like the dealer app does when
it loads: up to 1 lakh a minute. Run it with the cache off and on (see "Response cache" in the
main README) to compare; set `RATE` and `DURATION` to change it.

The last lines of each run say `all thresholds passed` or list what failed. Pass criteria: errors
< 1%, checks > 99%, p95 < 500 ms and p99 < 1 s. `stress.js` and `spike.js` find the limits; run them
when asked. Results are saved to `qa/k6/reports/`.

### Step 6 — Run the security scans

```bash
qa/security/run_scans.sh sast deps                   # code + dependency checks, no server needed
CLIENT_ID=local-client CLIENT_SECRET=local-test-secret qa/security/run_scans.sh zap  # OWASP ZAP (Docker)
```

The script gets one token with curl and ZAP's replacer adds `Authorization: Bearer <token>` to every
request. Without `CLIENT_ID`/`CLIENT_SECRET` ZAP scans unauthenticated (only useful when the server has
auth off).

Pass = no bandit issues, no known vulnerabilities, and ZAP `FAIL-NEW: 0`. Reports go to
`qa/security/reports/`.

### Step 7 — Test on Dev or Production

**Test dealers.** Real environments don't have the sample dealers. Real dealer codes for each month
live in `qa/test-data/dealers.json` (client data: gitignored, share it only through the team's
secret store). Build it once from a database that has the months loaded (read-only):

```bash
APP_ENV=dev uv run python qa/test-data/pick_dealers.py --months 2026-09 2026-10 --count 5
```

Per month, the first code is the main test dealer (most components, one agreement each); the others
add a dealer with negative amounts, one with only Luxury/Premium, and a spread of the rest. Dev, UAT
and local all load the same SAP files from GCS, so one file works everywhere. Playwright (with
`SAMPLE_DATA=false`) and k6 (`DEALERS_FILE=true`) read it; `DEALER_CODE` / `DEALER_CODES` override it.

Dev is at `https://dev.example.com` (Swagger UI at `/docs`; the API is at the root, e.g.
`/v1/schemes/current-month`). It requires a Bearer token: ask the developer for a QA client's
`client_id` and `client_secret` (one per environment; never commit them or paste them in tickets).

- **Postman:** choose **Scheme API - Dev** (`baseUrl` and `authEnabled = true` are already set),
  then set `clientId` and `clientSecret` and a real `dealerCode` that has data this month. Keep `sampleData = false` (skips sample-value checks).
- **Postman / newman:** take `dealerCode` from the file, e.g.
  `npx newman run <collection> -e Dev.postman_environment.json --env-var clientId=<id> --env-var clientSecret=<secret> --env-var month=10 --env-var year=2026 --env-var dealerCode=$(node -p "require('../test-data/dealers.json').months['2026-10'][0]")`.
- **Playwright (Dev):** `CLIENT_ID=<id> CLIENT_SECRET=<secret> MONTH=<m> YEAR=<yyyy> npm run test:dev` (dealer from `dealers.json`).
  Other servers: `API_BASE_URL=https://<host> CLIENT_ID=<id> CLIENT_SECRET=<secret> AUTH_ENABLED=true SAMPLE_DATA=false DEALER_CODE=<real dealer> MONTH=<m> YEAR=<yyyy> npx playwright test`.
  API1_TC_74–76_SEC forge tokens with the server's signing key and are skipped there unless
  `JWT_SECRET` is set; don't set it with a real environment's key outside a secure runner.
  `SAMPLE_DATA=false` skips the checks that only make sense for the local sample data, and then
  `DEALER_CODE` is required (the run stops with a message without it); `MONTH` and `YEAR` pick a
  month in which that dealer has data.
- **k6:** `BASE_URL=https://<host> CLIENT_ID=<id> CLIENT_SECRET=<secret> DEALERS_FILE=true k6 run -e MONTH=<m> -e YEAR=<yyyy> smoke.js`
  (or `DEALER_CODES=<code1>,<code2>,…` instead of the file). Use real dealers on that server (the generated `62…` perf dealers exist
  only locally). Agree the load level with the team before running load/stress tests on a shared server.

### Step 8 — Report results and bugs

- Record each run in [TEST_RESULTS.md](TEST_RESULTS.md): date, environment, counts per suite,
  k6 numbers, scan results.
- For a bug, include: environment and base URL, the request (method, URL, body, headers without
  the `Authorization` token and client secret), the response (status, body), the time, the dealer code, and expected vs actual.
  In Postman, the request's **Code** button (`</>`) gives a ready-to-paste curl command.

---

# Reference

## Folders

| Folder | Tool | What it covers |
|---|---|---|
| `postman/` | Postman / newman | API 1 collection (58 requests, 278 assertions, including the token endpoint); also shared with the client |
| `swagger/` | Swagger Editor / Swagger UI | OpenAPI 3.1 spec for API 1, with request examples (valid and `invalid_*`), example responses and every error status; also shared with the client |
| `api-tests/` | Playwright (API only) | 96 test cases: `api1_current_month.spec.js` (77), `common.spec.js` (19) |
| `k6/` | k6 | Smoke, load, stress, spike and fixed-rate (`rate.js`, 1 lakh/min) performance tests of API 1 |
| `security/` | bandit, pip-audit, OWASP ZAP | Static analysis, dependency CVEs, active API scan |
| `../tests/` | pytest | 180 unit tests (plus 21 database tests, run with `TEST_DATABASE_URL`: bulk upsert, loader lock, API 1 month rule) (no server needed): parsing rules, response shape, middleware, Postman and Swagger coverage, Redis cache, loader (order, limits, reconnect) |

## Running the suites

### Unit tests

```bash
uv run pytest
# Also the database tests (bulk upsert = row-by-row upsert, loader lock), in throwaway schemas:
TEST_DATABASE_URL=postgresql://acme:acme@localhost:5433/acme uv run pytest tests/test_load_db.py tests/test_month_query_db.py
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
tests, or if the collection calls another endpoint. Set `authEnabled=false` in the environment when the server has auth off
(no token is fetched and the auth cases report SKIPPED),
and `sampleData=false` when it doesn't hold the sample data.

### Swagger

1. Open https://editor.swagger.io → **File → Import file** → pick the spec from `qa/swagger/`.
   Any OpenAPI tool works too: Postman (**Import**), Insomnia, or a code generator.
2. Choose a server (Local `http://localhost:8000`, or Dev: edit the `host` variable), click
   **Authorize**, enter `client_id` and `client_secret` (`local-client` / `local-test-secret` locally)
   and click **Authorize**: Swagger gets the token from `/v1/auth/token` itself (skip when the server has auth off).
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
AUTH_ENABLED=true npx playwright test                             # all (local client by default)
npx playwright test tests/api1_current_month.spec.js              # one file (or npm run test:api1)
npm run test:security                                             # one category
npm run test:auth                                                 # only the token and Bearer cases
npx playwright test -g "API1_TC_15"                               # one case
CLIENT_ID=<id> CLIENT_SECRET=<secret> DEALER_CODE=<dealer> npm run test:dev   # Dev server (https://dev.example.com)
npm run report                                                    # HTML report
```

| Variable | Default | Meaning |
|---|---|---|
| `API_BASE_URL` | `http://localhost:8000` | Server under test |
| `CLIENT_ID`, `CLIENT_SECRET` | `local-client` / `local-test-secret` on localhost, else empty | Exchanged once per worker at `/v1/auth/token`; the token is sent as `Authorization: Bearer` (refetched when < 60 s are left). `CLIENT_ID=` sends no token |
| `AUTH_ENABLED` | `false` | `true` when the server requires a token: runs the auth cases (API1_TC_43–47_SEC, API1_TC_70–77, COMMON_TC_5–20) and makes a failed token request an error |
| `JWT_SECRET` | the `.env.example` key on localhost, else empty | The server's signing key, to forge expired / wrong-scope / wrong-claim tokens (API1_TC_74–76_SEC; skipped without it) |
| `JWT_TTL_SECONDS` | `3600` | The server's token lifetime, checked as `expires_in` |
| `TOKEN_RATE_LIMIT` | — | The server's `TOKEN_RATE_LIMIT_PER_MINUTE`; set it to run COMMON_TC_19_SEC, on its own (`-g COMMON_TC_19`): it uses up the token limit for a minute |
| `DEALER_CODE` | `6100000001`; with `SAMPLE_DATA=false` the month's first code in `qa/test-data/dealers.json` | A dealer with data in `MONTH`/`YEAR` |
| `MONTH`, `YEAR` | current month (India time) | Month sent to API 1 |
| `RATE_LIMIT` | — | The server's `RATE_LIMIT_PER_SECOND`; set it (with a low server value) to run API1_TC_69_SEC |
| `SAMPLE_DATA` | `true` | `false` on real environments: skips cases that assert sample values (API1_TC_16–19) |

### k6

```bash
cd qa/k6
k6 run smoke.js     # always first
k6 run load.js      # 150 VUs, 3 min hold
k6 run stress.js    # steps to 600 VUs
k6 run spike.js     # 20 -> 500 VUs burst
k6 run rate.js      # 1,700 requests/s for 2 min (1 lakh a minute)
```

| Script | Profile | Overrides |
|---|---|---|
| `smoke.js` | 1 VU, 30 s | — |
| `load.js` | ramp 30 s → 150 VUs for 3 min → ramp down | `TARGET_VUS`, `HOLD` |
| `stress.js` | 25% → 50% → 100% of 600 VUs, 1 min each | `MAX_VUS` |
| `spike.js` | 20 VUs → 500 VUs in 10 s, hold 1 min, recover | `BASE_VUS`, `SPIKE_VUS` |
| `rate.js` | fixed 1,700 requests/s for 2 min, no think time (also fails if > 1% of requests can't be sent) | `RATE`, `DURATION`, `MAX_VUS` |

All scripts send `POST /v1/schemes/current-month` only, with one Bearer token fetched in `setup()`
(`authSetup()` in `config.js`). Settings: `BASE_URL`, `CLIENT_ID` and `CLIENT_SECRET` (default
`local-client` / `local-test-secret` on localhost only; `AUTH_ENABLED=true` makes a refused token
request stop the run instead of continuing without a token),
`DEALER_PREFIX` (default `62`), `DEALER_COUNT` (default 20000), `UNKNOWN_RATE` (default 0.05),
`DEALER_CODES` (comma-separated real dealers; replaces the generated codes, for Dev/UAT),
`DEALERS_FILE=true` (take the month's dealers from `qa/test-data/dealers.json`),
`MONTH`/`YEAR` (month sent to API 1; default the current month in India time). 0.25–0.75 s
think time (none in `rate.js`). Thresholds: error rate < 1%,
checks > 99%, p95 < 500 ms and p99 < 1 s. JSON summaries go to `k6/reports/<script>-summary.json`.
For an HTML dashboard add `K6_WEB_DASHBOARD=true K6_WEB_DASHBOARD_EXPORT=reports/load.html`.

### Security scans

```bash
CLIENT_ID=local-client CLIENT_SECRET=local-test-secret qa/security/run_scans.sh   # all (ZAP needs the API running + Docker)
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
| API1_TC_2 | Functional | Row fields | Rows of the 10 components: exactly the 11 fields (the 10 template fields + `NextSlabRate`); other components add `QualificationBasis` and `InvoicedQty` |
| API1_TC_3 | Functional | Components | Components outside the 10 carry `QualificationBasis` and `InvoicedQty` (number or null): `GSR` for ROLLER, SANDPAPER, MASKING TAPE; `INVOICE_QTY` for the others, with `Qualification` = `InvoicedQty` (rule of 2026-10-22) |
| API1_TC_4 | Functional | Ordering | By `ClearCut`, highest first (empty last); equal `ClearCut`: by ToDate, then template order, then other components by name (changed 2026-10-22) |
| API1_TC_5 | Functional | Customer value | Equals the requested dealer |
| API1_TC_6 | Functional | Dealer code as JSON number | Same result as string |
| API1_TC_7 | Functional | Leading zeros | Same result as without |
| API1_TC_8 | Functional | Surrounding spaces | Trimmed, same result |
| API1_TC_9 | Functional | Unknown dealer | 200 `[]` |
| API1_TC_10 | Functional | Month and year as numeric strings (`"10"`, `"2026"`) | Same result as numbers |
| API1_TC_11 | Functional | Month with no data (January 2000) | 200 `[]` |
| API1_TC_12 | Validation | Date format and range | `YYYY-MM-DD`, FromDate ≤ ToDate |
| API1_TC_13 | Validation | Amount types | Numbers or null, never strings |
| API1_TC_14 | Validation | Component uniqueness | Each component once per start date (newest SAP update only; no old end date or old agreement) |
| API1_TC_15 | Validation | Month | Every row's validity period overlaps the requested month |
| API1_TC_16 | Validation | [sample] All 10 components | Present for 6100000001 |
| API1_TC_80 | Validation | [sample] Other components | `ALWD EB2` for 6100000001: `Qualification` = invoiced quantity 12.5, `QualificationBasis` `INVOICE_QTY`, `InvoicedQty` 12.5; `ROLLER`: `Qualification` = GSR (Q) 11350, `QualificationBasis` `GSR`, `InvoicedQty` 50; `LUXURY` has neither field |
| API1_TC_81 | Validation | [sample] ClearCut order | 6100000001: ROLLER (2951), LUXURY (1592.93), then the 0 rows in template order, ALWD EB2 last |
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
| API1_TC_66 | Functional | Month as one or two digits: `9`, `09` (unquoted), `"9"`, `"09"` | Same result as `9` |
| API1_TC_67 | Negative | Month `-9`, `-09`, `"+9"`, `9.0`, `"9.0"`, `09.5`, `1e1`, `0`, `00`, `009`, `"009"`, `"010"`, `" 9 "`, `010`, `0010`, `"0010"`, `0012`, `"012"`, `13` | 422 |
| API1_TC_68 | Negative | Year `02026`, `"02026"`, `26`, `"26"`, `-2026`, `"+2026"`, `2026.0`, `"2026.0"`, `2.026e3`, `" 2026 "` | 422 |
| API1_TC_78 | Validation | Every month 1–12 as `1`–`9`, `01`–`09`, `10`, `11`, `12`, numbers or strings (QA 2026-10-16) | 200, an array |
| API1_TC_79 | Edge | `"month": 09` with `Content-Type: Application/JSON` or `APPLICATION/JSON; charset=utf-8` (QA 2026-10-16 #8) | 200, same rows as `application/json` |
| API1_TC_69_SEC | Rate Limiting | 3 × the per-IP limit at once (runs when `RATE_LIMIT` is set, against a server with a low `RATE_LIMIT_PER_SECOND`, e.g. 20) | Some 429 with `Retry-After: 1`, security headers, no 5xx; 200 again a second later |
| API1_TC_70_SEC | Authentication | Non-ASCII Bearer token (`kéy`, a real token + `é`, `ÿÿÿ…`) | 401 `{"detail": "Invalid token"}` with security headers, never 500 |
| API1_TC_71_SEC | Authentication | Other schemes: `Basic <client_id:secret>`, `Token <jwt>`, `JWT <jwt>`, a bare token | 401 `Not authenticated` |
| API1_TC_72_SEC | Authentication | Unsigned token (`alg` `none` / `None` / `NONE`) | 401 `Invalid token` |
| API1_TC_73_SEC | Authentication | Token signed with another HS256 key, an empty key, or RS256 | 401 `Invalid token` |
| API1_TC_74_SEC | Authentication | Expired token (needs `JWT_SECRET`) | 401 `Token expired`, `WWW-Authenticate: Bearer … error="invalid_token"` |
| API1_TC_75_SEC | Authorization | Token without the `schemes:read` scope (empty, other, missing; needs `JWT_SECRET`) | 403 `Insufficient scope`, `error="insufficient_scope"` |
| API1_TC_76_SEC | Authentication | Signed token with a wrong claim: unknown (revoked) client, audience, issuer, `nbf` in the future, no `exp` / `jti` / `sub` (needs `JWT_SECRET`) | 401 `Invalid token` |
| API1_TC_77 | Authentication | Valid token with `Bearer` / `bearer` / `BEARER`, reused | 200 |

Every 401 is also checked for `WWW-Authenticate: Bearer realm="scheme-api"` (with
`error="invalid_token"` for a bad token), `nosniff` and `Cache-Control: no-store`. The auth cases run
only with `AUTH_ENABLED=true`.

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
| API1_TC_43_SEC | Authentication | No `Authorization`, empty, or `Bearer` with no token | 401 `Not authenticated` + `WWW-Authenticate: Bearer realm=…` |
| API1_TC_44_SEC | Authentication | Garbage Bearer token (`not-a-jwt`, `a.b.c`, empty signature, 2,000 characters) | 401 `Invalid token`, `error="invalid_token"` |
| API1_TC_45_SEC | Authentication | Real token with a changed signature, a changed payload (`sub`), or no signature | 401 `Invalid token` |
| API1_TC_46_SEC | Authentication | Token in the query string (`?access_token=`) only | 401 (header only) |
| API1_TC_47_SEC | Authentication | `X-API-Key` alone (the old scheme) | 401 `Not authenticated` |
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
| API1_TC_60_SEC | Headers (CWE-693) | HSTS `max-age` ≥ 31536000, sent exactly once | present, one header |
| API1_TC_61_SEC | Headers (CWE-693) | `Cache-Control: no-store` (also on errors) | present |
| API1_TC_62_SEC | Headers (CWE-693) | CSP `default-src 'none'`, `X-Frame-Options: DENY` | present |
| API1_TC_63_SEC | Info Disclosure (CWE-200) | No server version, no `X-Powered-By` | absent |
| API1_TC_64_SEC | CORS | Foreign Origin | no `Access-Control-Allow-Origin` |
| API1_TC_65_SEC | Rate Limiting | 50 parallel requests | no 500s, no 429 (far under the per-IP limit) |

All 422 responses are also checked for leaks: no traceback, file paths, SQL/DB errors or echoed input.

### Common — `common.spec.js`

| TC | Category | Description | Expected |
|---|---|---|---|
| COMMON_TC_1 | Functional | `GET /health` | 200 `{ok: true}` |
| COMMON_TC_2 | Negative | Unknown route | 404 |
| COMMON_TC_3_SEC | Authentication | `/health` without a token | 200 (liveness probe stays open) |
| COMMON_TC_4_SEC | Transport | Plain HTTP on deployed URL | redirect or refused (skipped on local http) |
| COMMON_TC_21_SEC | Supply Chain | `/docs` (skipped when docs are off): Swagger UI script and stylesheet from one exact `swagger-ui-dist` version with `integrity` + `crossorigin`; CSP allows only that release; the hashes match the files on the CDN (QA 2026-10-16 #14) | All true |

Token endpoint `POST /v1/auth/token` (RFC 6749 client credentials; run with `AUTH_ENABLED=true`).
Every error is checked for `{error, error_description}` only, `Cache-Control: no-store`, and no
traceback or secret in the body.

| TC | Category | Description | Expected |
|---|---|---|---|
| COMMON_TC_5 | Functional | Form-encoded `grant_type`, `client_id`, `client_secret`, `scope=schemes:read` | 200 `{access_token, token_type: "Bearer", expires_in: 3600, scope: "schemes:read"}`; the JWT's `sub`/`iss`/`aud` are right and it works on API 1 |
| COMMON_TC_6 | Functional | Same as JSON, no `scope` | 200, same body |
| COMMON_TC_7 | Functional | Credentials in HTTP Basic | 200, same body |
| COMMON_TC_8_SEC | Authentication | Wrong `client_secret` | 401 `invalid_client`, `WWW-Authenticate: Basic realm=…` |
| COMMON_TC_9_SEC | Authentication | Unknown `client_id` | 401 `invalid_client`, same message as a wrong secret (no client enumeration) |
| COMMON_TC_10_SEC | Negative | No `grant_type` | 400 `invalid_request` |
| COMMON_TC_11_SEC | Negative | `grant_type=password` | 400 `unsupported_grant_type` |
| COMMON_TC_12_SEC | Authorization | `scope=schemes:read admin` | 400 `invalid_scope` |
| COMMON_TC_13_SEC | Negative | Credentials in Basic **and** the body | 400 `invalid_request` |
| COMMON_TC_14_SEC | Method Abuse | `GET` with credentials in the query string | 405 |
| COMMON_TC_15_SEC | Headers | Success and error responses | `Cache-Control: no-store`, `Pragma: no-cache`, `nosniff` |
| COMMON_TC_16_SEC | Negative | `text/plain` body, broken JSON, a JSON array, a non-string value | 400 `invalid_request` |
| COMMON_TC_17_SEC | Parameter Pollution | `client_id` sent twice | 400 `invalid_request` |
| COMMON_TC_18_SEC | Authentication | No `client_secret`, an empty one, malformed Basic | 401 `invalid_client` |
| COMMON_TC_19_SEC | Rate Limiting | `TOKEN_RATE_LIMIT` + 5 token requests at once (runs when `TOKEN_RATE_LIMIT` is set; run alone); then two more with a made-up `X-Forwarded-For` (QA 2026-10-16 #1) | Some 429 with `Retry-After` ≤ 60 s, no 5xx; the spoofed requests are still 429; API 1 still works with the existing token |
| COMMON_TC_20_SEC | Parameter Pollution | JSON body with `client_secret` (or `client_id`) sent twice, the last one right (QA 2026-10-16 #9) | 400 `invalid_request` |

## OWASP API Security Top 10 (2023) coverage

| Risk | Status | How |
|---|---|---|
| API1 Broken Object Level Authorization | By design | One client gives access to every dealer (the client needs all dealers). If per-dealer access is ever needed, tokens must carry a dealer scope. |
| API2 Broken Authentication | Covered | OAuth2 client credentials (secrets stored as SHA-256 hashes, constant-time check, same error for unknown client and wrong secret) issuing 1-hour HS256 JWTs (algorithm pinned; `iss`/`aud`/`exp`/`nbf`/`jti` required; removing a client revokes its tokens); token endpoint rate-limited per IP. Tests: API1_TC_43–47_SEC and API1_TC_70–77 (missing, garbage, tampered, unsigned, other-key, expired, wrong-claim, wrong-scope tokens, other schemes, X-API-Key), COMMON_TC_3_SEC, COMMON_TC_5–19 (token endpoint), Postman folders 4 and 5. Auth is required outside `APP_ENV=local`; auth can be off only with `APP_ENV=local` set explicitly (pytest `test_api_refuses_to_run_open_when_app_env_is_unset`; manual start-up check in Step 3). |
| API3 Broken Object Property Level Authorization | Covered | Fixed response model; raw SAP columns are never returned (API1_TC_2). |
| API4 Unrestricted Resource Consumption | Yes | 4 KB body limit (API1_TC_54–56_SEC), bounded responses, DB pool limits with 503, per-IP rate limit of 2,500 requests/s with 429 (API1_TC_69_SEC), token endpoint 30 requests/min per IP (COMMON_TC_19_SEC). |
| API5 Broken Function Level Authorization | N/A | No admin or write endpoints. |
| API6 Unrestricted Access to Sensitive Business Flows | N/A | Read-only lookups. |
| API7 Server-Side Request Forgery | N/A | No URL inputs. |
| API8 Security Misconfiguration | Covered | Security headers, no stack traces, no server version (API1_TC_59–63_SEC, ZAP). The API refuses to start with weak, example or missing auth settings, including when `APP_ENV` is unset (pytest `tests/test_config.py`). TLS redirect to verify on deployment (COMMON_TC_4_SEC). |
| API9 Improper Inventory Management | Covered | Versioned `/v1`, OpenAPI spec in `docs/openapi.json`. Decide whether `/docs` is public in production (`DOCS_ENABLED`). |
| API10 Unsafe Consumption of APIs | Covered | SAP files are validated: required columns checked, exact component matching, bad rows logged and skipped. |
