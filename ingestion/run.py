"""Cron entry point.

    python -m ingestion.run init-db                    # create tables
    python -m ingestion.run schemes                    # load this month's scheme files
    python -m ingestion.run schemes --year 2026 --month 9
    python -m ingestion.run schemes --year 2026 --month 9 --reload   # re-read files already loaded
    # python -m ingestion.run reports                  # load generated reports (API 2, on hold)
"""
import argparse
import logging
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row

from app.cache import bump_data_version
from app.config import get_settings
from ingestion.excel import read_rows
from ingestion.load import is_loaded, save_scheme_file
from ingestion.schemes import check_columns, to_record
from ingestion.sources import list_files, month_prefixes
# API 2 is on hold: the report loader below also needs normalize_dealer_code, read_table,
# save_report_file, clean_value, report_period and SourceFile; re-import them to restore it.

log = logging.getLogger("ingestion")
SCHEMA_FILE = Path(__file__).resolve().parent.parent / "sql" / "001_init.sql"
# A dropped database connection (network or VPN blip) reconnects and carries on: loaded files are
# skipped and each file is saved in one transaction, so nothing is loaded twice or half-loaded.
RECONNECT_ATTEMPTS = 5
RECONNECT_WAIT_SECONDS = 10


def load_schemes(conn: psycopg.Connection, year: int, month: int, reload: bool = False) -> Counter:
    """Load one month's SAP files. With reload, files already loaded are read again
    (e.g. after a business-rule change); the upsert then rewrites their rows."""
    settings = get_settings()
    stats: Counter = Counter()
    files = []
    for prefix in month_prefixes(settings.scheme_prefix_template, year, month):
        files = list_files(prefix)
        if files:
            log.info("Reading %d files from %s", len(files), prefix)
            break
    else:
        log.warning("No scheme files yet for %d-%02d (looked in %s)", year, month,
                    ", ".join(month_prefixes(settings.scheme_prefix_template, year, month)))
    for file in files:
        if not reload and is_loaded(conn, "scheme", file):
            stats["files_skipped"] += 1
            continue
        try:
            rows = read_rows(file.read(), file.path, settings.scheme_header_rows)
            if rows:
                check_columns(rows[0].keys())
            records = []
            for number, row in enumerate(rows, start=1):
                try:
                    record = to_record(row, year, month, file.path)
                except ValueError as exc:
                    log.warning("%s data row %d skipped: %s", file.path, number, exc)
                    stats["rows_bad"] += 1
                    continue
                if record is None:
                    stats["rows_other_components"] += 1
                else:
                    records.append(record)
            save_scheme_file(conn, file, year, month, records)
        except psycopg.OperationalError:
            raise  # connection lost: main() reconnects and resumes
        except Exception:
            log.exception("Failed to load %s", file.path)
            stats["files_failed"] += 1
            continue
        stats["files_loaded"] += 1
        stats["rows_loaded"] += len(records)
    return stats


# --- API 2 is on hold (2026-10-15): the report loader (`reports` command). Uncomment to restore. ---
# def load_reports(conn: psycopg.Connection) -> Counter:
#     """Load the latest report if it isn't loaded yet; API 2 only ever serves the latest one.
#
#     Latest = newest <year>/<month> folder, then highest _v<N> in the file name, then newest upload.
#     Older reports are not loaded (each takes about two minutes), so a first run loads one file.
#     """
#     settings = get_settings()
#     stats: Counter = Counter()
#     files = list_files(settings.report_prefix)
#     if not files:
#         log.warning("No report files in %s", settings.report_prefix)
#         return stats
#     file = max(files, key=_report_rank)
#     period = report_period(file.path)
#     stats["files_older"] = len(files) - 1
#     if is_loaded(conn, "report", file):
#         log.info("Latest report %s is already loaded", file.path)
#         stats["files_skipped"] += 1
#         return stats
#     if period.year is None:
#         log.warning("%s is not in a <year>/<month>/ folder", file.path)
#     try:
#         log.info("Reading %s", file.path)
#         columns, rows = read_table(file.read(), file.path, settings.report_header_rows)
#         dealer_index = _report_dealer_index(columns, settings.report_dealer_column)
#
#         def records():
#             for row in rows:
#                 dealer_code = normalize_dealer_code(row[dealer_index])
#                 if dealer_code:
#                     yield dealer_code, [clean_value(v) for v in row]
#                 else:
#                     stats["rows_bad"] += 1
#
#         count = save_report_file(conn, file, period.year, period.month, period.version, columns, records())
#     except psycopg.OperationalError:
#         raise  # connection lost: main() reconnects and resumes
#     except Exception:
#         log.exception("Failed to load %s", file.path)
#         stats["files_failed"] += 1
#         return stats
#     stats["files_loaded"] += 1
#     stats["rows_loaded"] += count
#     return stats
#
#
# def _report_rank(file: SourceFile) -> tuple:
#     period = report_period(file.path)
#     return (period.year or 0, period.month or 0, period.version or 0, file.updated_at)
#
#
# def _report_dealer_index(columns: list[str], dealer_column: str) -> int:
#     if not columns:
#         raise ValueError("File has no header rows")
#     if dealer_column not in columns:
#         raise ValueError(f"No {dealer_column!r} column. First columns in file: {columns[:10]}")
#     return columns.index(dealer_column)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Load Scheme files from GCS into Postgres.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db", help="create the tables")
    schemes = commands.add_parser("schemes", help="load one month of scheme files (default: current month)")
    schemes.add_argument("--year", type=int)
    schemes.add_argument("--month", type=int)
    schemes.add_argument("--reload", action="store_true", help="re-read files that are already loaded")
    # commands.add_parser("reports", help="load generated period reports")  # API 2 is on hold (2026-10-15)
    args = parser.parse_args(argv)

    settings = get_settings()
    stats: Counter = Counter()
    for attempt in range(1, RECONNECT_ATTEMPTS + 1):
        try:
            with psycopg.connect(settings.database_url, autocommit=True, row_factory=dict_row,
                                 keepalives=1, keepalives_idle=30) as conn:
                if args.command == "init-db":
                    conn.execute(SCHEMA_FILE.read_text())
                    log.info("Tables created")
                    return 0
                if args.command == "schemes":
                    today = datetime.now(ZoneInfo(settings.timezone)).date()
                    # With --reload a reconnect starts the month over; the upsert makes that safe.
                    stats += load_schemes(conn, args.year or today.year, args.month or today.month, args.reload)
                # else:  # API 2 is on hold (2026-10-15)
                #     stats += load_reports(conn)
            break
        except psycopg.OperationalError as exc:
            if attempt == RECONNECT_ATTEMPTS:
                raise
            log.warning("Database connection lost (%s); reconnecting in %ds (attempt %d of %d)",
                        str(exc).strip(), RECONNECT_WAIT_SECONDS, attempt + 1, RECONNECT_ATTEMPTS)
            time.sleep(RECONNECT_WAIT_SECONDS)

    log.info("Done: %s", dict(stats))
    if stats["files_loaded"] and settings.cache_enabled and settings.redis_url:
        bump_data_version(settings.redis_url)  # cached API responses now get the new data
    return 1 if stats["files_failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
