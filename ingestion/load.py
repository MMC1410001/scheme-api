"""Write parsed files into Postgres. Each file is loaded in one transaction."""
from collections.abc import Iterable, Iterator
from itertools import islice
from typing import Any

from psycopg import Connection
from psycopg.types.json import Json, Jsonb

from ingestion.sources import SourceFile

REPORT_BATCH_SIZE = 1000
SCHEME_KEY = ("year", "month", "dealer_code", "agreement", "deal_suffix")
SCHEME_FIELDS = (
    *SCHEME_KEY, "component", "valid_from", "valid_to", "qualification_basis",
    "qualification", "gsr_b", "rebate_rate", "clear_cut", "shortfall_amount",
    "shortfall_pct", "last_updated_at", "source_file", "raw",
)
# Keep only the newest version of each row: an older update (e.g. a file loaded
# out of order) never overwrites a newer one.
_COLUMNS = ", ".join(SCHEME_FIELDS)
_PLACEHOLDERS = ", ".join(["%s"] * len(SCHEME_FIELDS))
_UPDATES = ", ".join(f"{f} = EXCLUDED.{f}" for f in SCHEME_FIELDS if f not in SCHEME_KEY)
_UPSERT_SCHEME = (
    f"INSERT INTO scheme_records ({_COLUMNS}) VALUES ({_PLACEHOLDERS}) "  # nosec B608: fixed column names; values are %s params
    f"ON CONFLICT ({', '.join(SCHEME_KEY)}) DO UPDATE SET {_UPDATES} "
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
        with conn.cursor() as cur:
            cur.executemany(
                _UPSERT_SCHEME,
                [tuple(Jsonb(r[f]) if f == "raw" else r[f] for f in SCHEME_FIELDS) for r in records],
            )


def save_report_file(
    conn: Connection,
    file: SourceFile,
    year: int | None,
    month: int | None,
    version: int | None,
    columns: list[str],
    rows: Iterable[tuple[str, list[Any]]],
) -> int:
    """Store a report in one transaction, streaming its rows in batches. Returns the row count."""
    with conn.transaction():
        file_id = _replace_file(conn, "report", file, year, month, 0)
        count = 0
        with conn.cursor() as cur:
            for batch in batched(rows, REPORT_BATCH_SIZE):
                cur.executemany(
                    "INSERT INTO report_rows (file_id, dealer_code, cells) VALUES (%s, %s, %s)",
                    [(file_id, dealer_code, Json(cells)) for dealer_code, cells in batch],
                )
                count += len(batch)
        conn.execute(
            "UPDATE ingested_files SET row_count = %s, version = %s, columns = %s WHERE id = %s",
            (count, version, Json(columns), file_id),
        )
    return count


def _replace_file(
    conn: Connection, kind: str, file: SourceFile, year: int | None, month: int | None, row_count: int
) -> int:
    # For reports, deleting the old entry also deletes its rows (ON DELETE CASCADE),
    # so a re-uploaded report replaces itself. Scheme rows are upserted instead.
    conn.execute("DELETE FROM ingested_files WHERE kind = %s AND gcs_path = %s", (kind, file.path))
    return conn.execute(
        """
        INSERT INTO ingested_files (kind, gcs_path, generation, source_updated_at, year, month, row_count)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (kind, file.path, file.generation, file.updated_at, year, month, row_count),
    ).fetchone()["id"]


def batched(items: Iterable[Any], size: int) -> Iterator[list[Any]]:
    iterator = iter(items)
    while batch := list(islice(iterator, size)):
        yield batch
