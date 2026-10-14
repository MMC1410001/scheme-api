# Scheme API — Test Execution Report

| | |
|---|---|
| Date | 2026-10-15 (functional, Redis rate tests); 2026-10-14 (earlier k6 runs, security scans, API 2) |
| Build | `scheme-api` 0.1.0 (local, uncommitted changes of this date) |
| Environment | Local: Apple M2, 8 cores, 16 GB · Postgres 16 and Redis 7 in Docker · API with 4 uvicorn workers, `--no-server-header`, `API_KEY` set |
| Data | Sample SAP exports + 20,000 made-up dealers (160,017 scheme rows); sample reports v1/v2 for API 2. API 2 load test: the real report sample (30,000 dealers × 900 columns), loaded locally for the run and removed afterwards |

> Performance numbers are from a laptop with the database on the same machine. They show the
> API's behaviour and relative limits, not production capacity. Re-run k6 on the deployed environment.

## Summary

Latest run, 2026-10-15 (API 2 on hold, Redis response cache added):

| Suite | Total | Passed | Failed | Skipped |
|---|---|---|---|---|
| Unit tests (pytest) | 119 | 119 | 0 | 0 |
| Postman — API 1 collection (27 requests) — assertions | 115 | 115 | 0 | 0 |
| Playwright — API 1 (`api1_current_month.spec.js`) | 65 | 65 | 0 | 0 |
| Playwright — Common (`common.spec.js`, incl. COMMON_TC_5: API 2 returns 404) | 5 | 4 | 0 | 1 |
| **Playwright total** | **70** | **69** | **0** | **1** |
| Postman — API 2 collection / Playwright `api2_report.spec.js` | — | — | — | API 2 on hold: not run |

Last API 2 results before it was put on hold (2026-10-14): Postman 91/91 assertions, Playwright 56/56.

Playwright by category (API 1 + common): Functional 11 + 1, Data Validation 8, Edge 5,
Negative 18 + 2 (API 1 includes 6 month/year cases), Security 23 + 2.

Skipped: COMMON_TC_4_SEC (HTTP → HTTPS redirect) needs the deployed HTTPS URL.

API 2 (2026-10-14, before the hold) data check on the real report sample: 270,000 cells (300 dealers × 900 columns) compared
between the file and the API, 0 differences beyond the float32 cleanup (largest change 5.7e-8
relative). Load: 30,000 rows in 106 s, 180 MB peak memory, 36 MB in Postgres.

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

| Test | Requests | Throughput | Errors | Checks | Max VUs | API 1 p95 / p99 (ms) | API 2 p95 / p99 (ms) | Result |
|---|---|---|---|---|---|---|---|---|
| Smoke | 60 | 2/s | 0.00% | 100% | 1 | 11.7 / 29.0 | 12.2 / 12.6 | Pass |
| Load (150 VUs, 3 min) | 60,229 | 267/s | 0.00% | 100% | 150 | 5.5 / 8.6 | 6.3 / 9.5 | Pass |
| Stress (to 600 VUs) | 174,316 | 580/s | 0.00% | 100% | 600 | 253.9 / 374.8 | 233.4 / 340.8 | Pass |
| Spike (20 → 500 VUs) | 68,715 | 473/s | 0.00% | 100% | 500 | 150.5 / 401.9 | 141.8 / 367.3 | Pass |
| Load, API 2 only, real-width rows (`-e API=api2`, 150 VUs, 3 min) | 59,954 | 266/s | 0.00% | 100% | 150 | — | 11.6 / 55.1 | Pass |

The first four rows were measured before API 2 moved to the real report format (small sample rows).
API 1's query is unchanged since (it already filtered on year and month; they now come from the
request), so its numbers still apply; a k6 smoke run of API 1 with the new body passed. The last row is API 2 alone with ~40 KB, 900-column rows from the real
report: about half the requested codes exist in it, so half the responses are full rows
(1.1 GB served, 4.9 MB/s).

Observations:
- At expected load (150 VUs) responses stay under 10 ms at p99.
- Latency rises from ~300 VUs as the laptop's CPU saturates (k6, API and Postgres share 8 cores),
  but stays inside the 500 ms p95 threshold with zero errors up to 600 VUs.
- The spike test recovered to baseline latency after the burst with no errors.
- API 2 with real-width rows stays at p95 12 ms / p99 55 ms at 150 VUs; the p99 rise compared with
  small rows is JSON serialisation of 900 fields.

## Security

| Scan | Result |
|---|---|
| bandit (static analysis) | 0 issues (1 false positive reviewed and annotated — see below) |
| pip-audit (dependency CVEs) | No known vulnerabilities |
| OWASP ZAP API scan | 119 PASS · 0 FAIL · 0 WARN. 248 attack payloads sent to the two endpoints with a valid key; all rejected with 422, no 5xx. |

## Issues found and resolved during this run

### RESOLVED — API 2 loader could not handle the real report (High)
- **Found by:** loading the real report sample (about 100 MB, 30 million cells).
- **Cause:** the workbook was opened fully in memory (several GB for this file), and each row was
  stored as JSON with all 900 column names (about 1.4 GB per report in Postgres).
- **Fix:** streaming read (openpyxl read-only) with batched inserts; merged group names filled
  to the right; column names stored once per file and rows as value arrays; only the latest report
  (newest month, highest `_v<N>`) is loaded.
- **Verified:** 106 s, 180 MB memory, 36 MB stored; 270,000 cells match the file.

### RESOLVED — EB FULL MONTH Qualification was always 0 (Medium)
- **Found by:** comparing EB rows in the real SAP sample: SAP leaves `GSR (Q)` empty on all
  EB FULL MONTH rows, while on slab rows `GSR (Q)` always equals `GSR (B)` (on every slab row).
- **Fix:** EB FULL MONTH takes its Qualification from `GSR (B)`, following the slab working.
  Months already loaded are recalculated with `ingestion.run schemes --reload`.
- **Verified:** real sample nearly all EB FULL MONTH rows now have a non-zero Qualification (the
  other 14 have GSR 0 in SAP); unit test, API1_TC_19 and a Postman check cover it.

### RESOLVED — float32 noise in report numbers (Low)
- The report generator writes 32-bit floats (`0.4` arrives as `0.4000000059604645`).
- **Fix:** rounded to 7 significant digits, at least 2 decimals. Covered by API2_TC_10 and API2_TC_13.
  To be confirmed with the teammate (open item 6).

### RESOLVED — Connection pool deadlock under load (High)
- **Found by:** k6 load test, 150 VUs. 0.35% of requests (192) timed out; server log showed
  `PoolTimeout: couldn't get a connection after 30.00 sec`.
- **Cause:** the database connection was taken in a FastAPI dependency and released after the
  endpoint ran on a different threadpool thread. Under concurrency all 40 threads waited for the
  4 pooled connections while the requests holding them could not get a thread to finish.
- **Fix:** each query borrows and returns its connection on the same thread; pool size and wait
  are configurable (`DB_POOL_MAX`, default 10 per worker; 5 s wait); pool exhaustion now returns
  `503` with `Retry-After` instead of hanging.
- **Verified:** load re-run — 60,229 requests, 0 errors, p99 8.6 ms; stress and spike also clean.

### RESOLVED — FastAPI validation errors echoed caller input (Medium)
- Default 422 bodies include an `input` field with whatever the caller sent (payload reflection).
- **Fix:** custom handler returns only location, message and type. Covered by API1_TC_48–53_SEC and API2_TC_39–44_SEC.

### RESOLVED — Missing security headers (Medium)
- Added `X-Content-Type-Options`, `X-Frame-Options`, `Strict-Transport-Security`,
  `Content-Security-Policy`, `Cache-Control: no-store`, `Referrer-Policy`, and (from ZAP warning
  90004) `Cross-Origin-Resource-Policy`. Server runs with `--no-server-header`.

### RESOLVED — No request size limit (Medium)
- Any body size was parsed. Now limited to 4 KB (`413`), including chunked bodies.

### RESOLVED — Dealer code accepted any characters (Low)
- Now letters and digits only, max 20; numbers accepted as well as strings.

### REVIEWED — bandit B608 "possible SQL injection" (false positive)
- `ingestion/load.py` builds the upsert statement from a fixed tuple of column names; every value
  is a `%s` parameter. Annotated with `# nosec B608` and the reason.

## Open items / recommendations

| # | Item | Owner |
|---|---|---|
| 1 | Turn on `API_KEY` in every environment (currently optional; agreed "no auth" for now). Dev's dealer API already uses `x-api-key`. | Team decision |
| 2 | Rate limiting at the gateway / load balancer (not in the app). | Infra |
| 3 | HTTPS redirect check (COMMON_TC_4_SEC) and a k6 load run on the deployed environment. | QA after deploy |
| 4 | Decide whether `/docs` is public in production (`DOCS_ENABLED=false` to hide). | Team decision |
| 5 | Re-run Playwright with `SAMPLE_DATA=false` and real dealer codes once GCS data is connected. | QA |
| 6 | Confirm with the teammate: API 2 serves the latest month's report; the float32 rounding; the duplicate "Group A"/"GROUP A" group names. | Developer |
