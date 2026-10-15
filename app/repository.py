from typing import Any

from psycopg_pool import ConnectionPool

from app.constants import COMPONENTS
from app.db import get_pool


class Repository:
    """Every SQL query the API runs lives here, so routes stay simple and tests can swap in a fake.

    Each method borrows a connection only for its own query. Holding one across the whole request
    (e.g. in a yield dependency) can deadlock under load: the dependency and the endpoint run on
    different threadpool threads, so threads end up waiting on connections nobody can release.
    """

    def __init__(self, pool: ConnectionPool) -> None:
        self.pool = pool

    def _fetch_all(self, query: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            return conn.execute(query, params).fetchall()

    def month_schemes(self, dealer_code: str, year: int, month: int) -> list[dict[str, Any]]:
        # Same order as the API template: by end date, then the template's component order.
        return self._fetch_all(
            """
            SELECT dealer_code AS customer, valid_from AS from_date, valid_to AS to_date,
                   component AS component_description, qualification, gsr_b,
                   rebate_rate, clear_cut, shortfall_amount, shortfall_pct
            FROM scheme_records
            WHERE dealer_code = %s AND year = %s AND month = %s
            ORDER BY valid_to NULLS LAST, array_position(%s::text[], component), agreement
            """,
            (dealer_code, year, month, list(COMPONENTS)),
        )


async def get_repository() -> Repository:
    return Repository(get_pool())
