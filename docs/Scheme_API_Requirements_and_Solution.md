# Scheme API — Requirements & Solution Document

| | |
|---|---|
| Project | Scheme API (API 1: monthly schemes) |
| Consumer | client team |
| Version | 0.4 (draft) — API 2 removed; document covers API 1 only |
| Date | 15 October 2026 |
| Author | Project team |
| Status | API 1 built and tested on sample and real SAP data. |

## Index

**Part A — Requirements**

1. Purpose and scope
2. Business context
3. Functional requirements
4. Input data: SAP scheme export
5. Output: API response template
6. Non-functional requirements
7. Assumptions
8. Open questions and dependencies

**Part B — Solution**

9. Solution overview and architecture
10. Technology stack
11. Data ingestion design
12. Data model
13. API specification
14. Error handling
15. Security design
16. Configuration
17. Project structure
18. Deployment and operations
19. Postman collection and Swagger spec
20. Testing and quality results

**Appendices**

- A. Example API calls
- B. Glossary

**List of figures**

- Figure 1 — Business context
- Figure 2 — API 1: from SAP export to API response
- Figure 3 — Solution architecture
- Figure 4 — Ingestion job: what happens to each file
- Figure 5 — API request lifecycle and responses
- Figure 6 — Data model (PostgreSQL)

# Part A — Requirements

## 1. Purpose and scope

The client team needs Scheme data for individual dealers. Scheme data is produced in SAP and
exported as files to Google Cloud Storage (GCS). This project builds an API that serves that data
by dealer code.

**In scope**

- API 1 — a dealer's scheme position for a month the caller chooses (8 scheme components).
- A scheduled job that loads the SAP exports from GCS into a database.
- Documentation, a Postman collection, and functional, performance and security testing.

**Out of scope**

- Changing data in SAP or GCS; the API is read-only.
- A user interface.

## 2. Business context

SAP exports scheme data every day into GCS, one folder per month. The API reads this data and
returns it to the client's systems on request.

![Figure 1 — Business context](figures/fig1_business_context.png)

## 3. Functional requirements

| ID | Requirement | Priority |
|---|---|---|
| FR-1 | API 1 takes a dealer code, a month and a year in the request body and returns that dealer's scheme rows for that month. | Must |
| FR-2 | API 1 returns only these 8 components: EB SLAB 1, EB SLAB 2, EB SLAB 3, VALUE, LUXURY, PREMIUM 01, PREMIUM 02, EB FULL MONTH. All other SAP components are excluded. | Must |
| FR-3 | API 1 returns the columns of the agreed API template (section 5). | Must |
| FR-4 | EB slabs are returned as separate rows (one row per slab). | Must |
| FR-5 | Qualification is the GSR value for EB and Value, and the invoiced quantity for Premium and Luxury. EB FULL MONTH follows the same GSR working as the EB slabs. | Must |
| FR-6 | Rows are sorted by validity date. | Must |
| FR-7 | Only the requested month's data is returned. A row belongs to the month of the GCS folder its file came from. The database keeps every month. | Must |
| FR-8 | SAP exports are loaded from GCS automatically by a scheduled (cron) job. | Must |
| FR-9 | Each daily file holds only that day's updates, so the API must show the latest version of every row across all of the month's files. | Must |
| FR-10 | Dealer code and customer code are the same identifier. | Must |

## 4. Input data

### 4.1 API 1 — SAP scheme export

| Property | Value |
|---|---|
| Location | `gs://acme-scheme-files/sap-exports/<year>/<month>/`, e.g. `.../2026/10/`; a new folder each month |
| File name | `sap-exports_YYYY_MM_main_YYYYMMDD_HHMMSS_NNN.csv` (NNN = part number) |
| Format | CSV, one header row, 53 columns |
| Size | Up to 50,000 rows per file; large exports are split into parts; about 550 files a month |
| Content | Only the rows SAP updated on that day (not a full snapshot); one file contains many dealers |
| Components | 66 different components ("Comp desc"); 8 are used |
| Row identity | Customer + Agreement + Deal Suffix (unique within a file) |
| Dates | DD/MM/YYYY; empty dates appear as 00/00/0000 |
| Numbers | Trailing spaces; negative numbers have the minus sign at the end, e.g. `882.00-` |

SAP columns used by API 1:

| SAP column | Used for |
|---|---|
| Customer | Dealer code |
| Agreement, Deal Suffix | Row identity |
| Comp desc | Component name (filter to the 8 components) |
| Valid from, To | Validity period |
| GSR (Q) | Qualification for EB SLAB 1–3 and Value |
| Invoiced Quantity(Q) | Qualification for Luxury and Premium |
| GSR (B) | GSR B |
| Rebate Rate | Rebate rate |
| Clear Cut CN | Clear cut |
| Short Fall, Short Fall % | Shortfall amount and percentage |
| Last Updated Date, Last Updated Time | Deciding which version of a row is the latest |

![Figure 2 — API 1: from SAP export to API response](figures/fig2_input_output.png)

## 5. Output

### 5.1 API 1 — response template

One row per component, matching the agreed API template:

| Template column | API field | Type |
|---|---|---|
| Customer | `Customer` | string |
| From Date | `FromDate` | date (YYYY-MM-DD) |
| To Date | `ToDate` | date (YYYY-MM-DD) |
| Component description | `ComponentDescription` | string |
| Qualification (GSR / invoiced qty) | `Qualification` | number |
| GSR B | `GsrB` | number |
| Rebate rate | `RebateRate` | number |
| Clear cut | `ClearCut` | number |
| Shortfall Amount | `ShortfallAmount` | number |
| Short fall % | `ShortfallPct` | number |

## 6. Non-functional requirements

| ID | Area | Requirement | Target |
|---|---|---|---|
| NFR-1 | Performance | Response time at expected load (150 concurrent users) | p95 < 500 ms, p99 < 1 s, errors < 1% |
| NFR-8 | Performance | Peak load from the dealer app: planned 600 requests a minute, observed up to 1 lakh a minute | 1,700 requests/s with < 1% errors; Redis response cache (section 18.1) |
| NFR-2 | Availability | When overloaded, fail fast with a retryable response instead of hanging | 503 with Retry-After within 5 s |
| NFR-3 | Freshness | New SAP exports appear in the API without manual steps | Within one cron cycle (suggested 30 minutes) |
| NFR-4 | Security | Protected against OWASP API Top 10 risks; no data leakage in errors | No ZAP failures, no bandit findings, no known CVEs |
| NFR-5 | Auditability | Every stored row keeps its original SAP values and source file name | Raw row stored with each record |
| NFR-6 | Reliability | Re-running the job or loading files out of order never produces wrong data | Idempotent loads; newest update wins |
| NFR-7 | Maintainability | Automated tests at unit, API, performance and security level | All suites runnable from the repository |

## 7. Assumptions

These assumptions were made while building the solution. Please confirm or correct them.

| ID | Assumption | Basis |
|---|---|---|
| A-1 | The caller (the client) chooses the month: `month` 1–12 and `year` 2000–2100, as JSON numbers or numeric strings. A month whose SAP exports have not been loaded returns an empty list. The cron job loads the current month, decided in India time (Asia/Kolkata); earlier months are loaded once with `ingestion.run schemes --year --month`. | Team decision (the client sends the month) |
| A-2 | A row belongs to the month of the folder its file is in. A scheme running across two months is reported in the folder's month. | Confirmed in discussion |
| A-3 | The API template's date headers are swapped (the column labelled "To Date" holds start dates). The API returns `FromDate` = SAP "Valid from" and `ToDate` = SAP "To". | Template sample values |
| A-4 | The template is the final output. Next slab rate, prepaid rate and customer name (from the first discussion) are not returned; "Rebate rate" is taken to be the rate that was meant. | Confirmed: follow the template |
| A-5 | A row is identified by Customer + Agreement + Deal Suffix. The version with the newest "Last Updated Date/Time" is kept; with equal timestamps the file loaded later wins. | Sample file analysis |
| A-6 | "Last Updated" times from SAP use one time zone and can be compared across files. | Single SAP system |
| A-7 | Component names are matched exactly as SAP spells them (e.g. `PREMIUM 01`). If SAP renames a component, the list in `app/constants.py` must be updated. | Look-alikes such as `ALWD EB2` and `ADDTNL PREMIUM 01` must be excluded |
| A-8 | Qualification uses GSR (Q) for EB SLAB 1–3 and VALUE, and Invoiced Quantity (Q) for LUXURY, PREMIUM 01 and PREMIUM 02. EB FULL MONTH follows the EB slab working: SAP leaves its GSR (Q) empty, and on slab rows GSR (Q) always equals GSR (B), so its Qualification is GSR (B). Real sample: GSR (Q) = GSR (B) on every slab row; GSR (Q) empty on every EB FULL MONTH row. | Discussion and template values |
| A-9 | Dealer codes are compared without leading zeros (`0006100000001` = `6100000001`) and contain only letters and digits, at most 20 characters. | SAP pads codes with zeros |
| A-10 | If a dealer has more than one agreement, one row per agreement per component is returned. The sample data has one agreement per dealer. | Sample file analysis |
| A-11 | An unknown dealer, or a dealer without data in the requested month, returns `200` with an empty list, not `404`. | Simplest for consumers |
| A-12 | The API uses `POST` with a JSON body, and response fields use PascalCase like the existing dealer APIs. | Discussion; existing Acme APIs |
| A-13 | SAP exports are in bucket `acme-scheme-files`, folder `sap-exports/<year>/<month>/` (confirmed: `sap-exports/2026/10`). Folders for January–September are assumed to be `01`–`09`; the job falls back to `1`–`9` if those don't exist. | Path confirmed |
| A-14 | No authentication was agreed for now. API key support (`X-API-Key`) is built in and switched on by setting `API_KEY`; turning it on is recommended. Local development has it on by default (`API_KEY=local-test-key`, matching the Postman Local environment). | Discussion; security review |
| A-15 | The project team's existing PostgreSQL database is used. A separate database can be created quickly if needed. | Decision in discussion |
| A-16 | The cron job runs every 30 minutes; SAP exports arrive several times a day. | ~550 files a month |
| A-17 | HTTPS termination and rate limiting are provided by the hosting gateway or load balancer. | Standard deployment |
| A-18 | Expected load is modelled as 150 concurrent users, as in the existing dealer API tests. Actual the client call volumes are not known. | Existing test practice |
| A-19 | Only `.csv` and `.xlsx` files are loaded from GCS. | Formats seen so far |
| A-20 | EB amounts are running totals from the 1st of the month: EB SLAB 1 (e.g. 1–10 Oct, 10 lakh) is included in EB SLAB 2 (1–15 Oct, 12 lakh), so 2 lakh were added, not 22 lakh in total. The API returns each row as SAP sends it and never adds rows together; a newer SAP update of the same row replaces it. Consumers must not sum the slabs. | Confirmed by the team; real sample: EB amounts never decrease from slab to slab (every dealer checked) |

## 8. Open questions and dependencies

| # | Item | Needed for |
|---|---|---|
| 1 | GCS access (service-account key); the GCS path is confirmed | Connecting to real data |
| 2 | Database credentials | Production data load |
| 3 | Decision on API key authentication | Security (recommended) |
| 4 | Deployment environment and base URLs (Dev, Production) | Go-live, HTTPS and rate-limit checks |
| 5 | Whether the interactive docs page (`/docs`) is public in production | Security configuration |
| 6 | ~~Start of month behaviour~~ Resolved: the client sends the month, so a month without data simply returns `[]`. | — |
| 7 | ~~Month in the API 1 request~~ Built: `month` and `year` are required. Remaining: which earlier months must be loaded (backfilled) before go-live, and whether the endpoint should be renamed (its path still says `current-month`). | Go-live data, API naming |
| 8 | Redis on UAT/Production: the `REDIS_URL` already set up for other dealer APIs, and whether this API shares it | Response cache |

# Part B — Solution

## 9. Solution overview and architecture

The solution has two parts that share a PostgreSQL database:

1. **Ingestion job (cron).** Lists new files in GCS, validates and parses them, and stores the
   latest version of each scheme row. Files already loaded are skipped.
2. **API service (FastAPI).** Answers dealer lookups from the database. Each request is a single
   indexed query, so responses are fast and do not depend on file sizes.

Separating loading from serving means the API never parses files during a request, and a bad file
cannot break the API.

![Figure 3 — Solution architecture](figures/fig3_architecture.png)

## 10. Technology stack

| Layer | Technology | Why |
|---|---|---|
| Language | Python 3.10+ | Team standard; same as the existing Acme APIs |
| API framework | FastAPI + uvicorn | Same as the existing Acme APIs; automatic OpenAPI docs and input validation |
| Database | PostgreSQL 16 (psycopg 3, connection pool) | Existing team database; upserts and JSON columns |
| File storage | Google Cloud Storage (google-cloud-storage) | Where the SAP exports already land |
| Excel / CSV | openpyxl (streaming read-only mode), csv | Reads multi-row headers; streams files row by row |
| Packaging | uv | Locked, reproducible dependencies |
| Testing | pytest, Postman/newman, Playwright, k6, bandit, pip-audit, OWASP ZAP | Unit, API, performance and security coverage |

## 11. Data ingestion design

The cron job runs `python -m ingestion.run schemes`.
Each run reads the current month's folder, `gs://acme-scheme-files/sap-exports/<year>/<month>/`
(for example `.../2026/10/`, then `.../2026/11/`). Months 1–9 are tried as `01` first, then `1`,
and the job logs a warning if the folder has no files yet. The API itself never reads GCS: it only
reads the database the job keeps up to date.

![Figure 4 — Ingestion job: what happens to each file](figures/fig4_ingestion_flow.png)

Key design points:

- **Skip loaded files.** Each loaded file is recorded with its GCS version ("generation"). An
  unchanged file is skipped; a re-uploaded file is loaded again.
- **Upsert, newest wins.** Rows are inserted or updated by Customer + Agreement + Deal Suffix. An
  existing row is replaced only if the new one has a newer or equal "Last Updated" time, so files
  can arrive in any order.
- **One transaction per file.** A file is either fully loaded or not at all.
- **Fast loading.** The next `LOADER_WORKERS` files (default 4) are downloaded and parsed in
  parallel while the current one is saved (still one at a time, in order). Each file's rows go to
  the database with one bulk `COPY` into a temporary table and one `INSERT … SELECT … ON CONFLICT`,
  which gives exactly the result of upserting the rows one by one (proved by a randomized database
  test). Real September files: about 6 s per file from a laptop (download-bound), against about 30 s
  with one statement per row; saving takes 0.3–0.5 s.
- **One run at a time.** A Postgres advisory lock stops a second run (e.g. the next cron run during
  a long backfill); it is released when the run's connection closes, even if the process dies.
  After a network drop the server may keep the old session (and the lock) alive; the run names its
  sessions, so on reconnect it ends its own stale session and carries on.
- **Fail loudly on format changes.** If a required column is missing, the file fails with an error
  that lists the missing and available columns; nothing is loaded from it.
- **Row-level tolerance.** A row with an unparseable value is skipped and logged; the rest of the
  file loads.
- **Summary log.** Each run logs files loaded/skipped/failed and rows loaded/dropped/bad, and exits
  with code 1 if any file failed (so the scheduler can alert).

Performance: a 50,000-row SAP file loads in about 1.6 seconds; 160,000 rows in 13 seconds.

## 12. Data model

![Figure 6 — Data model (PostgreSQL)](figures/fig6_data_model.png)

| Table | Purpose | Key |
|---|---|---|
| `scheme_records` | Latest version of each SAP scheme row for API 1, plus the original row as JSON | year, month, dealer_code, agreement, deal_suffix |
| `ingested_files` | Which files have been loaded, their GCS version and row count | kind + gcs_path |

`python -m ingestion.run init-db` creates the tables and upgrades older databases in place.

## 13. API specification

**Conventions**

| Item | Value |
|---|---|
| Base path | `/v1` |
| Format | JSON request and response, UTF-8 |
| Method | `POST` with a JSON body `{"dealer_code", "month", "year"}` |
| Authentication | `X-API-Key` header (when enabled) |
| Field naming | PascalCase in responses |
| Empty result | `200` with `[]` |
| Specification | OpenAPI 3: `docs/openapi.json` and a Swagger spec in `qa/swagger/`; Swagger UI at `/docs` when enabled |

**Endpoints**

| Method | Path | Description | Auth |
|---|---|---|---|
| POST | `/v1/schemes/current-month` | API 1 — dealer's scheme rows for the requested month | X-API-Key |
| GET | `/health` | Liveness check, returns `{"ok": true}` | None |

### 13.1 Request body

```json
{ "dealer_code": "6100000001", "month": 10, "year": 2026 }
```

| Field | Type | Rules |
|---|---|---|
| `dealer_code` | string or number | Required; letters and digits; 1–20 characters; leading zeros and surrounding spaces ignored |
| `month` | integer or numeric string | Required; 1–12; `true`/`false`, decimals and text are rejected (422) |
| `year` | integer or numeric string | Required; 2000–2100 |

### 13.2 API 1 — `POST /v1/schemes/current-month`

**Example call**

```bash
curl -X POST "https://<host>/v1/schemes/current-month" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <your key>" \
  -d '{"dealer_code": "6100000001", "month": 10, "year": 2026}'
```

**Example response — `200 OK`** (sample dealer; all 8 components)

```json
[
  {"Customer": "6100000001", "FromDate": "2026-10-01", "ToDate": "2026-10-11", "ComponentDescription": "EB SLAB 1", "Qualification": 405232.86, "GsrB": 405232.86, "RebateRate": 0.75, "ClearCut": 0.0, "ShortfallAmount": 0.0, "ShortfallPct": 0.0},
  {"Customer": "6100000001", "FromDate": "2026-10-01", "ToDate": "2026-10-18", "ComponentDescription": "EB SLAB 2", "Qualification": 405232.86, "GsrB": 405232.86, "RebateRate": 0.75, "ClearCut": 0.0, "ShortfallAmount": 0.0, "ShortfallPct": 0.0},
  {"Customer": "6100000001", "FromDate": "2026-10-01", "ToDate": "2026-10-25", "ComponentDescription": "EB SLAB 3", "Qualification": 405232.86, "GsrB": 405232.86, "RebateRate": 0.75, "ClearCut": 0.0, "ShortfallAmount": 0.0, "ShortfallPct": 0.0},
  {"Customer": "6100000001", "FromDate": "2026-10-01", "ToDate": "2026-10-31", "ComponentDescription": "VALUE", "Qualification": 405232.86, "GsrB": 405232.86, "RebateRate": 0.0, "ClearCut": 0.0, "ShortfallAmount": 0.0, "ShortfallPct": 0.0},
  {"Customer": "6100000001", "FromDate": "2026-10-01", "ToDate": "2026-10-31", "ComponentDescription": "LUXURY", "Qualification": 315.1, "GsrB": 159292.8, "RebateRate": 1.0, "ClearCut": 1592.93, "ShortfallAmount": 84.9, "ShortfallPct": 21.23},
  {"Customer": "6100000001", "FromDate": "2026-10-01", "ToDate": "2026-10-31", "ComponentDescription": "PREMIUM 01", "Qualification": 0.0, "GsrB": 0.0, "RebateRate": 0.0, "ClearCut": 0.0, "ShortfallAmount": 40.0, "ShortfallPct": 100.0},
  {"Customer": "6100000001", "FromDate": "2026-10-01", "ToDate": "2026-10-31", "ComponentDescription": "PREMIUM 02", "Qualification": 0.0, "GsrB": 0.0, "RebateRate": 0.0, "ClearCut": 0.0, "ShortfallAmount": 8.0, "ShortfallPct": 100.0},
  {"Customer": "6100000001", "FromDate": "2026-10-01", "ToDate": "2026-10-31", "ComponentDescription": "EB FULL MONTH", "Qualification": 405232.86, "GsrB": 405232.86, "RebateRate": 2.25, "ClearCut": 0.0, "ShortfallAmount": 0.0, "ShortfallPct": 0.0}
]
```

**Response fields**

| Field | Type | Meaning | SAP source |
|---|---|---|---|
| `Customer` | string | Dealer code | Customer |
| `FromDate` | date | Start of validity | Valid from |
| `ToDate` | date | End of validity | To |
| `ComponentDescription` | string | One of the 8 components | Comp desc |
| `Qualification` | number | GSR (EB, Value) or invoiced quantity (Luxury, Premium) | GSR (Q); GSR (B) for EB FULL MONTH; Invoiced Quantity(Q) |
| `GsrB` | number | Gross Sales Revenue for the benefit | GSR (B) |
| `RebateRate` | number | Rebate rate | Rebate Rate |
| `ClearCut` | number | Clear-cut credit note amount | Clear Cut CN |
| `ShortfallAmount` | number | Shortfall to the next slab | Short Fall |
| `ShortfallPct` | number | Shortfall as a percentage | Short Fall % |

Ordering: by `ToDate`, then in the order EB SLAB 1, EB SLAB 2, EB SLAB 3, VALUE, LUXURY,
PREMIUM 01, PREMIUM 02, EB FULL MONTH. Amounts can be negative or `null`.

### 13.3 Request lifecycle

![Figure 5 — API request lifecycle and responses](figures/fig5_request_lifecycle.png)

## 14. Error handling

All errors are JSON with a `detail` field. Errors never include stack traces, SQL, internal
addresses or the caller's input.

| Status | Meaning | Example body | Client action |
|---|---|---|---|
| 200 | Success (possibly `[]`) | `[...]` | — |
| 401 | Missing or wrong API key | `{"detail": "Invalid or missing API key"}` | Check the key |
| 404 | Unknown path | `{"detail": "Not Found"}` | Check the URL |
| 405 | Wrong method | `{"detail": "Method Not Allowed"}` | Use POST |
| 413 | Body over 4 KB | `{"detail": "Request body too large"}` | Send only `dealer_code` |
| 422 | Invalid body | `{"detail": [{"loc": ["body", "dealer_code"], "msg": "Field required", "type": "missing"}]}` | Fix the request |
| 503 | Temporarily busy | `{"detail": "Service busy, please retry"}` + `Retry-After: 2` | Retry with backoff |
| 500 | Unexpected error | `{"detail": "Internal server error"}` | Report with time of request |

## 15. Security design

| Control | Implementation |
|---|---|
| Authentication | `X-API-Key` header, constant-time comparison; `/health` stays open for probes |
| Input validation | `dealer_code`: letters and digits, 1–20 characters; `month` 1–12 and `year` 2000–2100 (no booleans); strict JSON parsing |
| SQL injection | All queries are parameterised; no user input is ever formatted into SQL |
| Payload limits | Request body capped at 4 KB (413), including chunked bodies |
| Response headers | `X-Content-Type-Options: nosniff`, `Strict-Transport-Security` (1 year), `Content-Security-Policy: default-src 'none'`, `X-Frame-Options: DENY`, `Cache-Control: no-store`, `Referrer-Policy: no-referrer`, `Cross-Origin-Resource-Policy: same-origin` |
| Information disclosure | Custom error handlers: no stack traces, no echoed input; server version header disabled |
| Overload protection | Bounded DB connection pool; 503 + Retry-After when exhausted |
| Data exposure | Fixed response model; raw SAP columns are stored but never returned |
| Transport and rate limits | HTTPS and rate limiting at the gateway (assumption A-17) |
| Secrets | API key and DB credentials come from environment variables or the environment's `.env.<env>` file, which is never committed |

## 16. Configuration

All settings are environment variables or a settings file per environment, chosen by `APP_ENV`:
`.env.local` (default), `.env.dev`, `.env.uat`, `.env.prod`. Environment variables win over the file.
One template, `.env.example`, is committed and marks what changes per environment; DevOps fill in
the database, `API_KEY`, Redis and GCS key, and keep the real values in a secret manager. The real
files are ignored by git. Branches `dev`, `uat` and `prod` (from `main`)
are available for per-environment releases.

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | local Postgres | Database connection |
| `API_KEY` | `local-test-key` in `.env.example` (local only); unset = no auth | Required `X-API-Key` value; use a long random secret on Dev/Production |
| `DOCS_ENABLED` | `true` | Serve `/docs` and `/openapi.json` |
| `DB_POOL_MAX` | `10` | DB connections per worker process |
| `GCS_BUCKET` | `acme-scheme-files` | Bucket with the files |
| `GOOGLE_APPLICATION_CREDENTIALS` | — | Service-account key file for GCS |
| `GCS_PROJECT` | — | Optional Google Cloud project for the GCS client; reading the bucket works without it |
| `SCHEME_PREFIX_TEMPLATE` | `sap-exports/{year}/{month:02d}/` | Folder of a month's SAP exports (tries `01`, then `1`) |
| `APP_ENV` | `local` | Which settings file to read: `.env.local`, `.env.dev`, `.env.uat` or `.env.prod` |
| `LOCAL_DATA_DIR` | — | Read files from a local folder instead of GCS (development) |
| `LOADER_WORKERS` | `4` | Files the cron job downloads and parses in parallel while saving the current one |
| `CACHE_ENABLED` | `false` | Turns the Redis response cache on or off (section 18.1) |
| `REDIS_URL` | — | Redis for the cache, e.g. `redis://host:6379/0`; the cache runs only when this is set and `CACHE_ENABLED=true` |
| `CACHE_TTL_SECONDS` | `900` | Longest a cached response lives; new data replaces it sooner |

## 17. Project structure

```
scheme-api/
├── app/                  FastAPI service
│   ├── main.py           app setup, error handlers, /health
│   ├── routes.py         API 1 endpoint
│   ├── schemas.py        request and response models
│   ├── repository.py     SQL queries (connection pool)
│   ├── security.py       X-API-Key check
│   ├── middleware.py     security headers, body size limit
│   ├── constants.py      the 8 components and qualification rules
│   └── config.py         settings from environment variables
├── ingestion/            cron job
│   ├── run.py            entry point: init-db, schemes
│   ├── sources.py        list/read files in GCS (or a local folder)
│   ├── excel.py          CSV/Excel streaming reader, header joining
│   ├── schemes.py        SAP business rules and parsing (API 1)
│   └── load.py           upsert / batched insert into PostgreSQL
├── sql/001_init.sql      tables and indexes
├── docs/                 this document, API guide, OpenAPI spec, figures
├── qa/                   QA guide and test results
│   ├── postman/          API 1 collection + Local/Dev environments
│   │                     (built by build_collection.py)
│   ├── swagger/          API 1 OpenAPI spec (built by build_specs.py)
│   ├── api-tests/tests/  Playwright: api1_current_month.spec.js, common.spec.js,
│   │                     helpers.js
│   ├── k6/               performance tests
│   └── security/         bandit, pip-audit, OWASP ZAP
├── tests/                unit tests (incl. checks that Postman and Swagger cover the endpoint)
└── scripts/              sample data, OpenAPI export, figures
```

## 18. Deployment and operations

**Local development and testing:** follow *Getting started (step by step)* in the repository
`README.md` (install, sample data, run, try a request, troubleshooting). QA follow the
step-by-step *QA guide* in `qa/README.md`.

**Run the API** in Dev/Production (one worker per CPU core; server version header disabled):

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4 --no-server-header
```

**Create tables once, then schedule the loader:**

```bash
uv run python -m ingestion.run init-db
# crontab: every 30 minutes
*/30 * * * * cd /path/to/api && uv run python -m ingestion.run schemes
```

**Operational checks**

- `GET /health` for liveness probes.
- Cron job exit code 1 means at least one file failed; the log names the file and the reason.
- A dropped database connection is retried (5 attempts, 10 s apart); the job resumes from the
  first file not yet loaded, since each file is saved in one transaction.
- Total database connections = workers × `DB_POOL_MAX` (default 40 for 4 workers).

### 18.1 Response cache (Redis)

The dealer app loads data with many parallel requests: about 600 a minute were
planned, up to 1 lakh a minute (≈ 1,700 a second) were seen. The data only changes when the cron
job loads new files, so API responses are cached in Redis, as already done for other dealer APIs on UAT.

- **Switch:** `CACHE_ENABLED=true` and `REDIS_URL` turn it on; either one missing turns it off
  (default off).
- **Key:** API, dealer code (normalised), month and year; empty answers are cached too.
- **Freshness:** every key includes a data version; after loading anything the cron job bumps it,
  so the next request reads the new data. `CACHE_TTL_SECONDS` (900) is the upper bound.
- **Failure:** Redis calls time out after 0.25 s; on an error the API answers from the database
  and does not try Redis again for 30 s. Redis is never required for the API to work.
- **Observability:** `X-Cache: HIT`, `MISS` or `BYPASS` on responses while the cache is on.
- **HTTP caching** stays off (`Cache-Control: no-store`): only the server caches.

Measured on a laptop at 1,700 requests/s for 2 minutes (`qa/k6/rate.js`): without the cache
1,596/s got through, p95 1.4 s; with a warm cache 1,695/s, median 1.3 ms, p95 92 ms; 0 errors in
both (details in section 20).

## 19. Postman collection and Swagger spec

Files in `qa/postman/`: the API 1 collection and its environments. They are generated by
`qa/postman/build_collection.py` (edit requests and tests there, then regenerate); a unit test fails
if an API endpoint has no request. A copy is kept in the QA automation repository
(`tests/api/scheme_api/`) and updated automatically whenever the collection changes.

| File | Purpose |
|---|---|
| `Scheme_API1_Current_Month.postman_collection.json` | API 1: requests, automatic tests, example responses (27 requests, 115 assertions) |
| `Local.postman_environment.json` | Local development settings |
| `Dev.postman_environment.json` | Dev settings (base URL to be filled in) |

The collection has these folders:

| Folder | Requests |
|---|---|
| 1. Health | Health check |
| 2. (the API) | Dealer code as string, as number, with leading zeros; month/year as strings; a month with no data; unknown dealer |
| 3. Negative cases | Missing month, missing year, month 13, year 1999, boolean month; then missing, empty, whitespace, null, special characters, too long, invalid JSON, wrong method, unknown route |
| 4. Security | No key, wrong key, SQL injection, script injection, oversized body, security headers |

**Swagger spec.** `qa/swagger/` holds an OpenAPI 3.1 file, for Swagger Editor/UI or any
OpenAPI tool: `Scheme_API1_Current_Month.openapi.json`.
It has the API's request and `/health`, the Local and Dev servers, the `X-API-Key` scheme, named
request examples (valid ones, and `invalid_…` ones that return 422), example responses, and every
error status (401, 413, 422, 500, 503). It is cut from the app's own spec by
`qa/swagger/build_specs.py`; the examples live in `app/openapi_examples.py`, so `/docs` shows the
same ones. A unit test fails if the spec is stale or an example does not behave as its name says.

Variables: `baseUrl`, `apiKey` (secret), `dealerCode`, `unknownDealerCode`, `authEnabled`,
`sampleData` (`true` locally; `false` skips the checks of sample values). Requests send `month` and
`year`: the current month in India time unless the environment sets `month` and `year`.

How to use:

1. In Postman choose **Import** and select the collection and an environment file.
2. Select the environment. For local testing choose **Scheme API - Local** (`baseUrl`
   `http://localhost:8000`, `apiKey` `local-test-key`, matching the local `.env.local`). For Dev, fill in
   `baseUrl`, `apiKey` and a `dealerCode` with data. Every request inherits the `X-API-Key` header
   from the collection's **Authorization** tab (`{{apiKey}}`), so nothing is set per request.
3. Send any request. Saved examples are under each request's **Examples**.
4. Choose **Run collection** to run every request with its tests.

Command line:

```bash
npx newman run Scheme_API1_Current_Month.postman_collection.json -e Local.postman_environment.json
```

## 20. Testing and quality results

Test run of 15 October 2026 on a local environment (Apple M2, PostgreSQL in Docker, API with
4 workers and API key enabled, 20,000 test dealers / 160,017 rows).

| Suite | Result |
|---|---|
| Unit tests (pytest) | 123 / 123 passed; 13 database tests (`TEST_DATABASE_URL`) passed against local Postgres |
| Postman / newman — API 1 collection | 115 / 115 assertions passed (27 requests) |
| Playwright — API 1 | 65 / 65 passed |
| Playwright — common | 3 passed, 1 skipped (HTTPS redirect check needs the deployed URL) |
| bandit (static analysis) | 0 issues |
| pip-audit (dependencies) | No known vulnerabilities |
| OWASP ZAP API scan | 119 passed, 0 failures, 0 warnings |

Performance (k6):

| Test | Requests | Errors | p95 | p99 | Result |
|---|---|---|---|---|---|
| API 1 load: 150 users, 3 min | 60,229 | 0.00% | 5.5 ms | 8.6 ms | Pass |
| API 1 stress: up to 600 users | 174,316 | 0.00% | 253.9 ms | 374.8 ms | Pass |
| API 1 spike: 20 → 500 users | 68,715 | 0.00% | 150.5 ms | 401.9 ms | Pass |
| API 1 at 1,700 requests/s (1 lakh/min), 2 min, no cache | 192,986 | 0.00% | 1,385 ms | 1,546 ms | Missed latency (1,596/s achieved) |
| API 1 at 1,700 requests/s, Redis cache warm | 203,345 | 0.00% | 92 ms | 303 ms | Pass (1,695/s achieved) |

Issues found by testing and fixed before this release:

| Issue | Found by | Fix |
|---|---|---|
| Database connection pool deadlock under concurrent load (0.35% timeouts) | k6 load test | Connections borrowed per query; bounded pool; 503 when busy |
| Validation errors echoed the caller's input | Security review | Custom error handler without input |
| Missing security headers | Security tests, ZAP | Security headers middleware |
| No request size limit | Security tests | 4 KB body limit |
| SAP negative numbers (`882.00-`) would have been rejected | Real SAP file test | Trailing-minus parsing |
| API could not keep up with 1 lakh requests a minute | k6 `rate.js` | Redis response cache; plain-ASGI headers middleware and async light dependencies (cache-hit ceiling 1,705/s → 2,922/s) |
| Loading a month took hours over the VPN (~30 s per file) | Live load of September | Parallel downloads + one bulk `COPY` per file (~6 s per file); one run at a time; `--limit` |

Performance figures come from a laptop and show relative behaviour; they should be re-measured on
the deployed environment.

# Appendices

## A. Example API calls

**Python**

```python
import requests

response = requests.post(
    "https://<host>/v1/schemes/current-month",
    json={"dealer_code": "6100000001", "month": 10, "year": 2026},
    headers={"X-API-Key": "<your key>"},
    timeout=15,
)
response.raise_for_status()
for row in response.json():
    print(row["ComponentDescription"], row["Qualification"], row["GsrB"])
```

**JavaScript (Node 18+)**

```javascript
const response = await fetch('https://<host>/v1/schemes/current-month', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', 'X-API-Key': '<your key>' },
  body: JSON.stringify({ dealer_code: '6100000001', month: 10, year: 2026 }),
});
if (!response.ok) throw new Error(`HTTP ${response.status}`);
const rows = await response.json();
```

**Error example — invalid dealer code**

```bash
curl -X POST "https://<host>/v1/schemes/current-month" \
  -H "Content-Type: application/json" -H "X-API-Key: <your key>" \
  -d '{"dealer_code": "6100-0001", "month": 10, "year": 2026}'
```

```json
{"detail": [{"loc": ["body", "dealer_code"], "msg": "String should match pattern '^[A-Za-z0-9]+$'", "type": "string_pattern_mismatch"}]}
```

## B. Glossary

| Term | Meaning |
|---|---|
| SAP | Acme's ERP system where schemes are calculated |
| GCS | Google Cloud Storage, where SAP exports are stored |
| EB | Early Bird scheme, paid in slabs within the month (SLAB 1–3) and for the full month |
| GSR | Gross Sales Revenue |
| Component | One scheme line for a dealer (SAP "Comp desc") |
| Agreement / Deal Suffix | SAP identifiers of a dealer's scheme agreement and its component line |
| Upsert | Insert a row, or update it if it already exists |
| VU | Virtual user in a k6 load test |
| p95 / p99 | Response time that 95% / 99% of requests are faster than |
| Cron | Scheduler that runs the loading job at fixed intervals |
