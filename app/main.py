import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg_pool import PoolTimeout

from app import cache, db
from app.config import get_settings
from app.middleware import BodySizeLimitMiddleware, SecurityHeadersMiddleware
from app.routes import router

log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.open_pool()
    cache.open_cache(settings.cache_enabled, settings.redis_url, settings.cache_ttl_seconds)
    yield
    cache.close_cache()
    db.close_pool()


settings = get_settings()
app = FastAPI(
    title="Scheme API",
    version="0.1.0",
    description="Scheme data (from SAP files in GCS) for the client team.",
    lifespan=lifespan,
    docs_url="/docs" if settings.docs_enabled else None,
    openapi_url="/openapi.json" if settings.docs_enabled else None,
    redoc_url=None,
)
# The last middleware added runs first: security headers wrap everything, including 413s.
app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_body_bytes)
app.add_middleware(SecurityHeadersMiddleware)
app.include_router(router)


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
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/health", tags=["health"])
def health() -> dict[str, bool]:
    return {"ok": True}
