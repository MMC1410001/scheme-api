# Scheme API — Test Execution Report

| | |
|---|---|
| Date | 2026-10-15 (functional, Redis rate tests); 2026-10-14 (earlier k6 runs, security scans) |
| Build | `scheme-api` 0.1.0 (local, uncommitted changes of this date) |
| Environment | Local: Apple M2, 8 cores, 16 GB · Postgres 16 and Redis 7 in Docker · API with 4 uvicorn workers, `--no-server-header`, `API_KEY` set |
| Data | Sample SAP exports + 20,000 made-up dealers (160,017 scheme rows) |

> Performance numbers are from a laptop with the database on the same machine. They show the
> API's behaviour and relative limits, not production capacity. Re-run k6 on the deployed environment.

## Summary

Latest run, 2026-10-15 (Redis response cache added):

| Suite | Total | Passed | Failed | Skipped |
|---|---|---|---|---|
| Unit tests (pytest) | 123 | 123 | 0 | 0 |
| Database tests (`tests/test_load_db.py`, local Postgres) | 13 | 13 | 0 | 0 |
| Postman — API 1 collection (27 requests) — assertions | 115 | 115 | 0 | 0 |
| Playwright — API 1 (`api1_current_month.spec.js`) | 65 | 65 | 0 | 0 |
| Playwright — Common (`common.spec.js`) | 4 | 3 | 0 | 1 |
| **Playwright total** | **69** | **68** | **0** | **1** |

Unit tests: 123 passed in a re-run on 2026-10-15 after the code cleanup (database tests not re-run).

Playwright by category (API 1 + common): Functional 11 + 1, Data Validation 8, Edge 5,
Negative 18 + 1 (API 1 includes 6 month/year cases), Security 23 + 2.

Skipped: COMMON_TC_4_SEC (HTTP → HTTPS redirect) needs the deployed HTTPS URL.

## Deployed environments

Runs on deployed environments are recorded privately. To repeat them: Playwright `npm run test:dev`
(or `API_BASE_URL=...`), newman with the Dev environment, k6 with `BASE_URL`; real test dealers go in
`qa/test-data/dealers.json` (gitignored), built read-only by `qa/test-data/pick_dealers.py`.
Make sure only one layer (proxy or app) sends `Strict-Transport-Security`; API1_TC_60_SEC checks it.

## Performance (k6)

### 1 lakh requests a minute, with and without the Redis cache (2026-10-15)

`qa/k6/rate.js`: a fixed 1,700 requests/s (≈ 1 lakh a minute, the peak seen from the dealer app)
for 2 minutes, no think time, API 1, 20,000 dealers (each asked for ~10 times), 4 workers.

| Run | Sent | Achieved | Not sent | Errors | Median | p95 | p99 | Cache hits |
|---|---|---|---|---|---|---|---|---|
| No cache (`CACHE_ENABLED=false`) | 192,986 | 1,596/s | 11,017 | 0 | 730 ms | 1,385 ms | 1,546 ms | — |
| Redis, starting empty | 198,084 | 1,651/s | 5,916 | 0 | 8 ms | 642 ms | 1,165 ms | 81% |
| **Redis, warm** | 203,345 | **1,695/s** | 656 | 0 | **1.3 ms** | **92 ms** | **303 ms** | 99% |

The warm-cache run passed every threshold; the others missed the latency and "not sent"
thresholds (the laptop runs k6, the API, Postgres and Redis on the same 8 cores). Before this run
two per-request costs were removed (a `BaseHTTPMiddleware` and thread hops for small dependencies);
that alone raised the ceiling for cache hits from 1,705/s to 2,922/s and for database reads from
1,432/s to 2,112/s (64 VUs, no think time). Re-run on the deployed environment with k6 on another
machine before relying on these numbers.

### Earlier runs (2026-10-14)

| Test | Errors | Checks | Max VUs | API 1 p95 / p99 (ms) | Result |
|---|---|---|---|---|---|
| Smoke | 0.00% | 100% | 1 | 11.7 / 29.0 | Pass |
| Load (150 VUs, 3 min) | 0.00% | 100% | 150 | 5.5 / 8.6 | Pass |
| Stress (to 600 VUs) | 0.00% | 100% | 600 | 253.9 / 374.8 | Pass |
| Spike (20 → 500 VUs) | 0.00% | 100% | 500 | 150.5 / 401.9 | Pass |

Request counts and throughput of these runs are not shown: they also counted traffic to an
endpoint that has since been removed, so they don't describe API 1 alone. API 1's query is
unchanged since (it already filtered on year and month; they now come from the request), so its
latency numbers still apply; a k6 smoke run of API 1 with the new body passed.

Observations:
- At expected load (150 VUs) responses stay under 10 ms at p99.
- Latency rises from ~300 VUs as the laptop's CPU saturates (k6, API and Postgres share 8 cores),
  but stays inside the 500 ms p95 threshold with zero errors up to 600 VUs.
- The spike test recovered to baseline latency after the burst with no errors.

## Security

| Scan | Result |
|---|---|
| bandit (static analysis) | 0 issues (1 false positive reviewed and annotated — see below) |
| pip-audit (dependency CVEs) | No known vulnerabilities |
| OWASP ZAP API scan | 119 PASS · 0 FAIL · 0 WARN (2026-10-14). Attack payloads sent with a valid key were all rejected with 422, no 5xx. |

## Issues found and resolved during this run

### RESOLVED — Loading a month took hours over the VPN (Medium)
- **Found by:** loading September into the shared database: about 30 s per file (10 s download,
  15–20 s saving ~5,500 rows one statement at a time), so ~4–5 hours for 533 files.
- **Fix:** the next 4 files are downloaded and parsed in parallel while one is saved; each file is
  saved with one `COPY` into a temporary table and one upsert; one run at a time (advisory lock);
  `--limit` for test runs.
- **Verified:** SAP export files into a throwaway local schema: 8 files in 48 s (~6 s per file,
  download-bound at ~3 MB/s), saving 0.3–0.5 s per file. A randomized database test shows the bulk
  upsert gives exactly the row-by-row result. A four-lens review (SQL, concurrency, behaviour, tests)
  found 15 smaller issues (e.g. `--limit` across reconnects, negative `--limit`, staging table on
  non-autocommit connections, slow Ctrl-C, untested GCS code); all fixed and covered by tests.
  A second pass (re-check of every finding, plus 16 deliberate code breaks) confirmed the fixes,
  caught 15 of 16 breaks, and found one more real problem: after a network drop the old session
  could keep the loader lock, so the reconnect would stop as if another run were active. Fixed: each
  run names its sessions and, on reconnect, ends its own stale session; failing that, the run exits
  with an error instead of a silent success. All breaks are now caught.
- **Not measured:** save time over the VPN into the shared database (writing there was not run).

### RESOLVED — EB FULL MONTH Qualification was always 0 (Medium)
- **Found by:** comparing EB rows in the real SAP sample: SAP leaves `GSR (Q)` empty on all
  EB FULL MONTH rows, while on slab rows `GSR (Q)` always equals `GSR (B)` (on every slab row).
- **Fix:** EB FULL MONTH takes its Qualification from `GSR (B)`, following the slab working.
  Months already loaded are recalculated with `ingestion.run schemes --reload`.
- **Verified:** real sample nearly all EB FULL MONTH rows now have a non-zero Qualification (the
  other 14 have GSR 0 in SAP); unit test, API1_TC_19 and a Postman check cover it.

### RESOLVED — Connection pool deadlock under load (High)
- **Found by:** k6 load test, 150 VUs. 0.35% of requests (192) timed out; server log showed
  `PoolTimeout: couldn't get a connection after 30.00 sec`.
- **Cause:** the database connection was taken in a FastAPI dependency and released after the
  endpoint ran on a different threadpool thread. Under concurrency all 40 threads waited for the
  4 pooled connections while the requests holding them could not get a thread to finish.
- **Fix:** each query borrows and returns its connection on the same thread; pool size and wait
  are configurable (`DB_POOL_MAX`, default 10 per worker; 5 s wait); pool exhaustion now returns
  `503` with `Retry-After` instead of hanging.
- **Verified:** load re-run — 0 errors, API 1 p99 8.6 ms; stress and spike also clean.

### RESOLVED — FastAPI validation errors echoed caller input (Medium)
- Default 422 bodies include an `input` field with whatever the caller sent (payload reflection).
- **Fix:** custom handler returns only location, message and type. Covered by API1_TC_48–53_SEC.

### RESOLVED — Missing security headers (Medium)
- Added `X-Content-Type-Options`, `X-Frame-Options`, `Strict-Transport-Security`,
  `Content-Security-Policy`, `Cache-Control: no-store`, `Referrer-Policy`, and (from ZAP warning
  90004) `Cross-Origin-Resource-Policy`. Server runs with `--no-server-header`.

### RESOLVED — No request size limit (Medium)
- Any body size was parsed. Now limited to 4 KB (`413`), including chunked bodies.

### RESOLVED — Dealer code accepted any characters (Low)
- Now letters and digits only, max 20; numbers accepted as well as strings.

### RESOLVED — Non-ASCII credentials returned 500 instead of 401 (Medium) — 2026-10-15
- **Found by:** exploratory probes: `X-API-Key: kéy` → `500` (`secrets.compare_digest` raises
  `TypeError` for non-ASCII `str`), with a stack trace logged per request.
- **Fix:** the `X-API-Key` check was replaced the same day by OAuth2 client credentials + JWT (next
  entry). Non-ASCII Bearer tokens and Basic credentials are rejected as `401`; a JSON token request
  with a value that is not valid UTF-8 (e.g. a lone `\ud800`) is a `400 invalid_request`.
- **Verified:** pytest `test_non_ascii_credentials_return_401_not_500`,
  `test_secret_that_is_not_valid_utf8_gets_400_not_500`; API1_TC_70_SEC; Postman "Non-ASCII Bearer token".

### DONE — Authentication: OAuth2 client credentials + JWT Bearer tokens replace X-API-Key — 2026-10-15
- `POST /v1/auth/token` issues 1-hour HS256 tokens; `/v1/schemes` needs `Authorization: Bearer`.
  Client secrets are stored only as SHA-256 hashes; outside `local` the API will not start without
  valid auth settings. Separate token-endpoint limit: 30 per IP per minute.
- **Reviewed:** three independent reviews (auth security, HTTP/RFC correctness, operations and test
  validity), each finding checked by a skeptic who tried to refute it. Confirmed and fixed: an empty
  `AUTH_CLIENTS=` crashed settings loading (API and loader); an empty `JWT_PREVIOUS_SECRET=` stopped
  the API from starting; a lone surrogate in a JSON `client_secret` gave `500` (now `400`); the unit
  tests shared the developer's Redis rate-limit counters, so results depended on test order (now
  counted in memory from zero per test). All four have regression tests.
- **Verified:** pytest 253 passed; Playwright 93 passed / 3 skipped; newman 278/278; k6 smoke clean.
  Full Playwright and newman runs together make 36 token requests, above the 30/min token limit: run
  them a minute apart (see qa/README.md).

### RESOLVED — API ran without auth when APP_ENV was unset (Medium) — 2026-10-15
- **Found by:** security review of the whole codebase: `APP_ENV` defaults to `local`, and locally
  with no `AUTH_CLIENTS`/`JWT_SECRET` auth was off with only a log warning. A server deployed without
  `APP_ENV` and without auth settings would have served `/v1/schemes` to anyone.
- **Fix:** auth can be off only when `APP_ENV=local` is set explicitly. With `APP_ENV` unset and no
  auth settings the API refuses to start (`APP_ENV is not set and there are no auth settings`);
  with auth settings it starts as before (`app/main.py`).
- **Verified:** pytest `test_api_refuses_to_run_open_when_app_env_is_unset` and the updated
  `test_api_starts_with_auth_settings_or_locally_without_any`; pytest 254 passed, 29 skipped.
  Manual check in qa/README.md Step 3 (start-up safety). Not testable from Playwright/Postman/k6:
  they need a running server.

### RESOLVED — 500 responses had no security headers (Low) — 2026-10-15
- **Found by:** the same 500: no `Cache-Control: no-store`, CSP, HSTS, nosniff or X-Frame-Options.
- **Cause:** Starlette sends the catch-all `Exception` handler's response from its outermost layer,
  outside `SecurityHeadersMiddleware`.
- **Fix:** the handler sets `SECURITY_HEADERS` itself (`app/main.py`).
- **Verified:** `test_unexpected_errors_return_a_generic_500` now checks every security header.

### RESOLVED — Two periods of one agreement in one month folder overwrote each other (Medium) — 2026-10-15
- **Found by:** code review: `scheme_records` was keyed by year, month, dealer, agreement and deal
  suffix only. A month folder holding the same agreement and suffix for two validity periods (e.g. a
  late SAP update of last month's scheme next to this month's) kept only the newer row, silently.
- **Fix:** the validity period is part of the key (unique index `scheme_records_key`, empty dates
  count as equal); the loader's upsert uses it. Existing databases are upgraded in place by the next
  load run or `init-db` (old primary key dropped). Rows already lost need a `--reload` (open item 6).
- **Verified:** database tests for both periods kept, newer update replacing only its own period,
  empty dates, the upgrade from the old key (incl. the `next_slab_rate` back-fill, now matched by
  row id), and the API's month answer; the randomized bulk-vs-row-by-row test now mixes periods.
  Validity periods are normally fixed per scheme, so normal data is unaffected.
- **Reviewed:** four independent reviews (SQL/migration, loader, HTTP, test strength), each finding
  checked by two skeptics. Confirmed and fixed: on PostgreSQL 11/12 the upgrade added one more
  `status` CHECK constraint per loader run (now upgraded in a DO block; verified on PG 11.16, 12.22
  and 16, fresh and upgraded, 3 runs each → 1 constraint); each bad row was logged twice (now once);
  the retry test could not tell a new download from a re-parse (now asserts 2 downloads). Upgrade
  and concurrent runs over 500k old rows were verified on PG 11–16. Note: for GCS files the
  re-read fetches the same, checksum-verified version, so it can only help a damaged local copy.
- **Local database migrated** (`init-db`, then `schemes --reload`): primary key gone, key index and
  `ingested_files.status`/`rows_bad` present, 6 files reloaded, 0 failed; dealer 6100000001 still
  gets its 8 October rows with the newer LUXURY values.

### RESOLVED — Rows that could not be read were dropped silently (Low) — 2026-10-15
- **Found by:** code review: bad rows were only logged; the file was recorded as loaded and never
  looked at again.
- **Fix:** such a file is read once more; if rows are still bad, its good rows are loaded and the
  file is recorded with `status = 'failed'` and `rows_bad` in `ingested_files`, the run exits with
  code 1, and the file is not retried until it is re-uploaded or reloaded.
- **Verified:** `tests/test_run.py` (second read clean → loaded; still bad → good rows saved, failed,
  bad rows counted once; clean file read once; cache refreshed and exit 1) and
  `tests/test_load_db.py` (status, `rows_bad`, upgrade of an old `ingested_files`).

### RESOLVED — `HEAD /health` returned 405; a trailing slash got a 307 redirect (Low) — 2026-10-15
- **Fix:** `HEAD /health` answers 200 (kept out of the OpenAPI spec); `redirect_slashes=False`, so
  `/v1/schemes/current-month/` is a 404 instead of a redirect that could point at `http://` behind
  the TLS proxy. **Verified:** `tests/test_routes_http.py`.

### REVIEWED — bandit B608 "possible SQL injection" (false positive)
- `ingestion/load.py` builds the upsert statement from a fixed tuple of column names; every value
  is a `%s` parameter. Annotated with `# nosec B608` and the reason.

## Open items / recommendations

| # | Item | Owner |
|---|---|---|
| 1 | ~~Turn on `API_KEY` in every environment (currently optional; agreed "no auth" for now). The existing dealer API already uses `x-api-key`.~~ **Done 2026-10-15, superseded:** `X-API-Key` is replaced by OAuth2 client credentials + JWT Bearer tokens (`POST /v1/auth/token`), required outside `APP_ENV=local`. QA suites updated (API1_TC_43–47_SEC, API1_TC_70–77, COMMON_TC_5–19, Postman folder 5. Auth). | Team decision |
| 2 | Rate limiting at the gateway / load balancer (not in the app). | Infra |
| 3 | HTTPS redirect check (COMMON_TC_4_SEC) and a k6 load run on the deployed environment. | QA after deploy |
| 4 | Decide whether `/docs` is public in production (`DOCS_ENABLED=false` to hide). | Team decision |
| 5 | Re-run Playwright with `SAMPLE_DATA=false` and real dealer codes once GCS data is connected. | QA |
| 6 | Reload every month already loaded on dev/UAT (`ingestion.run schemes --year Y --month M --reload`) so rows written over before the validity period joined the row key come back. | DevOps / QA after deploy |
