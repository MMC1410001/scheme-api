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

    def parse(content, path, year, month_, header_rows):
        with lock:
            month["parsed"].append(content.decode())
            month["running"] += 1
            month["peak"] = max(month["peak"], month["running"])
        time.sleep(0.05 if content == b"a" else 0.01)
        with lock:
            month["running"] -= 1
        if content == b"d":
            raise ValueError("Missing columns ['customer']")
        return [{"file": content.decode()}] * 2, Counter(rows_other_components=1)

    monkeypatch.setattr(run, "parse_scheme_file", parse)
    monkeypatch.setattr(run, "save_scheme_file", lambda conn, f, y, m, records: month["saved"].append(f.path.rsplit("/", 1)[1]))
    return month


def workers(monkeypatch, n):
    monkeypatch.setattr(run, "get_settings", lambda: Settings(_env_file=None, loader_workers=n))


def test_files_are_saved_in_listing_order_and_bad_files_are_counted(fake_month):
    stats = run.load_schemes(object(), 2026, 9)
    assert fake_month["saved"] == ["a.csv", "c.csv", "e.csv"]  # b already loaded, d unreadable
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
    assert fake_month["saved"] == ["a.csv", "c.csv", "e.csv"]


def test_zero_workers_still_loads(fake_month, monkeypatch):
    workers(monkeypatch, 0)
    run.load_schemes(object(), 2026, 9)
    assert fake_month["saved"] == ["a.csv", "c.csv", "e.csv"]


def test_lost_connection_stops_without_reading_further_files(fake_month, monkeypatch):
    workers(monkeypatch, 1)

    def save(conn, f, y, m, records):
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

    def save(conn, f, y, m, records):
        if f.path.endswith("c.csv") and not lost:
            lost.append(f.path)
            raise psycopg.OperationalError("the connection is closed")
        real_save(conn, f, y, m, records)

    monkeypatch.setattr(run, "save_scheme_file", save)
    stats, done = Counter(), set()
    with pytest.raises(psycopg.OperationalError):
        run.load_schemes(object(), 2026, 9, stats=stats, done=done)
    run.load_schemes(object(), 2026, 9, stats=stats, done=done)  # what main() does after reconnecting
    assert fake_month["saved"] == ["a.csv", "c.csv", "e.csv"]
    assert stats == Counter(files_loaded=3, rows_loaded=6, files_skipped=1, files_failed=1, rows_other_components=3)


def test_lost_connection_does_not_wait_for_downloads_in_progress(fake_month, monkeypatch):
    workers(monkeypatch, 1)
    real_parse = run.parse_scheme_file

    def parse(content, path, year, month, header_rows):
        if content == b"c":
            time.sleep(0.5)  # being prefetched while a is saved
        return real_parse(content, path, year, month, header_rows)

    def save(conn, f, y, m, records):
        raise psycopg.OperationalError("the connection is closed")

    monkeypatch.setattr(run, "parse_scheme_file", parse)
    monkeypatch.setattr(run, "save_scheme_file", save)
    started = time.monotonic()
    with pytest.raises(psycopg.OperationalError):
        run.load_schemes(object(), 2026, 9)
    assert time.monotonic() - started < 0.4  # the reconnect is not held up by c's download
    time.sleep(0.6)
