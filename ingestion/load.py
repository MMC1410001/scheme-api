"""Write parsed files into Postgres. Each file is loaded in one transaction."""
from datetime import date
from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb

from app.constants import GSR_COMPONENTS
from ingestion.sources import SourceFile

# Raise this when a release changes which rows or values the loader keeps from a file. Files read by
# an older version are then read again by the next run (scheduled or by hand) of their month, so no
# --reload is needed. 1: every component (was the 10 template ones), invoiced_qty, component in the key.
LOADER_VERSION = 1

# A row's identity: the validity period is part of it, so one month's folder can hold the same
# agreement and suffix for two periods; so is the component, as SAP can give two components the same
# suffix (sql/001_init.sql, scheme_records_key).
SCHEME_KEY = ("year", "month", "dealer_code", "agreement", "deal_suffix", "component", "valid_from", "valid_to")
SCHEME_FIELDS = (
    "year", "month", "dealer_code", "agreement", "deal_suffix", "component", "valid_from", "valid_to",
    "qualification_basis", "qualification", "gsr_b", "rebate_rate", "clear_cut", "shortfall_amount",
    "shortfall_pct", "next_slab_rate", "invoiced_qty", "last_updated_at", "source_file", "raw",
)
# raw is stored as its values only, in the file's column order; raw_layout points to the column
# names, stored once in raw_layouts (half of a row was the 54 names repeated). Every value is kept,
# zeros and empty ones included; the view scheme_records_raw shows raw as name -> value again.
STORED_FIELDS = (*SCHEME_FIELDS, "raw_layout")
# Keep only the newest version of each row: an older update (e.g. a file loaded
# out of order) never overwrites a newer one.
_COLUMNS = ", ".join(STORED_FIELDS)
_UPDATES = ", ".join(f"{f} = EXCLUDED.{f}" for f in STORED_FIELDS if f not in SCHEME_KEY)
# The values an update would change, the file name aside: equal means SAP sent the same row again.
_COMPARED = [f for f in STORED_FIELDS if f not in SCHEME_KEY and f != "source_file"]
_UPDATED = ", ".join(f"scheme_records.{f}" for f in _COMPARED)
_EXCLUDED = ", ".join(f"EXCLUDED.{f}" for f in _COMPARED)
_KEY = ", ".join(SCHEME_KEY)  # DISTINCT ON treats empty dates as equal, like the unique index
# The unique index's columns, exactly as written there, so Postgres can match ON CONFLICT to it.
_CONFLICT_KEY = (
    "year, month, dealer_code, agreement, deal_suffix, component, "
    "(coalesce(valid_from, '-infinity'::date)), (coalesce(valid_to, '-infinity'::date))"
)
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
    f"ON CONFLICT ({_CONFLICT_KEY}) DO UPDATE SET {_UPDATES} "
    "WHERE EXCLUDED.last_updated_at >= scheme_records.last_updated_at"
    # SAP sending the same row again rewrites nothing (no dead row versions to vacuum).
    f" AND ({_UPDATED}) IS DISTINCT FROM ({_EXCLUDED})"
)

# API 1 serves one row per dealer, component and start date: the newest SAP update, from any
# folder and any agreement (app/repository.py, month_schemes). Rows it would never serve are deleted,
# so the table holds one row per scheme. Rows without a start date are one scheme per folder month.
# Newest first: update time, then the newer folder, then the higher agreement; on a full tie the
# later end date (none counts as the earliest), then the higher deal suffix. The API orders the same way.
SCHEME_GROUP = (
    "dealer_code, component, valid_from, "
    "(CASE WHEN valid_from IS NULL THEN year END), (CASE WHEN valid_from IS NULL THEN month END)"
)
NEWEST_FIRST = (
    "last_updated_at DESC, year DESC, month DESC, agreement DESC, valid_to DESC NULLS LAST, deal_suffix DESC"
)
_RANKED = (
    f"SELECT ctid AS row_id, row_number() OVER (PARTITION BY {SCHEME_GROUP} ORDER BY {NEWEST_FIRST}) AS rank "  # nosec B608: fixed SQL
    "FROM scheme_records"
)
# After a file: only the schemes the file touched, found through the dealer index.
_PRUNE_STAGE = (
    "WITH touched AS ("  # nosec B608: fixed SQL, no values in it
    "  SELECT DISTINCT dealer_code, component, valid_from, "
    "         CASE WHEN valid_from IS NULL THEN year END AS year, CASE WHEN valid_from IS NULL THEN month END AS month "
    "  FROM pg_temp.scheme_stage"
    "), ranked AS ("
    f"  {_RANKED} r WHERE EXISTS (SELECT 1 FROM touched t WHERE t.dealer_code = r.dealer_code "
    "    AND t.component = r.component AND (t.valid_from = r.valid_from "
    "      OR (t.valid_from IS NULL AND r.valid_from IS NULL AND t.year = r.year AND t.month = r.month)))"
    ") DELETE FROM scheme_records WHERE ctid IN (SELECT row_id FROM ranked WHERE rank > 1)"
)
# Once, for rows stored before pruning existed (see prune_all).
_PRUNE_ALL = (
    f"WITH ranked AS ({_RANKED}) "  # nosec B608: fixed SQL
    "DELETE FROM scheme_records WHERE ctid IN (SELECT row_id FROM ranked WHERE rank > 1)"
)
# Once, for rows stored with raw as a name -> value object (see compact_raw): the names go to
# raw_layouts (an object's keys come back in one fixed order, so equal key sets share a layout).
_ADD_OBJECT_LAYOUTS = (
    "INSERT INTO raw_layouts (columns) "
    "SELECT DISTINCT ARRAY(SELECT jsonb_object_keys(raw)) FROM scheme_records "
    "WHERE raw_layout IS NULL AND jsonb_typeof(raw) = 'object' "
    "ON CONFLICT (columns) DO NOTHING"
)
_OBJECTS_TO_VALUES = (
    "WITH k AS ("
    "  SELECT ctid AS row_id, ARRAY(SELECT jsonb_object_keys(raw)) AS columns FROM scheme_records "
    "  WHERE raw_layout IS NULL AND jsonb_typeof(raw) = 'object'"
    ") UPDATE scheme_records r SET raw_layout = l.id, raw = coalesce("
    "    (SELECT jsonb_agg(r.raw -> u.name ORDER BY u.i) FROM unnest(l.columns) WITH ORDINALITY AS u(name, i)),"
    "    '[]'::jsonb) "
    "FROM k JOIN raw_layouts l ON l.columns = k.columns WHERE r.ctid = k.row_id"
)
# Once, for rows stored before the qualification rule of 2026-10-22 (see requalify): components
# outside GSR_COMPONENTS took GSR (Q); they now take invoiced quantity (Q), which every row already
# keeps in invoiced_qty. Rows that already hold it are not written again. Done a batch of dealers at
# a time (through the dealer index), each followed by a VACUUM, so the rewritten rows reuse the space
# of the old ones: dev had 5.8 million of 6.8 million rows to rewrite in one 6.9 GB table.
_TO_INVOICE_QTY = (
    "UPDATE scheme_records SET qualification_basis = 'INVOICE_QTY', qualification = invoiced_qty "
    "WHERE dealer_code = ANY(%s) AND component <> ALL(%s) "
    "AND (qualification_basis, qualification) IS DISTINCT FROM ('INVOICE_QTY', invoiced_qty)"
)
REQUALIFY_DEALERS_PER_BATCH = 2000  # about 300,000 rows on dev
# A scheme has expired when its period ended before the cutoff; open-ended counts as its start date
# (one day), and a row without a start date by its folder month.
_DELETE_EXPIRED = (
    "DELETE FROM scheme_records WHERE CASE WHEN valid_from IS NULL THEN make_date(year, month, 1) "
    "ELSE coalesce(valid_to, valid_from) END < %s"
)


def is_loaded(conn: Connection, kind: str, file: SourceFile) -> bool:
    """This version of the file was already read by this loader version (or a newer one)."""
    row = conn.execute(
        "SELECT generation, loader_version FROM ingested_files WHERE kind = %s AND gcs_path = %s",
        (kind, file.path),
    ).fetchone()
    return row is not None and row["generation"] == file.generation and row["loader_version"] >= LOADER_VERSION


def save_scheme_file(
    conn: Connection, file: SourceFile, year: int, month: int, records: list[dict[str, Any]], rows_bad: int = 0,
    failed: bool = False,
) -> int:
    """Save a file's rows and record it as loaded, or as failed if rows_bad rows could not be read
    or the whole file could not be read (failed, with no records). Older versions of the schemes
    in the file are deleted in the same transaction; returns how many rows that deleted."""
    with conn.transaction():
        _replace_file(conn, "scheme", file, year, month, len(records), rows_bad, failed or rows_bad > 0)
        layouts = {names: _layout_id(conn, names) for names in {tuple(r["raw"]) for r in records}}  # usually one
        conn.execute(_DROP_STAGE)
        conn.execute(_CREATE_STAGE)
        with conn.cursor() as cur:
            with cur.copy(_COPY_STAGE) as copy:
                for ord_, r in enumerate(records):
                    copy.write_row([*(Jsonb(list(r["raw"].values())) if f == "raw" else r[f] for f in SCHEME_FIELDS),
                                    layouts[tuple(r["raw"])], ord_])
            cur.execute(_UPSERT_STAGE)
            cur.execute(_PRUNE_STAGE)
            return cur.rowcount


def _layout_id(conn: Connection, names: tuple[str, ...]) -> int:
    """The raw_layouts row for these column names, added the first time they are seen."""
    conn.execute("INSERT INTO raw_layouts (columns) VALUES (%s) ON CONFLICT (columns) DO NOTHING", (list(names),))
    return conn.execute("SELECT id FROM raw_layouts WHERE columns = %s", (list(names),)).fetchone()["id"]


def compact_raw(conn: Connection) -> int | None:
    """Once per database: rewrite raw of rows stored as name -> value objects (before raw_layouts)
    as values only. Every value is kept. Returns the rows rewritten, or None when done before."""
    with conn.transaction():
        first = conn.execute(
            "INSERT INTO loader_maintenance (name) VALUES ('raw-values-only') ON CONFLICT DO NOTHING RETURNING name"
        ).fetchone()
        if first is None:
            return None
        conn.execute(_ADD_OBJECT_LAYOUTS)
        return conn.execute(_OBJECTS_TO_VALUES).rowcount


def prune_all(conn: Connection) -> int | None:
    """Delete the older versions of every scheme, once per database: rows loaded before pruning
    existed. Later files prune their own schemes (save_scheme_file). Returns the rows deleted, or
    None when this was done before."""
    with conn.transaction():
        first = conn.execute(
            "INSERT INTO loader_maintenance (name) VALUES ('prune-older-versions') ON CONFLICT DO NOTHING RETURNING name"
        ).fetchone()
        if first is None:
            return None
        return conn.execute(_PRUNE_ALL).rowcount


def requalify(conn: Connection, dealers_per_batch: int = REQUALIFY_DEALERS_PER_BATCH) -> int | None:
    """Once per database: give rows stored before the 2026-10-22 rule the Qualification new files
    get (invoiced quantity outside GSR_COMPONENTS). Needs an autocommit connection: each batch commits
    on its own, then VACUUM marks its old rows reusable. Stopped half way, the next run carries on
    (done rows are skipped). Returns the rows rewritten, or None when done before."""
    name = "qualification-rule-2026-10-22"
    if conn.execute("SELECT 1 FROM loader_maintenance WHERE name = %s", (name,)).fetchone():
        return None
    dealers = [r["dealer_code"] for r in conn.execute("SELECT DISTINCT dealer_code FROM scheme_records ORDER BY 1")]
    rewritten = 0
    for start in range(0, len(dealers), dealers_per_batch):
        batch = conn.execute(_TO_INVOICE_QTY, (dealers[start:start + dealers_per_batch], list(GSR_COMPONENTS))).rowcount
        if batch:
            conn.execute("VACUUM (PARALLEL 0) scheme_records")
        rewritten += batch
    conn.execute("INSERT INTO loader_maintenance (name) VALUES (%s) ON CONFLICT DO NOTHING", (name,))
    return rewritten


def delete_expired(conn: Connection, cutoff: date) -> int:
    """Delete schemes whose period ended before cutoff; returns how many."""
    return conn.execute(_DELETE_EXPIRED, (cutoff,)).rowcount


def retention_cutoff(today: date, months: int) -> date:
    """First day of the oldest month kept: months=3 in October keeps October, September and August."""
    index = today.year * 12 + today.month - 1 - (months - 1)
    return date(index // 12, index % 12 + 1, 1)


def _replace_file(
    conn: Connection, kind: str, file: SourceFile, year: int, month: int, row_count: int, rows_bad: int = 0,
    failed: bool = False,
) -> int:
    # Re-recording a file replaces its entry; its scheme rows are upserted, not deleted.
    conn.execute("DELETE FROM ingested_files WHERE kind = %s AND gcs_path = %s", (kind, file.path))
    return conn.execute(
        """
        INSERT INTO ingested_files (kind, gcs_path, generation, source_updated_at, year, month, row_count,
                                    status, rows_bad, loader_version)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (kind, file.path, file.generation, file.updated_at, year, month, row_count,
         "failed" if failed else "loaded", rows_bad, LOADER_VERSION),
    ).fetchone()["id"]
