"""Cron entry point.

    python -m ingestion.run init-db                    # create tables
    python -m ingestion.run schemes                    # load this month's scheme files
    python -m ingestion.run schemes --year 2026 --month 9
    python -m ingestion.run schemes --year 2026 --month 9 --reload   # re-read files already loaded
    python -m ingestion.run schemes --year 2026 --month 9 --limit 5  # at most 5 new files (testing)
    python -m ingestion.run compact                    # give freed space back to the disk (locks the table)
"""
import argparse
import logging
import os
import secrets
import sys
import time
from collections import Counter, deque
from collections.abc import Callable, Iterable, Iterator
from itertools import islice
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row

from app.cache import bump_data_version
from app.config import get_settings
from app.logfile import log_to_file
from ingestion.excel import read_table
from ingestion.load import (
    compact_raw, delete_expired, is_loaded, prune_all, requalify, retention_cutoff, save_scheme_file,
)
from ingestion.schemes import check_columns, to_record
from ingestion.sources import SourceFile, list_files, month_prefixes

log = logging.getLogger("ingestion")
SCHEMA_FILE = Path(__file__).resolve().parent.parent / "sql" / "001_init.sql"
# A dropped database connection (network or VPN blip) reconnects and carries on: loaded files are
# skipped and each file is saved in one transaction, so nothing is loaded twice or half-loaded.
RECONNECT_ATTEMPTS = 5
RECONNECT_WAIT_SECONDS = 10
# Only one loader run at a time (e.g. a long backfill and the next cron run): a Postgres advisory
# lock, held for the connection's lifetime, so it is released even if the process dies.
LOADER_LOCK_KEY = "acme-scheme-loader"
LOADER_LOCK_SQL = "SELECT pg_try_advisory_lock(hashtext(%s)) AS locked"
# After a network drop the server may keep this run's old session (and its lock) alive for a long
# time. Each run names its sessions, so on reconnect it can end its own stale session and go on.
END_OWN_STALE_SESSIONS_SQL = """
SELECT pg_terminate_backend(a.pid) FROM pg_stat_activity a
JOIN pg_locks l ON l.pid = a.pid AND l.locktype = 'advisory' AND l.granted
WHERE a.application_name = %s AND a.pid <> pg_backend_pid()
"""
LOCK_RECLAIM_TRIES = 5


def load_schemes(
    conn: psycopg.Connection, year: int, month: int, reload: bool = False, limit: int | None = None,
    stats: Counter | None = None, done: set[str] | None = None,
) -> Counter:
    """Load one month's SAP files. With reload, files already loaded are read again
    (e.g. after a business-rule change); the upsert then rewrites their rows. limit loads at most
    that many new files (for testing).

    A file with rows that can't be read is read once more; if rows are still bad, its good rows are
    saved and the file is recorded as failed (counted in files_failed), so it is not retried.
    A file that can't be read at all (not UTF-8, missing columns, not a workbook) is also read once
    more, then recorded as failed with no rows, so it is not downloaded again on every run.

    stats and done carry a run across reconnects: counts are added as each file finishes, and the
    paths of files dealt with (skipped, loaded or failed) are kept in done, so a retry neither
    repeats them nor counts them twice.

    The next few files are downloaded and parsed in parallel (LOADER_WORKERS) while the current
    one is saved; files are still saved one at a time, in listing order, each in one transaction.
    """
    settings = get_settings()
    stats = Counter() if stats is None else stats
    done = set() if done is None else done
    # Every candidate folder is read ("…/01/" and "…/1/"): if both exist, neither is left out.
    files = []
    prefixes = month_prefixes(settings.scheme_prefix_template, year, month)
    for prefix in prefixes:
        found = [file for file in list_files(prefix) if file.path not in {f.path for f in files}]
        if found:
            log.info("Reading %d files from %s", len(found), prefix)
            files.extend(found)
    if not files:
        log.warning("No scheme files yet for %d-%02d (looked in %s)", year, month, ", ".join(prefixes))
    files = [file for file in files if file.path not in done]  # dealt with earlier in this run
    todo = [file for file in files if reload or not is_loaded(conn, "scheme", file)]
    stats["files_skipped"] += len(files) - len(todo)
    done.update(file.path for file in files if file not in todo)
    if limit is not None and len(todo) > limit:
        log.info("--limit %d: loading %d of %d new files; the rest are left for the next run", limit, limit, len(todo))
        todo = todo[:limit]

    def prepare(file: SourceFile) -> tuple[list[dict], Counter, float]:
        started = time.monotonic()
        # Bad rows are logged one by one only from the read that is kept, so each appears once.
        try:
            records, counts = parse_scheme_file(file.read(), file.path, year, month, settings.scheme_header_rows,
                                                log_rows=False)
            problem = f"{counts['rows_bad']} rows could not be read" if counts["rows_bad"] else None
        except UnreadableFile as exc:
            problem = f"the file could not be read ({exc})"
        if problem:
            # Read (download) the file once more, in case the first copy was damaged. If it is still
            # unreadable, UnreadableFile reaches the loop below, which records the file as failed.
            log.warning("%s: %s; reading the file again", file.path, problem)
            records, counts = parse_scheme_file(file.read(), file.path, year, month, settings.scheme_header_rows)
        return records, counts, time.monotonic() - started

    workers = max(1, settings.loader_workers)
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="read")
    try:
        for file, future in prefetched(pool, todo, prepare, ahead=workers):
            counts: Counter = Counter()
            try:
                records, counts, read_seconds = future.result()
                started = time.monotonic()
                stats["rows_replaced"] += save_scheme_file(conn, file, year, month, records, counts["rows_bad"])
            except psycopg.OperationalError:
                raise  # connection lost: main() reconnects and retries this file
            except UnreadableFile as exc:
                # Unreadable on both reads: recorded as failed with no rows, so later runs skip it
                # until SAP re-uploads it (or --reload); download errors are not this and are retried.
                log.error("%s could not be read after reading it again (%s); the file is marked failed",
                          file.path, exc)
                save_scheme_file(conn, file, year, month, [], failed=True)
                stats["files_failed"] += 1
                done.add(file.path)
                continue
            except Exception:
                log.exception("Failed to load %s", file.path)
                stats.update(counts)
                stats["files_failed"] += 1
                done.add(file.path)
                continue
            if counts["rows_bad"]:
                # Still bad after reading the file again: its good rows are saved, the file is recorded
                # as failed (ingested_files.status) and not retried until SAP re-uploads it.
                log.error("%s: %d rows still could not be read after reading the file again; its %d good rows "
                          "are loaded and the file is marked failed", file.path, counts["rows_bad"], len(records))
                stats["files_failed"] += 1
            else:
                log.info("Loaded %s: %d rows (read %.1fs, save %.1fs)", file.path, len(records),
                         read_seconds, time.monotonic() - started)
                stats["files_loaded"] += 1
            stats.update(counts)
            stats["rows_loaded"] += len(records)
            done.add(file.path)
    except BaseException:
        # Lost connection or Ctrl-C: drop queued downloads and don't wait for running ones
        # (their results would be thrown away; a reconnect downloads them again).
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown(wait=True)
    return stats


def parse_scheme_file(
    content: bytes, path: str, year: int, month: int, header_rows: int, log_rows: bool = True,
) -> tuple[list[dict], Counter]:
    """One SAP file's rows (every component), plus counts of rows left out.
    With log_rows, each skipped row is logged with the reason. Raises UnreadableFile if the file
    can't be parsed at all or lacks the expected columns (checked even when it has no data rows)."""
    counts: Counter = Counter()
    try:
        columns, grid = read_table(content, path, header_rows)
        check_columns(columns)
        rows = [dict(zip(columns, row)) for row in grid]
    except Exception as exc:  # the bytes are already downloaded, so this is the file's content
        raise UnreadableFile(f"{type(exc).__name__}: {exc}") from exc
    records = []
    for number, row in enumerate(rows, start=1):
        try:
            record = to_record(row, year, month, path)
        except ValueError as exc:
            if log_rows:
                log.warning("%s data row %d skipped: %s", path, number, exc)
            counts["rows_bad"] += 1
            continue
        if record is None:
            counts["rows_no_component"] += 1
        else:
            records.append(record)
    return records, counts


class UnreadableFile(Exception):
    """A downloaded file that can't be parsed as a whole (encoding, columns, format)."""


def prefetched(pool: ThreadPoolExecutor, items: Iterable, work: Callable, ahead: int) -> Iterator[tuple[object, Future]]:
    """Yield (item, future of work(item)) in item order, keeping at most `ahead` items started
    beyond the one being handed out, so memory stays bounded however many files there are."""
    remaining = iter(items)
    queue: deque = deque((item, pool.submit(work, item)) for item in islice(remaining, ahead))
    while queue:
        item, future = queue.popleft()
        upcoming = next(remaining, _END)
        if upcoming is not _END:
            queue.append((upcoming, pool.submit(work, upcoming)))
        yield item, future


_END = object()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Load Scheme files from GCS into Postgres.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db", help="create the tables")
    commands.add_parser("compact", help="give the space of deleted and rewritten rows back to the disk "
                                        "(VACUUM FULL: scheme_records is locked, API requests wait, ~1-2 min)")
    schemes = commands.add_parser("schemes", help="load one month of scheme files (default: current month)")
    schemes.add_argument("--year", type=int)
    schemes.add_argument("--month", type=int)
    schemes.add_argument("--reload", action="store_true", help="re-read files that are already loaded")
    schemes.add_argument("--limit", type=positive_int, help="load at most this many new files (for testing)")
    args = parser.parse_args(argv)

    settings = get_settings()
    log_to_file(settings)
    stats: Counter = Counter()
    done: set[str] = set()
    run_name = f"scheme-loader-{os.getpid()}-{secrets.token_hex(3)}"  # names this run's sessions
    lost_lock = False
    for attempt in range(1, RECONNECT_ATTEMPTS + 1):
        try:
            with psycopg.connect(settings.database_url, autocommit=True, row_factory=dict_row,
                                 keepalives=1, keepalives_idle=30, application_name=run_name) as conn:
                if args.command == "init-db":
                    ensure_schema(conn)
                    log.info("Tables created")
                    return 0
                if args.command == "compact":
                    if not take_loader_lock(conn, run_name):
                        log.warning("A loader run is in progress; try again when it has finished")
                        return 1
                    before = conn.execute("SELECT pg_total_relation_size('scheme_records') AS n").fetchone()["n"]
                    log.info("Compacting scheme_records; it is locked until this finishes")
                    # FULL takes no PARALLEL option; its index rebuilds use these workers (and shared memory).
                    conn.execute("SET max_parallel_maintenance_workers = 0")
                    conn.execute("VACUUM (FULL, ANALYZE) scheme_records")
                    after = conn.execute("SELECT pg_total_relation_size('scheme_records') AS n").fetchone()["n"]
                    log.info("scheme_records: %d MB -> %d MB", before // 2**20, after // 2**20)
                    return 0
                if args.command == "schemes":
                    if not take_loader_lock(conn, run_name, reclaim=attempt > 1):
                        if attempt == 1:
                            log.warning("Another loader run is still in progress; this run does nothing")
                            return 0
                        log.error("Reconnected, but another session holds the loader lock; stopping this run")
                        lost_lock = True
                        break
                    ensure_schema(conn)
                    today = datetime.now(ZoneInfo(settings.timezone)).date()
                    # --limit counts files across reconnects. With --reload a reconnect starts the
                    # month over; the upsert makes that safe.
                    limit = None if args.limit is None else args.limit - stats["files_loaded"] - stats["files_failed"]
                    if limit is None or limit > 0:
                        load_schemes(conn, args.year or today.year, args.month or today.month,
                                     args.reload, limit, stats, done)
                    tidy_up(conn, today, settings.loader_retention_months, stats)
            break
        except psycopg.OperationalError as exc:
            if attempt == RECONNECT_ATTEMPTS:
                raise
            log.warning("Database connection lost (%s); reconnecting in %ds (attempt %d of %d)",
                        str(exc).strip(), RECONNECT_WAIT_SECONDS, attempt + 1, RECONNECT_ATTEMPTS)
            time.sleep(RECONNECT_WAIT_SECONDS)

    log.info("Done: %s", dict(stats))
    # rows_loaded also counts the good rows of files marked failed, which are saved too.
    if (stats["files_loaded"] or stats["rows_loaded"] or stats["rows_expired"] or stats["rows_requalified"]) \
            and settings.cache_enabled and settings.redis_url:
        bump_data_version(settings.redis_url)  # cached API responses now get the new data
    return 1 if stats["files_failed"] or lost_lock else 0


def tidy_up(conn: psycopg.Connection, today: date, retention_months: int, stats: Counter) -> None:
    """After the load: once per database, rewrite raw stored as objects as values only, delete the
    older versions stored before each file pruned its own and apply the 2026-10-22 qualification rule
    to rows stored before it; then delete schemes that ended before the oldest month kept. Space freed
    is reused; `compact` gives it back to the disk."""
    rewritten = compact_raw(conn)
    if rewritten is not None:
        log.info("One-off clean-up: stored raw as values only for %d rows (names in raw_layouts)", rewritten)
    pruned = prune_all(conn)
    if pruned is not None:
        log.info("One-off clean-up: deleted %d older versions of schemes", pruned)
        stats["rows_replaced"] += pruned
    requalified = requalify(conn)
    if requalified is not None:
        log.info("One-off clean-up: Qualification = invoiced quantity for %d rows outside the GSR components",
                 requalified)
        stats["rows_requalified"] += requalified
    expired = 0
    if retention_months > 0:
        cutoff = retention_cutoff(today, retention_months)
        expired = delete_expired(conn, cutoff)
        if expired:
            log.info("Deleted %d schemes that ended before %s (LOADER_RETENTION_MONTHS=%d)",
                     expired, cutoff, retention_months)
        stats["rows_expired"] += expired
    if rewritten or pruned or requalified or expired:
        # Big change: mark space reusable, refresh statistics. PARALLEL 0: parallel index vacuuming needs
        # a shared memory segment of maintenance_work_mem, larger than dev's /dev/shm (failed 2026-10-21).
        conn.execute("VACUUM (ANALYZE, PARALLEL 0) scheme_records")


def ensure_schema(conn: psycopg.Connection) -> None:
    """Create missing tables and indexes. Idempotent, so each load run also picks up schema
    additions from newer releases (e.g. API 1's dealer index) without a separate init-db step."""
    conn.execute(SCHEMA_FILE.read_text())


def take_loader_lock(conn: psycopg.Connection, run_name: str, reclaim: bool = False, key: str = LOADER_LOCK_KEY) -> bool:
    """Take the loader lock. With reclaim (after a reconnect), first end this run's own stale
    session if it still holds the lock; another run's lock is never touched."""
    if conn.execute(LOADER_LOCK_SQL, (key,)).fetchone()["locked"]:
        return True
    for _ in range(LOCK_RECLAIM_TRIES if reclaim else 0):
        conn.execute(END_OWN_STALE_SESSIONS_SQL, (run_name,))
        time.sleep(1)  # the ended session releases its locks as it exits
        if conn.execute(LOADER_LOCK_SQL, (key,)).fetchone()["locked"]:
            log.info("Took back the loader lock from this run's previous session")
            return True
    return False


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return number


if __name__ == "__main__":
    sys.exit(main())
