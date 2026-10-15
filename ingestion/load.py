"""Write parsed files into Postgres. Each file is loaded in one transaction."""
from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb

from ingestion.sources import SourceFile

SCHEME_KEY = ("year", "month", "dealer_code", "agreement", "deal_suffix")
SCHEME_FIELDS = (
    *SCHEME_KEY, "component", "valid_from", "valid_to", "qualification_basis",
    "qualification", "gsr_b", "rebate_rate", "clear_cut", "shortfall_amount",
    "shortfall_pct", "last_updated_at", "source_file", "raw",
)
# Keep only the newest version of each row: an older update (e.g. a file loaded
# out of order) never overwrites a newer one.
_COLUMNS = ", ".join(SCHEME_FIELDS)
_UPDATES = ", ".join(f"{f} = EXCLUDED.{f}" for f in SCHEME_FIELDS if f not in SCHEME_KEY)
_KEY = ", ".join(SCHEME_KEY)
# A file's rows are copied into a staging table in one round trip (COPY), then upserted with one
# statement, instead of one INSERT per row (~5,500 per file), which is slow over a remote link.
# The staging table is made fresh for each file, so it always matches scheme_records (constraints
# and defaults included) and never holds an earlier file's rows, however the caller manages transactions.
_DROP_STAGE = "DROP TABLE IF EXISTS pg_temp.scheme_stage"
_CREATE_STAGE = (
    "CREATE TEMP TABLE scheme_stage (LIKE scheme_records INCLUDING CONSTRAINTS INCLUDING DEFAULTS, ord BIGINT NOT NULL) "
    "ON COMMIT DROP"
)
_COPY_STAGE = f"COPY pg_temp.scheme_stage ({_COLUMNS}, ord) FROM STDIN"  # nosec B608: fixed column names
# Same result as upserting the rows one by one in file order: within the file the newest
# "Last Updated" wins and a tie goes to the later row (ord); against the table, a row only
# replaces a stored one that is not newer.
_UPSERT_STAGE = (
    f"INSERT INTO scheme_records ({_COLUMNS}) "  # nosec B608: fixed column names, no values in the SQL
    f"SELECT DISTINCT ON ({_KEY}) {_COLUMNS} FROM pg_temp.scheme_stage "
    f"ORDER BY {_KEY}, last_updated_at DESC, ord DESC "
    f"ON CONFLICT ({_KEY}) DO UPDATE SET {_UPDATES} "
    "WHERE EXCLUDED.last_updated_at >= scheme_records.last_updated_at"
)


def is_loaded(conn: Connection, kind: str, file: SourceFile) -> bool:
    row = conn.execute(
        "SELECT generation FROM ingested_files WHERE kind = %s AND gcs_path = %s",
        (kind, file.path),
    ).fetchone()
    return row is not None and row["generation"] == file.generation


def save_scheme_file(
    conn: Connection, file: SourceFile, year: int, month: int, records: list[dict[str, Any]]
) -> None:
    with conn.transaction():
        _replace_file(conn, "scheme", file, year, month, len(records))
        conn.execute(_DROP_STAGE)
        conn.execute(_CREATE_STAGE)
        with conn.cursor() as cur:
            with cur.copy(_COPY_STAGE) as copy:
                for ord_, r in enumerate(records):
                    copy.write_row([*(Jsonb(r[f]) if f == "raw" else r[f] for f in SCHEME_FIELDS), ord_])
            cur.execute(_UPSERT_STAGE)


def _replace_file(
    conn: Connection, kind: str, file: SourceFile, year: int, month: int, row_count: int
) -> int:
    # Re-recording a file replaces its entry; its scheme rows are upserted, not deleted.
    conn.execute("DELETE FROM ingested_files WHERE kind = %s AND gcs_path = %s", (kind, file.path))
    return conn.execute(
        """
        INSERT INTO ingested_files (kind, gcs_path, generation, source_updated_at, year, month, row_count)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (kind, file.path, file.generation, file.updated_at, year, month, row_count),
    ).fetchone()["id"]
