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
| 1 | `POST /v1/schemes/current-month` | `{"dealer_code": "6100000001", "month": 10, "year": 2026}` | The dealer's rows for the 10 template components in that month, in template order |

API 1 returns a JSON list (empty when there's no data). Every call needs `Authorization: Bearer <token>`,
with a 1-hour token from `POST /v1/auth/token` (OAuth2 client credentials; see *Authentication*).
Interactive docs: `/docs`.
Requirements & solution design: [docs/Scheme_API_Requirements_and_Solution.md](docs/Scheme_API_Requirements_and_Solution.md) (Word version alongside).
Integration guide: [docs/API_GUIDE.md](docs/API_GUIDE.md). Postman collection, Swagger spec and QA suite: [qa/](qa/README.md).

API 1 returns the columns of the API template plus `NextSlabRate`, with PascalCase keys like the other dealer APIs:

```json
{"Customer": "6100000001", "FromDate": "2026-10-01", "ToDate": "2026-10-18",
 "ComponentDescription": "EB SLAB 2", "Qualification": 405232.86, "GsrB": 405232.86,
 "RebateRate": 0.75, "ClearCut": 0.0, "ShortfallAmount": 0.0, "ShortfallPct": 0.0,
 "NextSlabRate": 1.0}
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

`.env.local` holds your local settings and is never committed (the app reads it when `APP_ENV`
is `local`, the default). The defaults are ready for local use:
the API reads files from the `local_data/` folder instead of GCS, and requires a Bearer token
from the local client `local-client` / secret `local-test-secret` (Step 7 shows how to get one).
To run locally without auth, delete `AUTH_CLIENTS` and `JWT_SECRET` from `.env.local` and start the
API with `APP_ENV=local` set explicitly, e.g. `APP_ENV=local uv run python -m app --reload`
(the API logs a warning). With `APP_ENV` unset the API refuses to start with no auth settings, and
also with the `.env.example` client and key, so a server that forgets `APP_ENV` never runs open or
on credentials anyone can read. Locally, always start it with `APP_ENV=local`.

### Step 3 — Start the database

Make sure Docker Desktop is running (whale icon in the menu bar), then, from the repo folder:

```bash
docker run -d --name acme-db -p 5433:5432 \
  -e POSTGRES_USER=acme -e POSTGRES_PASSWORD=acme -e POSTGRES_DB=acme \
  -v "$PWD/sql":/docker-entrypoint-initdb.d:ro -v acme-pgdata:/var/lib/postgresql/data \
  postgres:16
docker ps --filter name=acme-db        # should show "Up"
```

This starts PostgreSQL on port 5433 and creates the tables. Next time: `docker start acme-db`.
The repo has no Docker files (DevOps owns the deployment images); a local `docker-compose.yml` is
gitignored if you prefer Compose.

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
APP_ENV=local uv run python -m app --reload
```

Leave this terminal open; it shows each request. `--reload` restarts the API when you save a file.
`python -m app` runs uvicorn with its `X-Forwarded-For` handling off (see "Rate limit per IP" below);
`APP_ENV=local` lets the API use the example client from `.env.example`.

### Step 7 — Try a request

Open **http://localhost:8000/docs** in your browser:

1. Click **Authorize**, enter client_id `local-client` and client_secret `local-test-secret`, tick
   the `schemes:read` scope, click **Authorize** → **Close**. Swagger UI fetches the token for you.
2. Open **POST /v1/schemes/current-month** → **Try it out**.
3. Use the body `{"dealer_code": "6100000001", "month": 10, "year": 2026}` (use the current month
   and year: the sample data is written for the current month) → **Execute**.
4. You should get `200` and 10 rows (EB SLAB 1, 2, 3, VALUE, VALUE %, VALUE FA, LUXURY, PREMIUM 01, 02, EB FULL MONTH).

Or from a second terminal:

```bash
# 1. Get a token (valid 1 hour; reuse it)
TOKEN=$(curl -s -u local-client:local-test-secret -d grant_type=client_credentials \
  http://localhost:8000/v1/auth/token | jq -r .access_token)

# 2. Call the API with it
curl -X POST http://localhost:8000/v1/schemes/current-month \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"dealer_code": "6100000001", "month": 10, "year": 2026}'
```

(`jq` comes with `brew install jq`; without it, copy `access_token` from the first response.)

For Postman, see [qa/README.md](qa/README.md).

### Step 8 — Run the tests

```bash
uv run pytest
```

All tests should pass. They don't need the database or the API to be running.

### Step 9 — Stop everything

Press `Ctrl+C` in the API terminal, then `docker stop acme-db` (and `acme-redis` if you started it).
Next time, `docker start acme-db` and continue from Step 4
(skip Step 5 unless you want fresh sample data).

### Troubleshooting

| Problem | Fix |
|---|---|
| `Cannot connect to the Docker daemon` | Open Docker Desktop and wait until it says it's running |
| `port is already allocated` (5433 or 8000) | Something else uses the port: stop it, or change the `-p` port in Step 3 / `--port` |
| `401 Not authenticated` | Send `Authorization: Bearer <token>` (Step 7), or Authorize in `/docs` |
| `401 Invalid token` / `401 Token expired` | Get a new token from `/v1/auth/token` (tokens last 1 hour; restarting with a new `JWT_SECRET` or removing the client also invalidates them) |
| `401 invalid_client` from `/v1/auth/token` | Wrong client_id or secret: locally `local-client` / `local-test-secret` |
| API won't start: `APP_ENV is not set and there are no auth settings` | Locally without auth, start with `APP_ENV=local uv run python -m app`; otherwise set `AUTH_CLIENTS` and `JWT_SECRET` |
| API won't start: `Auth settings invalid for APP_ENV=...` | Fix the listed `AUTH_CLIENTS` / `JWT_SECRET` problems (outside `local` the example values from `.env.example` are refused). `APP_ENV=(not set)`: locally, start with `APP_ENV=local` |
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

- Only 10 components (SAP "Comp desc") are kept, matched exactly: EB SLAB 1, EB SLAB 2,
  EB SLAB 3, VALUE, VALUE %, VALUE FA, LUXURY, PREMIUM 01, PREMIUM 02, EB FULL MONTH. Look-alikes
  such as `ALWD EB2`, `ALWD VALUE SCH` or `ADDTNL PREMIUM 01` are dropped. The list is in `app/constants.py`.
- Each daily file contains only the rows SAP updated that day. A row is identified by
  Customer + Agreement + Deal Suffix + validity period (Valid from, To) within a month folder, and
  the version with the newest "Last Updated Date/Time" wins, whatever order files are loaded in.
  The period is part of the key because one month's folder can hold the same agreement and suffix
  for two periods (late SAP updates of last month's scheme next to this month's); both are kept.
- Qualification = `GSR (Q)` for EB SLAB 1–3 and Value (VALUE, VALUE %, VALUE FA), `Invoiced Quantity(Q)` for Luxury and Premium.
  EB FULL MONTH follows the EB slab working: SAP leaves its `GSR (Q)` empty (on slab rows `GSR (Q)`
  always equals `GSR (B)`), so its Qualification is `GSR (B)`.
- After a rule change, recalculate a month that is already loaded with
  `uv run python -m ingestion.run schemes --year <Y> --month <M> --reload`.
- Column mapping: From/To date = `Valid from`/`To`, GSR B = `GSR (B)`, Rebate rate = `Rebate Rate`,
  Clear cut = `Clear Cut CN`, Shortfall amount/% = `Short Fall`/`Short Fall %`, Next slab rate = `Next Slab Rate`
  (required: a file without it is rejected like any other missing column).
- Dates are DD/MM/YYYY; SAP negatives like `882.00-` become -882.
- A row belongs to every month its validity period (From–To) overlaps, not to its file's month folder
  (the September folder also holds late updates of August schemes). The newest update of an agreement
  wins; rows without dates use their folder's month.
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

Run the API (one worker per CPU core; `python -m app` hides the server header and turns off
uvicorn's own `X-Forwarded-For` handling):

```bash
APP_ENV=<env> uv run python -m app --host 0.0.0.0 --port 8000 --workers 4
```

Set `TRUSTED_PROXIES` to the nginx / load balancer address(es) in front of it (below). If the platform
must call uvicorn directly, use `uvicorn app.main:app ... --no-server-header --no-proxy-headers`.

Each worker keeps up to `DB_POOL_MAX` (default 10) database connections. Requests over 4 KB get
413, invalid ones 422, and if the database pool is exhausted the API answers 503 with `Retry-After`.

**Rate limit per IP:** more than `RATE_LIMIT_PER_SECOND` (default 2,500) requests from one client
IP in a second get `429 Too many requests` with `Retry-After: 1`; `/health` is never limited. 2,500/s
(1.5 lakh a minute) is above the busiest load seen (1 lakh a minute), even if it all comes from one
The client server, and k6 at 1,700/s showed no 429s and no slowdown. Counts are kept in Redis when
`REDIS_URL` is set (exact across workers and servers, independent of `CACHE_ENABLED`); without Redis,
or while it is down, each worker counts on its own. `RATE_LIMIT_ENABLED=false` turns it off.
The client IP is the connection's address. Behind nginx or a load balancer, set `TRUSTED_PROXIES`
to its address(es) (IPs or CIDRs, comma-separated, e.g. `10.0.0.5` or `172.18.0.0/16` for a Docker
network): `X-Forwarded-For` is then read right to left and the first address that is not a trusted
proxy is the caller, so an entry the caller wrote itself is never used. Without `TRUSTED_PROXIES`,
`X-Forwarded-For` is ignored and every caller behind the proxy shares one count. nginx must append
the address it received from: `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;`.
Start the API with `python -m app` (or uvicorn with `--no-proxy-headers`): uvicorn's own handling,
on by default, trusts 127.0.0.1 and would let anything on the same machine choose its IP. `POST /v1/auth/token` has its
own, lower limit on top: `TOKEN_RATE_LIMIT_PER_MINUTE` (default 30) per IP per minute.

**Authentication (OAuth2 client credentials + JWT, `app/security.py`):** a caller sends its
`client_id` and `client_secret` to `POST /v1/auth/token` (`grant_type=client_credentials`, form-encoded
or JSON, or the credentials as HTTP Basic) and gets a Bearer token valid for `JWT_TTL_SECONDS` (1 hour).
It sends `Authorization: Bearer <token>` on every `/v1/schemes` call and gets a new token shortly
before expiry or after a `401 Token expired`. Checking a token costs about 49 µs and needs no database
or Redis call. `/health` needs no token.

| Setting | Meaning |
|---|---|
| `AUTH_CLIENTS` | JSON `{"<client_id>": "<SHA-256 hex of its secret>"}`; only hashes are stored. Removing a client revokes its tokens at once |
| `JWT_SECRET` | HS256 signing key, 32+ characters |
| `JWT_PREVIOUS_SECRET` | The old key while rotating (optional) |
| `JWT_TTL_SECONDS` | Token lifetime, default `3600` |
| `TOKEN_RATE_LIMIT_PER_MINUTE` | Token requests per IP per minute, default `30` |

```bash
uv run python -m app.security new-client partner     # prints client_id, client_secret and the AUTH_CLIENTS entry
uv run python -m app.security new-jwt-secret     # prints a JWT_SECRET
```

Rotating `JWT_SECRET` without downtime: move the current value to `JWT_PREVIOUS_SECRET`, set a new
`JWT_SECRET`, restart, and remove `JWT_PREVIOUS_SECRET` after one `JWT_TTL_SECONDS`.
Outside `APP_ENV=local` the API refuses to start unless `AUTH_CLIENTS` and `JWT_SECRET` are set and
valid, and it refuses the local example values from `.env.example`, also when `APP_ENV` is not set.

Cron (e.g. every 30 minutes):

```
*/30 * * * * cd /path/to/api && uv run python -m ingestion.run schemes
```

The cron job only keeps the database in sync with GCS; clients are always served from the
database. Each `schemes` run:

1. Lists the files in this month's GCS folder, e.g. `gs://acme-scheme-files/sap-exports/2026/10/`
   (next month `.../2026/11/`). The month comes from today's date in India time.
2. Skips files it has already loaded (tracked in `ingested_files` by path and GCS version).
3. Upserts the rows of new files: a row (Customer + Agreement + Deal Suffix + validity period) is
   inserted once and only replaced by a newer "Last Updated" version, so re-runs and overlapping
   files never create duplicates.
4. Rows that can't be read (e.g. a bad date or number) are skipped and logged. Such a file is read
   once more; if rows are still bad, its good rows are loaded and the file is recorded as
   `failed` in `ingested_files` (`status`, `rows_bad`). A failed file is not retried until SAP
   re-uploads it, or the month is loaded with `--reload`. To list them:
   `SELECT gcs_path, rows_bad FROM ingested_files WHERE status = 'failed';`

Upgrading a database loaded before the period joined the key: the next run (or `init-db`) changes
the key by itself, but rows already written over by another period of the same agreement only come
back by reloading those months: `python -m ingestion.run schemes --year 2026 --month 9 --reload`
(each month that was loaded).

How it loads quickly (a month has ~530 files of ~15 MB):

- The next `LOADER_WORKERS` files (default 4) are downloaded and parsed in parallel while the
  current one is saved. Files are still saved one at a time, in order, each in one transaction.
- A file's rows go to the database in one bulk `COPY` and one upsert, not one statement per row.
- Measured on SAP export files from a laptop: about 6 s per file (download-bound, ~3 MB/s),
  against about 30 s before; saving takes 0.3–0.5 s per file. More workers don't help on a
  connection that is already full.
- Memory: each file is held in memory while it is parsed and saved, together with the next
  `LOADER_WORKERS` files. A real file (~5,500 rows) peaks at about 40 MB; a 50,000-row file at about
  360 MB, so up to ~1.8 GB with the default 4 workers. Lower `LOADER_WORKERS` if files grow that large.
- Both month folders are read (`…/2026/01/` and `…/2026/1/`), so neither is missed if both exist.
- A file that can't be read at all (not UTF-8, missing columns, not a workbook) is read once more and
  then recorded as failed with no rows (`ingested_files.status = 'failed'`, `row_count = 0`); like a
  file with bad rows, it is not retried until SAP re-uploads it or the month is reloaded. A failed
  download is not recorded, so the next run tries again. Numbers such as `NaN` or `Infinity` make the
  row bad instead of being stored.
- `TIMEZONE` (default `Asia/Kolkata`) decides the current month when `--year`/`--month` are not given.
- Only one run at a time: a run that finds another still going (e.g. a long backfill when the next
  cron run starts) logs a warning and stops.
- `--limit N` loads only the first N new files (for testing); each file's log line shows its read
  and save time.

The job logs a summary (`files_loaded`, `rows_loaded`, `rows_other_components`, `rows_bad`,
`files_older`, …) and exits with code 1 if any file failed, including files marked `failed` for
bad rows (their good rows are still loaded).

## Environments (local, dev, uat, prod)

The code is the same everywhere; only the settings differ. Each environment has its own settings
file, chosen by `APP_ENV`; `.env.example` is the one template for all of them (it notes what
changes per environment). Environment variables always win over the file.

| Environment | `APP_ENV` | Settings file | Differences |
|---|---|---|---|
| Local | `local` (default) | `.env.local` | Sample files from `local_data/`, local Postgres and Redis in Docker, client `local-client` / `local-test-secret` |
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
2. Auth (see *Authentication* above), separately per environment, never reused:
   - one client per caller: `uv run python -m app.security new-client partner` (and e.g. `qa`). Give
     the printed `client_id` and `client_secret` to that caller through a secure channel; put the
     printed hash entries together in `AUTH_CLIENTS`, e.g. `{"partner": "<hash>", "qa": "<hash>"}`;
   - `JWT_SECRET`: `uv run python -m app.security new-jwt-secret`.

   Existing `.env.dev`, `.env.uat` and `.env.prod` must get `AUTH_CLIENTS` and `JWT_SECRET` before
   the next deploy, or the API will not start. Their `API_KEY` lines are no longer used and can be
   removed. Secrets must never be committed.
3. `GOOGLE_APPLICATION_CREDENTIALS`: path to the GCS service-account key mounted on the server
   (or run as a service account that can read the bucket).
4. `DOCS_ENABLED`: `true` for dev and UAT, `false` for prod (the template defaults to `true`, so
   set it explicitly there). Swagger UI is served by the API itself at `/docs`; after deploying,
   `/docs` should return 200 on dev/UAT and `/docs` and `/openapi.json` 404 on prod. The page loads
   one pinned Swagger UI release (`app/docs.py`) from `cdn.jsdelivr.net` with integrity hashes, so the
   browser refuses the files if the CDN ever serves different ones.
5. Give the cron job the same settings as the API (database, GCS and, for the cache,
   `CACHE_ENABLED` and `REDIS_URL`).
6. Create the tables once: `APP_ENV=<env> uv run python -m ingestion.run init-db`. Each loader run
   also adds tables or indexes from newer releases (e.g. API 1's dealer index), so no extra step after
   an upgrade, but run `init-db` once right after deploying one so the API is fast before the next load.
7. Rate limit: start the API with `python -m app` (above), set `TRUSTED_PROXIES` to the nginx / load
   balancer address(es) and make nginx append `X-Forwarded-For` (`$proxy_add_x_forwarded_for`); set
   `RATE_LIMIT_PER_SECOND` only to change the 2,500 default. **Changed 2026-10-16:** the old
   `--proxy-headers --forwarded-allow-ips` start command no longer decides the client IP.
8. Upgrading to the release that adds `NextSlabRate` (2026-10-15): run `init-db` **before** starting the
   new API. It adds the `next_slab_rate` column and fills it for rows already loaded (from each
   row's stored SAP data); the new API fails with 500 until the column exists. Cached responses
   use a new key prefix, so no old body without `NextSlabRate` is served.

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
- Local Redis: `docker run -d --name acme-redis -p 6379:6379 redis:7-alpine redis-server --save "" --appendonly no --maxmemory 256mb --maxmemory-policy allkeys-lru`,
  then set `CACHE_ENABLED=true` and
  `REDIS_URL=redis://localhost:6379/0` in `.env.local` and restart the API.

Measured on a laptop at 1,700 requests/s (1 lakh a minute) for 2 minutes, `qa/k6/rate.js`: without
the cache 1,596/s got through (p95 1.4 s); with a warm cache 1,695/s (median 1.3 ms, p95 92 ms),
0 errors either way. See `qa/TEST_RESULTS.md`.

## Project layout

```
app/          FastAPI app: routes, models, SQL queries, OAuth2/JWT auth (security.py), security middleware
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
- [x] Auth: OAuth2 client credentials + JWT Bearer tokens (1 hour); required outside `local`, the API won't start without it.
