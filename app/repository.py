import calendar
from datetime import date
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
        # A row belongs to every month its validity period (From..To) overlaps, whichever GCS month
        # folder its file sat in: SAP keeps updating last month's schemes in the first days of the
        # next month's folder. A row without dates falls back to its folder's month. The same
        # agreement and period can be stored under two folders; the newest SAP update wins (the
        # period is part of the key so an agreement reused for another month is never dropped).
        # Same order as the API template: by end date, then the template's component order.
        first = date(year, month, 1)
        last = date(year, month, calendar.monthrange(year, month)[1])
        return self._fetch_all(
            """
            SELECT dealer_code AS customer, valid_from AS from_date, valid_to AS to_date,
                   component AS component_description, qualification, gsr_b,
                   rebate_rate, clear_cut, shortfall_amount, shortfall_pct, next_slab_rate
            FROM (
                SELECT DISTINCT ON (agreement, deal_suffix, valid_from, valid_to) *
                FROM scheme_records
                WHERE dealer_code = %(dealer)s
                  AND CASE WHEN valid_from IS NULL THEN year = %(year)s AND month = %(month)s
                           ELSE valid_from <= %(last)s AND coalesce(valid_to, valid_from) >= %(first)s END
                ORDER BY agreement, deal_suffix, valid_from, valid_to, last_updated_at DESC, year DESC, month DESC
            ) latest
            ORDER BY valid_to NULLS LAST, array_position(%(components)s::text[], component), agreement
            """,
            {"dealer": dealer_code, "year": year, "month": month, "first": first, "last": last,
             "components": list(COMPONENTS)},
        )


async def get_repository() -> Repository:
    return Repository(get_pool())
