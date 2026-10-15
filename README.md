# Scheme API

Scheme data for the client team.

SAP scheme files land in GCS every day (one folder per month). A cron job loads them into
Postgres, and the API reads from Postgres.

```
GCS (daily SAP scheme files)
   │   cron: python -m ingestion.run schemes
   ▼
Postgres ──► FastAPI ──► the client
```

| API | Endpoint | Body | Returns |
|---|---|---|---|
| 1 | `POST /v1/schemes/current-month` | `{"dealer_code": "6100000001", "month": 10, "year": 2026}` | The dealer's rows for the 8 template components in that month, in template order |

API 1 returns a JSON list (empty when there's no data). Interactive docs: `/docs`.
Requirements & solution design: [docs/Scheme_API_Requirements_and_Solution.md](docs/Scheme_API_Requirements_and_Solution.md) (Word version alongside).
Integration guide: [docs/API_GUIDE.md](docs/API_GUIDE.md). Postman collection, Swagger spec and QA suite: [qa/](qa/README.md).

API 1 returns the columns of the API template, with PascalCase keys like the other dealer APIs:

```json
{"Customer": "6100000001", "FromDate": "2026-10-01", "ToDate": "2026-10-18",
 "ComponentDescription": "EB SLAB 2", "Qualification": 405232.86, "GsrB": 405232.86,
 "RebateRate": 0.75, "ClearCut": 0.0, "ShortfallAmount": 0.0, "ShortfallPct": 0.0}
```

## Getting started (step by step)

New to the project? Follow these steps in order. They run everything on your own machine with
made-up sample data, so you don't need GCS access or the real database.

### Step 0 — Install the tools (once)

| Tool | Why | Install on a Mac |
|---|---|---|
| Git | Download the code | `xcode-select --install` |
| Docker Desktop | Runs the local database | Download from docker.com, open it once and sign in |
| uv | Installs Python and the project's packages | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |

Check they work: `git --version`, `docker --version`, `uv --version` should each print a version.

### Step 1 — Get the code

```bash
git clone https://github.com/<your-github-user>/scheme-api.git
cd scheme-api
```

All the commands below are run from this folder.

### Step 2 — Create your settings file

```bash
cp .env.example .env.local
```

`.env.local` holds your local settings and is never committed (the app reads it because
`APP_ENV` defaults to `local`). The defaults are ready for local use:
the API reads files from the `local_data/` folder instead of GCS, and requires the API key
`local-test-key` in an `X-API-Key` header.

### Step 3 — Start the database

Make sure Docker Desktop is running (whale icon in the menu bar), then:

```bash
docker compose up -d
docker compose ps        # should show "api-db-1 ... Up"
```

This starts PostgreSQL on port 5433 and creates the tables.

### Step 4 — Install the Python packages

```bash
uv sync
```

The first run downloads Python and the packages into `.venv/` (a minute or two).

### Step 5 — Create sample data and load it

```bash
uv run python scripts/make_sample_data.py        # writes made-up SAP files into local_data/
uv run python -m ingestion.run schemes           # loads them into the database
```

You should see `Done: {... 'files_loaded': 2, 'rows_loaded': 17}`. This is the same job that runs on a
schedule (cron) in production.

### Step 6 — Start the API

```bash
uv run uvicorn app.main:app --reload
```

Leave this terminal open; it shows each request. `--reload` restarts the API when you save a file.

### Step 7 — Try a request

Open **http://localhost:8000/docs** in your browser:

1. Click **Authorize**, enter `local-test-key`, click **Authorize** → **Close**.
2. Open **POST /v1/schemes/current-month** → **Try it out**.
3. Use the body `{"dealer_code": "6100000001", "month": 10, "year": 2026}` (use the current month
   and year: the sample data is written for the current month) → **Execute**.
4. You should get `200` and 8 rows (EB SLAB 1, 2, 3, VALUE, LUXURY, PREMIUM 01, 02, EB FULL MONTH).

Or from a second terminal:

```bash
curl -X POST http://localhost:8000/v1/schemes/current-month \
  -H "Content-Type: application/json" -H "X-API-Key: local-test-key" \
  -d '{"dealer_code": "6100000001", "month": 10, "year": 2026}'
```

For Postman, see [qa/README.md](qa/README.md).

### Step 8 — Run the tests

```bash
uv run pytest
```

All tests should pass. They don't need the database or the API to be running.

### Step 9 — Stop everything

Press `Ctrl+C` in the API terminal, then `docker compose stop`. Next time, start again from Step 3
(skip Step 5 unless you want fresh sample data).

### Troubleshooting

| Problem | Fix |
|---|---|
| `Cannot connect to the Docker daemon` | Open Docker Desktop and wait until it says it's running |
| `port is already allocated` (5433 or 8000) | Something else uses the port: stop it, or change the port in `docker-compose.yml` / `--port` |
| `401 Invalid or missing API key` | Send the header `X-API-Key: local-test-key` (or Authorize in `/docs`) |
| API returns `[]` | No data for that dealer in that month (the sample data is for the current month): re-run Step 5, use dealer `6100000001` |
| `422` from API 1 | `month` (1–12) and `year` (2000–2100) are required along with `dealer_code` |
| `uv: command not found` | Close and reopen the terminal after installing uv |
| `connection refused` on 5433 | The database isn't running: Step 3 |

### Making a change

1. Edit the code (`app/` for the API, `ingestion/` for the loading job).
2. Run `uv run pytest`; add or update tests for your change in `tests/`.
3. If you changed an endpoint, update the Postman collection in `qa/postman/build_collection.py`
   and run `python3 qa/postman/build_collection.py` (a test fails if an endpoint is missing
   there). Update the Playwright file in `qa/api-tests/tests/` too, and run
   `uv run python qa/swagger/build_specs.py` to refresh the Swagger spec.
4. Commit and push: `git add -A && git commit -m "What changed" && git push`.

## Business rules

### API 1 — SAP scheme files

Input: SAP exports `sap-exports_YYYY_MM_main_YYYYMMDD_HHMMSS_NNN.csv` (one header row,
up to 50,000 rows per file, several files a day).

- Only 8 components (SAP "Comp desc") are kept, matched exactly: EB SLAB 1, EB SLAB 2,
  EB SLAB 3, VALUE, LUXURY, PREMIUM 01, PREMIUM 02, EB FULL MONTH. Look-alikes such as
  `ALWD EB2` or `ADDTNL PREMIUM 01` are dropped. The list is in `app/constants.py`.
- Each daily file contains only the rows SAP updated that day. A row is identified by
  Customer + Agreement + Deal Suffix, and the version with the newest
  "Last Updated Date/Time" wins, whatever order files are loaded in.
- Qualification = `GSR (Q)` for EB SLAB 1–3 and Value, `Invoiced Quantity(Q)` for Luxury and Premium.
  EB FULL MONTH follows the EB slab working: SAP leaves its `GSR (Q)` empty (on slab rows `GSR (Q)`
  always equals `GSR (B)`), so its Qualification is `GSR (B)`.
- After a rule change, recalculate a month that is already loaded with
  `uv run python -m ingestion.run schemes --year <Y> --month <M> --reload`.
- Column mapping: From/To date = `Valid from`/`To`, GSR B = `GSR (B)`, Rebate rate = `Rebate Rate`,
  Clear cut = `Clear Cut CN`, Shortfall amount/% = `Short Fall`/`Short Fall %`.
- Dates are DD/MM/YYYY; SAP negatives like `882.00-` become -882.
- A row's month is the month folder its file came from.
- Dealer codes are compared without leading zeros (`0006100000001` = `6100000001`).
- EB amounts are running totals from the 1st: EB SLAB 1 (1–10 Oct, 10 lakh) is included in EB SLAB 2
  (1–15 Oct, 12 lakh), so 2 lakh were added, not 22. Each row is returned as SAP sends it and rows are
  never added together; a newer update of the same row replaces it.
- **Month:** the caller sends `month` and `year` with the dealer code; API 1 returns that month's
  rows. The database keeps every month
  (year + month are part of the key). The cron job loads the current month (India time); load an
  earlier month once with `uv run python -m ingestion.run schemes --year <Y> --month <M>`.

## Run against GCS and the real database

In the environment's settings file (see *Environments*): empty `LOCAL_DATA_DIR`, set `GCS_BUCKET`, `GOOGLE_APPLICATION_CREDENTIALS`,
`DATABASE_URL`, and the scheme prefix (`SCHEME_PREFIX_TEMPLATE`). On a laptop,
`gcloud auth application-default login` can replace the key file; `GCS_PROJECT` is optional.
The Dev database is reachable only over the team VPN. Then create the
tables once (also run it after pulling changes to `sql/001_init.sql`; it upgrades in place):

```bash
uv run python -m ingestion.run init-db
```

Run the API (hide the server header; one worker per CPU core):

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4 --no-server-header
```

Each worker keeps up to `DB_POOL_MAX` (default 10) database connections. Requests over 4 KB get
413, invalid ones 422, and if the database pool is exhausted the API answers 503 with `Retry-After`.

Cron (e.g. every 30 minutes):

```
*/30 * * * * cd /path/to/api && uv run python -m ingestion.run schemes
```

The cron job only keeps the database in sync with GCS; clients are always served from the
database. Each `schemes` run:

1. Lists the files in this month's GCS folder, e.g. `gs://acme-scheme-files/sap-exports/2026/10/`
   (next month `.../2026/11/`). The month comes from today's date in India time.
2. Skips files it has already loaded (tracked in `ingested_files` by path and GCS version).
3. Upserts the rows of new files: a row (Customer + Agreement + Deal Suffix) is inserted once and
   only replaced by a newer "Last Updated" version, so re-runs and overlapping files never create
   duplicates.

How it loads quickly (a month has ~530 files of ~15 MB):

- The next `LOADER_WORKERS` files (default 4) are downloaded and parsed in parallel while the
  current one is saved. Files are still saved one at a time, in order, each in one transaction.
- A file's rows go to the database in one bulk `COPY` and one upsert, not one statement per row.
- Measured on real September files from a laptop: about 6 s per file (download-bound, ~3 MB/s),
  against about 30 s before; saving takes 0.3–0.5 s per file. More workers don't help on a
  connection that is already full.
- Only one run at a time: a run that finds another still going (e.g. a long backfill when the next
  cron run starts) logs a warning and stops.
- `--limit N` loads only the first N new files (for testing); each file's log line shows its read
  and save time.

The job logs a summary (`files_loaded`, `rows_loaded`, `rows_other_components`, `rows_bad`,
`files_older`, …) and exits with code 1 if any file failed.

## Environments (local, dev, uat, prod)

The code is the same everywhere; only the settings differ. Each environment has its own settings
file, chosen by `APP_ENV`; `.env.example` is the one template for all of them (it notes what
changes per environment). Environment variables always win over the file.

| Environment | `APP_ENV` | Settings file | Differences |
|---|---|---|---|
| Local | `local` (default) | `.env.local` | Sample files from `local_data/`, local Postgres and Redis in Docker, `local-test-key` |
| Dev | `dev` | `.env.dev` | The teammate's shared `app_db` (team VPN only); cache off; `/docs` on |
| UAT | `uat` | `.env.uat` | UAT database and Redis (from DevOps); cache on; `/docs` on |
| Prod | `prod` | `.env.prod` | Production database and Redis; cache on; `/docs` off |

The four files hold secrets, so git ignores them (only `.env.example` is committed). Branches
`dev`, `uat` and `prod` exist (created from `main`) in case an environment needs to run a different
version later; today they match `main`.

For DevOps, per environment:

1. Create `.env.<env>` from `.env.example` (or load the values into the platform's secret manager /
   environment variables), set `APP_ENV=<env>` for the API and the cron job, and fill in the values
   marked `[dev/uat/prod]`.
2. `API_KEY`: a separate long random secret per environment
   (`python3 -c "import secrets; print(secrets.token_urlsafe(32))"`). Give it to the API callers
   through a secure channel; it must never be committed.
3. `GOOGLE_APPLICATION_CREDENTIALS`: path to the GCS service-account key mounted on the server
   (or run as a service account that can read the bucket).
4. `DOCS_ENABLED`: `true` for dev and UAT, `false` for prod (the template defaults to `true`, so
   set it explicitly there). Swagger UI is served by the API itself at `/docs`; after deploying,
   `/docs` should return 200 on dev/UAT and `/docs` and `/openapi.json` 404 on prod. The page loads
   its scripts from `cdn.jsdelivr.net`.
5. Give the cron job the same settings as the API (database, GCS and, for the cache,
   `CACHE_ENABLED` and `REDIS_URL`).
6. Create the tables once: `APP_ENV=<env> uv run python -m ingestion.run init-db`.

## Response cache (Redis)

The dealer app can send far more requests than planned (up to 1 lakh a minute), while
the data only changes when the cron job loads new files. So API responses can be cached in Redis:

| Setting | Default | Meaning |
|---|---|---|
| `CACHE_ENABLED` | `false` | Turns the cache on or off |
| `REDIS_URL` | — | e.g. `redis://localhost:6379/0`; the cache is on only when this is set **and** `CACHE_ENABLED=true` |
| `CACHE_TTL_SECONDS` | `900` | Longest a cached response lives |

- Each response is cached by dealer, month and year, including empty answers.
- New data is never hidden: after loading anything, the cron job bumps a data version in Redis, so
  the next request reads the database again. (The cron job needs the same `CACHE_ENABLED` and
  `REDIS_URL`.)
- If Redis is down or slow (over 0.25 s), requests are answered from the database as usual and
  Redis is not tried again for 30 s. Responses carry `X-Cache: HIT`, `MISS` or `BYPASS` while the
  cache is on.
- Local Redis: `docker compose up -d redis`, then set `CACHE_ENABLED=true` and
  `REDIS_URL=redis://localhost:6379/0` in `.env.local` and restart the API.

Measured on a laptop at 1,700 requests/s (1 lakh a minute) for 2 minutes, `qa/k6/rate.js`: without
the cache 1,596/s got through (p95 1.4 s); with a warm cache 1,695/s (median 1.3 ms, p95 92 ms),
0 errors either way. See `qa/TEST_RESULTS.md`.

## Project layout

```
app/          FastAPI app: routes, models, SQL queries, API key, security middleware
ingestion/    cron job: read files from GCS, apply scheme rules, load into Postgres
sql/          table definitions
scripts/      sample-data generator, OpenAPI export
tests/        unit tests (no database needed)
docs/         API guide for the client and QA, OpenAPI spec
qa/           Postman collection, Swagger spec and Playwright tests, k6, security scans
```

## Open questions

- [x] GCS path for the SAP exports: `acme-scheme-files/sap-exports/<year>/<month>/` (e.g. `.../2026/10`)
- [ ] GCS access (service-account key)
- [ ] Month folders for January–September: `01` or `1`? (the job tries `01`, then `1`)
- [ ] The API template labels its date columns "To Date" then "From Date", but the values are
      from-date then to-date. The API returns `FromDate` = `Valid from` and `ToDate` = `To`.
- [x] API 1: the client sends the month and year in the request (built)
- [ ] Backfill: which earlier months must be loaded before go-live?
- [ ] Auth: the client calls with no auth today. Set `API_KEY` to require an `X-API-Key` header.
