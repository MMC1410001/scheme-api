"""The loader: reconnects after a dropped database connection, prefetches files in parallel but
saves them in order, honours --limit, and never runs twice at once."""
import logging
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import datetime, timezone

import psycopg
import pytest

from app.config import Settings
from ingestion import run

CONNECTS = []


@pytest.fixture
def no_wait(monkeypatch):
    CONNECTS.clear()

    def connect(*args, **kwargs):
        CONNECTS.append(kwargs)
        return nullcontext(object())

    monkeypatch.setattr(run, "RECONNECT_WAIT_SECONDS", 0)
    monkeypatch.setattr(run.psycopg, "connect", connect)
    monkeypatch.setattr(run, "take_loader_lock", lambda *a, **k: True)
    monkeypatch.setattr(run, "ensure_schema", lambda conn: None)


def test_schemes_reconnects_and_resumes_after_connection_loss(monkeypatch, no_wait):
    calls = []

    def load(conn, year, month, reload, limit, stats, done):
        calls.append((reload, limit))
        if len(calls) == 1:
            raise psycopg.OperationalError("the connection is closed")
        stats["files_loaded"] += 3

    monkeypatch.setattr(run, "load_schemes", load)
    assert run.main(["schemes", "--year", "2026", "--month", "9", "--reload"]) == 0
    assert calls == [(True, None), (True, None)]  # --reload starts the month over after a reconnect
    assert all(kwargs["autocommit"] for kwargs in CONNECTS)  # each file is committed on its own


def test_gives_up_after_the_last_attempt(monkeypatch, no_wait):
    def load(*args):
        raise psycopg.OperationalError("server closed the connection")

    monkeypatch.setattr(run, "load_schemes", load)
    with pytest.raises(psycopg.OperationalError):
        run.main(["schemes", "--year", "2026", "--month", "9"])
    assert len(CONNECTS) == run.RECONNECT_ATTEMPTS


def test_limit_reaches_the_loader_and_counts_across_reconnects(monkeypatch, no_wait):
    limits = []

    def load(conn, year, month, reload, limit, stats, done):
        limits.append(limit)
        stats["files_loaded"] += 2  # two files were done before the connection dropped
        if len(limits) == 1:
            raise psycopg.OperationalError("the connection is closed")

    monkeypatch.setattr(run, "load_schemes", load)
    assert run.main(["schemes", "--year", "2026", "--month", "9", "--limit", "5"]) == 0
    assert limits == [5, 3]


def test_limit_already_reached_before_a_reconnect_loads_nothing_more(monkeypatch, no_wait):
    limits = []

    def load(conn, year, month, reload, limit, stats, done):
        limits.append(limit)
        stats["files_loaded"] += limit
        raise psycopg.OperationalError("the connection is closed")  # after the last file

    monkeypatch.setattr(run, "load_schemes", load)
    assert run.main(["schemes", "--year", "2026", "--month", "9", "--limit", "2"]) == 0
    assert limits == [2]


@pytest.mark.parametrize("value", ["0", "-1", "abc"])
def test_limit_must_be_a_positive_number(value, capsys):
    with pytest.raises(SystemExit):
        run.main(["schemes", "--limit", value])
    assert "--limit" in capsys.readouterr().err


def test_second_run_stops_while_another_holds_the_lock(monkeypatch, no_wait):
    monkeypatch.setattr(run, "take_loader_lock", lambda *a, **k: False)
    monkeypatch.setattr(run, "load_schemes", lambda *a: pytest.fail("must not load while another run holds the lock"))
    assert run.main(["schemes", "--year", "2026", "--month", "9"]) == 0


def test_prefetched_keeps_order_and_bounds_work_in_flight():
    started, running, peak = [], 0, 0
    lock = threading.Lock()

    def work(n):
        nonlocal running, peak
        with lock:
            started.append(n)
            running += 1
            peak = max(peak, running)
        time.sleep(0.02 * (5 - n % 5))  # later items often finish first
        with lock:
            running -= 1
        return n * 10

    with ThreadPoolExecutor(max_workers=8) as pool:
        out = []
        for item, future in run.prefetched(pool, range(12), work, ahead=3):
            assert len(started) <= item + 4  # the current item plus at most `ahead` more
            out.append((item, future.result()))
    assert out == [(n, n * 10) for n in range(12)]
    assert peak <= 4


def source(name):
    return run.SourceFile(path=f"sap-exports/2026/09/{name}.csv", generation="1",
                          updated_at=datetime(2026, 9, 1, tzinfo=timezone.utc), read=lambda: name.encode())


@pytest.fixture
def fake_month(monkeypatch):
    """Files a-e: b is already loaded, d can't be parsed, a is the slowest to read."""
    files = [source(n) for n in ("a", "b", "c", "d", "e")]
    loaded = {"sap-exports/2026/09/b.csv"}
    month = {"saved": [], "parsed": [], "running": 0, "peak": 0}
    lock = threading.Lock()
    monkeypatch.setattr(run, "list_files", lambda prefix: files)
    monkeypatch.setattr(run, "is_loaded", lambda conn, kind, f: f.path in loaded)

    def parse(content, path, year, month_, header_rows, log_rows=True):
        with lock:
            month["parsed"].append(content.decode())
            month["running"] += 1
            month["peak"] = max(month["peak"], month["running"])
        time.sleep(0.05 if content == b"a" else 0.01)
        with lock:
            month["running"] -= 1
        if content == b"d":
            raise run.UnreadableFile("ValueError: Missing columns ['customer']")
        return [{"file": content.decode()}] * 2, Counter(rows_other_components=1)

    monkeypatch.setattr(run, "parse_scheme_file", parse)
    monkeypatch.setattr(run, "save_scheme_file",
                        lambda conn, f, y, m, records, rows_bad=0, failed=False:
                        month["saved"].append(f.path.rsplit("/", 1)[1] + (" (failed)" if failed else "")))
    return month


def workers(monkeypatch, n):
    monkeypatch.setattr(run, "get_settings", lambda: Settings(_env_file=None, loader_workers=n))


def test_files_are_saved_in_listing_order_and_bad_files_are_counted(fake_month):
    stats = run.load_schemes(object(), 2026, 9)
    # b already loaded; d unreadable twice, so recorded as failed (not retried by later runs)
    assert fake_month["saved"] == ["a.csv", "c.csv", "d.csv (failed)", "e.csv"]
    assert fake_month["parsed"].count("d") == 2  # read once more before giving up
    assert stats == Counter(files_loaded=3, rows_loaded=6, files_skipped=1, files_failed=1, rows_other_components=3)


@pytest.mark.parametrize(("limit", "saved"), [(1, ["a.csv"]), (2, ["a.csv", "c.csv"])])
def test_limit_loads_only_the_first_new_files(fake_month, limit, saved):
    stats = run.load_schemes(object(), 2026, 9, limit=limit)
    assert fake_month["saved"] == saved
    assert fake_month["parsed"] == [s[0] for s in saved]  # nothing beyond the limit is even read
    assert stats == Counter(files_loaded=limit, rows_loaded=2 * limit, files_skipped=1, rows_other_components=limit)


def test_stats_are_kept_in_the_callers_counter(fake_month):
    stats = Counter(files_loaded=7)
    assert run.load_schemes(object(), 2026, 9, stats=stats) is stats
    assert stats["files_loaded"] == 10


@pytest.mark.parametrize("n", [1, 2])
def test_loader_workers_bounds_parallel_reads(fake_month, monkeypatch, n):
    workers(monkeypatch, n)
    run.load_schemes(object(), 2026, 9)
    assert fake_month["peak"] <= n
    assert fake_month["saved"] == ["a.csv", "c.csv", "d.csv (failed)", "e.csv"]


def test_zero_workers_still_loads(fake_month, monkeypatch):
    workers(monkeypatch, 0)
    run.load_schemes(object(), 2026, 9)
    assert fake_month["saved"] == ["a.csv", "c.csv", "d.csv (failed)", "e.csv"]


def test_lost_connection_stops_without_reading_further_files(fake_month, monkeypatch):
    workers(monkeypatch, 1)

    def save(conn, f, y, m, records, rows_bad=0, failed=False):
        raise psycopg.OperationalError("the connection is closed")

    monkeypatch.setattr(run, "save_scheme_file", save)
    with pytest.raises(psycopg.OperationalError):
        run.load_schemes(object(), 2026, 9)
    time.sleep(0.1)  # give any wrongly queued read time to start
    # Saving a failed, c was already being prefetched (1 worker ahead); d and e are never read.
    assert fake_month["parsed"] == ["a", "c"]
    assert not [t for t in threading.enumerate() if t.name.startswith("read") and t.is_alive()]


def test_reconnect_that_cannot_get_the_lock_back_fails_the_run(monkeypatch, no_wait, caplog):
    caplog.set_level(logging.INFO, logger="ingestion")
    attempts = []

    def lock(conn, run_name, reclaim=False):
        attempts.append(reclaim)
        return not reclaim  # first attempt gets it; after the reconnect another session holds it

    def load(conn, year, month, reload, limit, stats, done):
        stats["files_loaded"] += 2
        raise psycopg.OperationalError("the connection is closed")

    monkeypatch.setattr(run, "take_loader_lock", lock)
    monkeypatch.setattr(run, "load_schemes", load)
    assert run.main(["schemes", "--year", "2026", "--month", "9"]) == 1  # not a silent success
    assert attempts == [False, True]  # the reconnect tries to take back its own lock
    assert "Done: {'files_loaded': 2}" in caplog.text


def test_each_run_names_its_sessions(monkeypatch, no_wait):
    monkeypatch.setattr(run, "load_schemes", lambda *a: None)
    run.main(["schemes", "--year", "2026", "--month", "9"])
    run.main(["schemes", "--year", "2026", "--month", "9"])
    names = [kwargs["application_name"] for kwargs in CONNECTS]
    assert all(n.startswith("scheme-loader-") for n in names) and names[0] != names[1]


def test_counts_are_right_after_a_reconnect(fake_month, monkeypatch):
    """c's save loses the connection once: c is retried, and nothing is counted twice."""
    real_save = run.save_scheme_file
    lost = []

    def save(conn, f, y, m, records, rows_bad=0, failed=False):
        if f.path.endswith("c.csv") and not lost:
            lost.append(f.path)
            raise psycopg.OperationalError("the connection is closed")
        real_save(conn, f, y, m, records, rows_bad, failed)

    monkeypatch.setattr(run, "save_scheme_file", save)
    stats, done = Counter(), set()
    with pytest.raises(psycopg.OperationalError):
        run.load_schemes(object(), 2026, 9, stats=stats, done=done)
    run.load_schemes(object(), 2026, 9, stats=stats, done=done)  # what main() does after reconnecting
    assert fake_month["saved"] == ["a.csv", "c.csv", "d.csv (failed)", "e.csv"]
    assert stats == Counter(files_loaded=3, rows_loaded=6, files_skipped=1, files_failed=1, rows_other_components=3)


def test_lost_connection_does_not_wait_for_downloads_in_progress(fake_month, monkeypatch):
    workers(monkeypatch, 1)
    real_parse = run.parse_scheme_file

    def parse(content, path, year, month, header_rows, log_rows=True):
        if content == b"c":
            time.sleep(0.5)  # being prefetched while a is saved
        return real_parse(content, path, year, month, header_rows, log_rows)

    def save(conn, f, y, m, records, rows_bad=0, failed=False):
        raise psycopg.OperationalError("the connection is closed")

    monkeypatch.setattr(run, "parse_scheme_file", parse)
    monkeypatch.setattr(run, "save_scheme_file", save)
    started = time.monotonic()
    with pytest.raises(psycopg.OperationalError):
        run.load_schemes(object(), 2026, 9)
    assert time.monotonic() - started < 0.4  # the reconnect is not held up by c's download
    time.sleep(0.6)


@pytest.fixture
def one_file(monkeypatch):
    """One new file x; each download returns a new copy (b"copy1", b"copy2", ...) and parsing copy n
    gives reads_bad[n - 1] bad rows; 3 good rows each time."""
    state = {"downloads": 0, "reads": 0, "reads_bad": [], "saved": [], "parsed": []}

    def download():
        state["downloads"] += 1
        return b"copy%d" % state["downloads"]

    file = run.SourceFile(path="sap-exports/2026/09/x.csv", generation="1",
                          updated_at=datetime(2026, 9, 1, tzinfo=timezone.utc), read=download)
    monkeypatch.setattr(run, "list_files", lambda prefix: [file])
    monkeypatch.setattr(run, "is_loaded", lambda conn, kind, f: False)

    def parse(content, path, year, month, header_rows, log_rows=True):
        state["parsed"].append((content, log_rows))
        bad = state["reads_bad"][int(content.removeprefix(b"copy")) - 1]  # decided by the bytes read
        state["reads"] += 1
        return [{"row": n} for n in range(3)], Counter(rows_bad=bad) if bad else Counter()

    monkeypatch.setattr(run, "parse_scheme_file", parse)
    monkeypatch.setattr(run, "save_scheme_file",
                        lambda conn, f, y, m, records, rows_bad=0: state["saved"].append((len(records), rows_bad)))
    return state


def test_a_file_with_bad_rows_is_read_again_and_loads_if_the_second_read_is_clean(one_file):
    one_file["reads_bad"] = [2, 0]  # e.g. a damaged download the first time
    stats = run.load_schemes(object(), 2026, 9)
    assert one_file["downloads"] == 2  # downloaded again, not just parsed again
    assert one_file["parsed"] == [(b"copy1", False), (b"copy2", True)]  # rows logged only from the kept read
    assert one_file["saved"] == [(3, 0)]  # recorded as loaded
    assert stats == Counter(files_loaded=1, rows_loaded=3)


def test_a_file_still_bad_after_reading_it_again_keeps_its_good_rows_and_is_marked_failed(one_file, caplog):
    one_file["reads_bad"] = [2, 2]
    stats = run.load_schemes(object(), 2026, 9)
    assert one_file["downloads"] == 2  # read once more, not more
    assert one_file["parsed"] == [(b"copy1", False), (b"copy2", True)]
    assert one_file["saved"] == [(3, 2)]  # good rows saved; rows_bad makes the file "failed"
    assert stats == Counter(files_failed=1, rows_loaded=3, rows_bad=2)  # bad rows counted once, not per read
    assert "marked failed" in caplog.text


def test_a_clean_file_is_read_once(one_file):
    one_file["reads_bad"] = [0]
    run.load_schemes(object(), 2026, 9)
    assert one_file["downloads"] == 1


def test_good_rows_of_a_failed_file_refresh_the_cache_and_the_run_fails(monkeypatch, no_wait):
    bumped = []

    def load(conn, year, month, reload, limit, stats, done):
        stats.update(files_failed=1, rows_loaded=3, rows_bad=2)

    monkeypatch.setattr(run, "load_schemes", load)
    monkeypatch.setattr(run, "bump_data_version", bumped.append)
    monkeypatch.setattr(run, "get_settings",
                        lambda: Settings(_env_file=None, cache_enabled=True, redis_url="redis://example"))
    assert run.main(["schemes", "--year", "2026", "--month", "9"]) == 1
    assert bumped == ["redis://example"]


def test_each_bad_row_is_logged_once_even_when_the_file_is_read_twice(monkeypatch, caplog):
    csv = (b"customer,agreement,deal_suffix,comp_desc,valid_from,to,gsr_q,invoiced_quantity_q,gsr_b,rebate_rate,"
           b"clear_cut_cn,short_fall,short_fall_pct,next_slab_rate,last_updated_date,last_updated_time\n"
           b"6100000001,A1,10,VALUE,01/09/2026,30/09/2026,1,,1,0,0,0,0,0,02/09/2026,10:00:00\n"
           b"6100000001,A2,10,VALUE,not a date,30/09/2026,1,,1,0,0,0,0,0,02/09/2026,10:00:00\n")
    file = run.SourceFile(path="sap-exports/2026/09/x.csv", generation="1",
                          updated_at=datetime(2026, 9, 1, tzinfo=timezone.utc), read=lambda: csv)
    monkeypatch.setattr(run, "list_files", lambda prefix: [file])
    monkeypatch.setattr(run, "is_loaded", lambda conn, kind, f: False)
    monkeypatch.setattr(run, "save_scheme_file", lambda *a, **k: None)
    stats = run.load_schemes(object(), 2026, 9)
    assert stats == Counter(files_failed=1, rows_loaded=1, rows_bad=1)
    assert caplog.text.count("data row 2 skipped") == 1


# --- QA 2026-10-16 regressions ------------------------------------------------------------------

SAP_COLUMNS = ("Client,Agreement,Customer,Valid from,To,Deal Suffix,Comp desc,GSR (Q),Invoiced Quantity(Q),"
               "GSR (B),Rebate Rate,Clear Cut CN,Short Fall,Short Fall %,Next Slab Rate,Last Updated Date,"
               "Last Updated Time")
SAP_LINE = "100,0200000001,6100009999,01/10/2026,31/10/2026,06,LUXURY,1.00,2.000,3.00,1.00,0.00,0.00,0.00,1.25,01/10/2026,10:00:00"


def test_both_month_folders_are_loaded_when_both_exist(monkeypatch):
    """#2: with files in 2026/01/ and 2026/1/, the 1/ folder used to be skipped without a word."""
    listed = {"sap-exports/2026/01/": [run.SourceFile("sap-exports/2026/01/a.csv", "1", datetime.now(timezone.utc),
                                                      lambda: b"a")],
              "sap-exports/2026/1/": [run.SourceFile("sap-exports/2026/1/b.csv", "1", datetime.now(timezone.utc),
                                                     lambda: b"b")]}
    saved = []
    monkeypatch.setattr(run, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(run, "list_files", lambda prefix: listed.get(prefix, []))
    monkeypatch.setattr(run, "is_loaded", lambda conn, kind, f: False)
    monkeypatch.setattr(run, "parse_scheme_file", lambda content, *a, **k: ([{"f": content}], Counter()))
    monkeypatch.setattr(run, "save_scheme_file", lambda conn, f, *a, **k: saved.append(f.path))
    stats = run.load_schemes(object(), 2026, 1)
    assert saved == ["sap-exports/2026/01/a.csv", "sap-exports/2026/1/b.csv"]
    assert stats["files_loaded"] == 2


@pytest.mark.parametrize(("content", "reason"), [
    (f"{SAP_COLUMNS}\n{SAP_LINE.replace('LUXURY', 'LUXURY é')}\n".encode("cp1252"), "UnicodeDecodeError"),  # #5
    (f"{SAP_COLUMNS.replace('Customer', 'Dealer')}\n{SAP_LINE}\n".encode(), "Missing columns"),  # #5
    (b"Foo,Bar\n", "Missing columns"),  # #12: header only, wrong columns
    (b"", "Missing columns"),  # empty file
    (SAP_COLUMNS.replace(",", ";").encode() + b"\n", "Missing columns"),  # wrong delimiter
])
def test_a_file_that_cannot_be_read_as_a_whole_raises_unreadable_file(content, reason):
    with pytest.raises(run.UnreadableFile, match=reason):
        run.parse_scheme_file(content, "f.csv", 2026, 10, header_rows=1)


def test_a_header_only_file_with_the_right_columns_loads_no_rows():
    assert run.parse_scheme_file(f"{SAP_COLUMNS}\n".encode(), "f.csv", 2026, 10, 1) == ([], Counter())


def test_an_unreadable_file_is_read_again_then_recorded_as_failed(monkeypatch):
    """#5: it used to be left unrecorded, so every cron run downloaded it again and failed again."""
    reads, saved = [], []
    bad = run.SourceFile("sap-exports/2026/10/x.csv", "1", datetime.now(timezone.utc),
                         lambda: reads.append(1) or b"Foo,Bar\n")
    monkeypatch.setattr(run, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(run, "list_files", lambda prefix: [bad] if prefix.endswith("/10/") else [])
    monkeypatch.setattr(run, "is_loaded", lambda conn, kind, f: False)
    monkeypatch.setattr(run, "save_scheme_file",
                        lambda conn, f, y, m, records, rows_bad=0, failed=False: saved.append((f.path, records, failed)))
    stats = run.load_schemes(object(), 2026, 10)
    assert len(reads) == 2
    assert saved == [("sap-exports/2026/10/x.csv", [], True)]
    assert stats == Counter(files_failed=1, files_skipped=0)


def test_a_download_error_is_not_recorded_so_the_next_run_retries(monkeypatch):
    def broken():
        raise ConnectionError("GCS timed out")

    saved = []
    monkeypatch.setattr(run, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(run, "list_files", lambda prefix: [run.SourceFile("p/x.csv", "1", datetime.now(timezone.utc),
                                                                          broken)] if prefix.endswith("/10/") else [])
    monkeypatch.setattr(run, "is_loaded", lambda conn, kind, f: False)
    monkeypatch.setattr(run, "save_scheme_file", lambda *a, **k: saved.append(a))
    stats = run.load_schemes(object(), 2026, 10)
    assert saved == [] and stats["files_failed"] == 1


def test_the_default_month_is_the_current_month_in_india_time(monkeypatch, no_wait):
    """#14: TIMEZONE decides the loader's default month (31 Oct 20:00 UTC is already 1 Nov in India)."""
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 31, 20, 0, tzinfo=timezone.utc).astimezone(tz)

    months = []
    monkeypatch.setattr(run, "datetime", FrozenDatetime)
    monkeypatch.setattr(run, "load_schemes", lambda conn, year, month, *a: months.append((year, month)))
    monkeypatch.setattr(run, "get_settings", lambda: Settings(_env_file=None))
    run.main(["schemes"])
    monkeypatch.setattr(run, "get_settings", lambda: Settings(_env_file=None, timezone="UTC"))
    run.main(["schemes"])
    assert months == [(2026, 11), (2026, 10)]
