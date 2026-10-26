import logging
import os
from contextlib import asynccontextmanager

import psycopg
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg_pool import PoolTimeout

from app import cache, db
from app.config import APP_ENV, get_settings
from app.docs import docs_router
from app.logfile import log_to_file
from app.middleware import (
    SECURITY_HEADERS, BodySizeLimitMiddleware, LenientJsonNumbersMiddleware, SecurityHeadersMiddleware,
)
from app.openapi_examples import HEALTH_RESPONSES
from app.ratelimit import RateLimitMiddleware
from app.repository import Repository, get_repository
from app.routes import router
from app.scheduler import LoaderScheduler, log_schedule_off
from app.security import TOKEN_URL, auth_router, config_problems

log = logging.getLogger("app")
# /health gives up on the database after this long, inside the usual 5 s probe timeout.
HEALTH_DB_TIMEOUT_SECONDS = 2.0


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.open_pool()
    cache.open_cache(settings.cache_enabled, settings.redis_url, settings.cache_ttl_seconds)
    scheduler = LoaderScheduler(settings) if settings.loader_schedule_on else None
    if scheduler:
        scheduler.start()
    else:
        log_schedule_off(settings)
    yield
    if scheduler:
        await scheduler.stop()
    cache.close_cache()
    db.close_pool()


settings = get_settings()
log_to_file(settings)
# Auth is off only on a developer's machine (APP_ENV=local set explicitly) with no auth settings; a
# deploy that forgets APP_ENV refuses to start rather than run open. Anywhere else, or once any auth
# setting is given, it must be complete and strong or the API refuses to start. The published
# .env.example client and key are accepted only with APP_ENV=local set explicitly, so a server that
# forgets APP_ENV cannot run on credentials anyone can read.
EXPLICIT_APP_ENV = os.environ.get("APP_ENV")
if EXPLICIT_APP_ENV is None and not (settings.auth_clients or settings.jwt_secret):
    raise RuntimeError("APP_ENV is not set and there are no auth settings: set AUTH_CLIENTS and JWT_SECRET, "
                       "or APP_ENV=local to run without auth on your machine")
if APP_ENV != "local" or settings.auth_clients or settings.jwt_secret:
    if problems := config_problems(settings, EXPLICIT_APP_ENV or "(not set)"):
        raise RuntimeError(f"Auth settings invalid for APP_ENV={EXPLICIT_APP_ENV or '(not set)'}: "
                           + "; ".join(problems) + ("" if EXPLICIT_APP_ENV else
                           " (set APP_ENV=local to use the .env.example values on your machine)"))
else:
    log.warning("Auth is OFF (APP_ENV=local with no AUTH_CLIENTS/JWT_SECRET)")
app = FastAPI(
    title="Scheme API",
    version="0.1.0",
    description="Scheme data (from SAP files in GCS) for the client team.",
    lifespan=lifespan,
    docs_url=None,  # served by app.docs (pinned Swagger UI with integrity hashes) when docs are on
    openapi_url="/openapi.json" if settings.docs_enabled else None,
    redoc_url=None,
    # "/v1/schemes/current-month/" is a 404, not a 307 to the path without the slash: behind a proxy
    # that ends TLS the redirect could point at http://, and POST clients may not follow it.
    redirect_slashes=False,
)
# The last middleware added runs first: security headers wrap everything, including 413s and 429s;
# the rate limit refuses a request before its body is read; the size limit applies before the
# lenient-JSON middleware buffers a body.
app.add_middleware(LenientJsonNumbersMiddleware)
app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_body_bytes)
if settings.rate_limit_enabled:
    app.add_middleware(RateLimitMiddleware, limit=settings.token_rate_limit_per_minute, window_seconds=60,
                       paths={TOKEN_URL}, name="token", redis_url=settings.redis_url,
                       trusted_proxies=settings.trusted_proxies)
    app.add_middleware(RateLimitMiddleware, limit=settings.rate_limit_per_second, redis_url=settings.redis_url,
                       trusted_proxies=settings.trusted_proxies)
app.add_middleware(SecurityHeadersMiddleware)
app.include_router(auth_router)
app.include_router(router)
if settings.docs_enabled:
    app.include_router(docs_router)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # FastAPI's default 422 body echoes the caller's input back; return only where and why it failed.
    errors = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errors})


@app.exception_handler(PoolTimeout)
async def database_busy(request: Request, exc: PoolTimeout) -> JSONResponse:
    log.warning("No free database connection for %s %s", request.method, request.url.path)
    return JSONResponse(status_code=503, content={"detail": "Service busy, please retry"}, headers={"Retry-After": "2"})


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    # Starlette sends this response from its outermost layer, outside SecurityHeadersMiddleware.
    return JSONResponse(status_code=500, content={"detail": "Internal server error"}, headers=SECURITY_HEADERS)


@app.get("/health", tags=["health"], responses=HEALTH_RESPONSES)
def health(repo: Repository = Depends(get_repository)) -> JSONResponse:
    # Checks the database too: a deploy that can't reach it fails its health check instead of
    # passing it and then answering 503 to every data request.
    try:
        repo.ping(HEALTH_DB_TIMEOUT_SECONDS)
    except (PoolTimeout, psycopg.Error):
        log.warning("Health check: database unreachable")
        return JSONResponse(status_code=503, content={"ok": False, "detail": "Database unreachable"},
                            headers={"Retry-After": "5"})
    return JSONResponse(content={"ok": True})


# Some load balancers probe with HEAD. Kept out of the OpenAPI spec, which documents GET only.
app.head("/health", include_in_schema=False)(health)
