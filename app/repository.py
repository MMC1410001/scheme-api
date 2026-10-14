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

# --- API 2 is on hold (2026-10-15): the report queries. Uncomment to restore. ---
#     def latest_report(self) -> dict[str, Any] | None:
#         # Same choice as the cron job: newest period folder, then highest _v<N>, then newest upload.
#         rows = self._fetch_all(
#             """
#             SELECT id, gcs_path, year, month, version, columns
#             FROM ingested_files
#             WHERE kind = 'report'
#             ORDER BY COALESCE(year, 0) DESC, COALESCE(month, 0) DESC,
#                      COALESCE(version, 0) DESC, source_updated_at DESC
#             LIMIT 1
#             """
#         )
#         return rows[0] if rows else None
#
#     def report_rows(self, report: dict[str, Any], dealer_code: str) -> list[dict[str, Any]]:
#         rows = self._fetch_all(
#             "SELECT cells FROM report_rows WHERE file_id = %s AND dealer_code = %s ORDER BY id",
#             (report["id"], dealer_code),
#         )
#         columns = report["columns"]
#         return [dict(zip(columns, row["cells"])) for row in rows]


async def get_repository() -> Repository:
    return Repository(get_pool())
