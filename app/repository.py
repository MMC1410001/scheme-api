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

    def ping(self, timeout: float) -> None:
        """Raises unless the database answers within about `timeout` seconds (for /health)."""
        with self.pool.connection(timeout=timeout) as conn:
            conn.execute("SELECT 1")

    def month_schemes(self, dealer_code: str, year: int, month: int) -> list[dict[str, Any]]:
        # A row belongs to every month its validity period (From..To) overlaps, whichever GCS month
        # folder its file sat in: SAP keeps updating last month's schemes in the first days of the
        # next month's folder. A row without dates falls back to its folder's month. One component
        # and start date is one scheme: only its newest SAP update is returned, from any folder, even
        # when SAP changed the end date (1-18 Sep corrected to 1-19 Sep) or re-issued the scheme
        # under a new agreement number. A scheme for another month starts on another date, so it
        # stays a separate row. The newest row is picked before the end date is checked, so an old
        # end date can't bring it back. Equal update times: the newer folder, then the higher agreement,
        # then the later end date and the higher deal suffix. The loader deletes every other version
        # with the same order (ingestion/load.py, NEWEST_FIRST), so this mostly finds one row per scheme.
        # Every component SAP sends, by Clear Cut, highest first (business rule, 2026-10-22); empty Clear Cut last.
        # Equal Clear Cut (often 0): the API template's order, by end date, then the template's
        # component order; any other component after those, by name.
        first = date(year, month, 1)
        last = date(year, month, calendar.monthrange(year, month)[1])
        return self._fetch_all(
            """
            SELECT dealer_code AS customer, valid_from AS from_date, valid_to AS to_date,
                   component AS component_description, qualification, gsr_b,
                   rebate_rate, clear_cut, shortfall_amount, shortfall_pct, next_slab_rate,
                   qualification_basis, invoiced_qty
            FROM (
                SELECT DISTINCT ON (component, valid_from) *
                FROM scheme_records
                WHERE dealer_code = %(dealer)s
                  AND CASE WHEN valid_from IS NULL THEN year = %(year)s AND month = %(month)s
                           ELSE valid_from <= %(last)s END
                ORDER BY component, valid_from, last_updated_at DESC, year DESC, month DESC, agreement DESC,
                         valid_to DESC NULLS LAST, deal_suffix DESC
            ) latest
            WHERE valid_from IS NULL OR coalesce(valid_to, valid_from) >= %(first)s
            ORDER BY clear_cut DESC NULLS LAST, valid_to NULLS LAST,
                     array_position(%(components)s::text[], component), component, agreement
            """,
            {"dealer": dealer_code, "year": year, "month": month, "first": first, "last": last,
             "components": list(COMPONENTS)},
        )


async def get_repository() -> Repository:
    return Repository(get_pool())
