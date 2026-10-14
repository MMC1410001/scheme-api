from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import get_settings

_pool: ConnectionPool | None = None


def open_pool() -> None:
    global _pool
    settings = get_settings()
    _pool = ConnectionPool(
        settings.database_url,
        min_size=settings.db_pool_min,
        max_size=settings.db_pool_max,
        # Wait at most this long for a free connection, then answer 503 instead of hanging.
        timeout=settings.db_pool_timeout,
        kwargs={"autocommit": True, "row_factory": dict_row},
        # Check each connection before use, so a DB restart doesn't surface as 500s.
        check=ConnectionPool.check_connection,
        open=True,
    )


def close_pool() -> None:
    if _pool is not None:
        _pool.close()


def get_pool() -> ConnectionPool:
    if _pool is None:
        raise RuntimeError("Database pool is not open")
    return _pool
