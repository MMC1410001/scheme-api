"""The API's loader schedule (app/scheduler.py): daily at 09:00 India time, which months, on by default
outside local, failed or hung runs are tried again, and only one worker runs the load.

The leader test needs a Postgres, so it only runs when TEST_DATABASE_URL is set (see test_load_db.py).
"""
import asyncio
import logging
import os
import sys
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app import config
from app.config import Settings
from app.scheduler import LoaderScheduler, months_to_load, next_run_at

IST = ZoneInfo("Asia/Kolkata")


def settings(**values) -> Settings:
    return Settings(_env_file=None, **values)


@pytest.mark.parametrize(("today", "expected"), [
    (date(2026, 10, 19), [(2026, 10)]),
    (date(2026, 11, 1), [(2026, 10), (2026, 11)]),
    (date(2026, 11, 3), [(2026, 10), (2026, 11)]),
    (date(2026, 11, 4), [(2026, 11)]),
    (date(2027, 1, 2), [(2026, 12), (2027, 1)]),
])
def test_months_to_load(today, expected):
    assert months_to_load(today, catch_up_days=3) == expected


def test_months_to_load_without_catch_up():
    assert months_to_load(date(2026, 11, 1), catch_up_days=0) == [(2026, 11)]


@pytest.mark.parametrize(("now", "expected"), [
    (datetime(2026, 10, 19, 0, 30, tzinfo=IST), datetime(2026, 10, 19, 9, 0, tzinfo=IST)),
    (datetime(2026, 10, 19, 8, 59, 59, tzinfo=IST), datetime(2026, 10, 19, 9, 0, tzinfo=IST)),
    (datetime(2026, 10, 19, 9, 0, tzinfo=IST), datetime(2026, 10, 20, 9, 0, tzinfo=IST)),
    (datetime(2026, 10, 31, 15, 0, tzinfo=IST), datetime(2026, 11, 1, 9, 0, tzinfo=IST)),
])
def test_next_run_is_the_next_9am(now, expected):
    assert next_run_at(now, time(9, 0)) == expected


def test_default_is_daily_at_9():
    assert settings().loader_daily_at == "09:00"


@pytest.mark.parametrize(("value", "expected"), [("9:00", "09:00"), (" 21:30 ", "21:30")])
def test_daily_at_is_normalised(value, expected):
    assert settings(loader_daily_at=value).loader_daily_at == expected


@pytest.mark.parametrize("value", ["9am", "25:00", "09:60", ""])
def test_bad_daily_at_stops_start_up(value):
    with pytest.raises(ValidationError):
        settings(loader_daily_at=value)


@pytest.mark.parametrize(("app_env", "enabled", "expected"), [
    ("local", None, False), ("dev", None, True), ("prod", None, True),
    ("dev", False, False), ("local", True, True),
])
def test_schedule_on_by_default_outside_local(monkeypatch, app_env, enabled, expected):
    monkeypatch.setattr(config, "APP_ENV", app_env)
    assert settings(loader_schedule_enabled=enabled).loader_schedule_on is expected


def test_empty_setting_means_default(monkeypatch):
    monkeypatch.setattr(config, "APP_ENV", "dev")
    monkeypatch.setenv("LOADER_SCHEDULE_ENABLED", "")
    assert settings().loader_schedule_on is True
    monkeypatch.setenv("LOADER_SCHEDULE_ENABLED", "false")
    assert settings().loader_schedule_on is False


def test_run_once_loads_each_month(monkeypatch):
    scheduler = LoaderScheduler(settings(loader_catch_up_days=31))  # today is always within 31 days
    ran = []

    async def fake_run(year, month):
        ran.append((year, month))
        return 0
    monkeypatch.setattr(scheduler, "_run_loader", fake_run)
    assert asyncio.run(scheduler.run_once()) is True
    assert len(ran) == 2 and ran[0] < ran[1]


def daily(monkeypatch, leader, outcomes, retries=3):
    """Run run_daily with retries 0 s apart; outcomes are what each run_once returns (or raises)."""
    scheduler = LoaderScheduler(settings(loader_retries=retries))
    scheduler.settings.loader_retry_minutes = 0
    monkeypatch.setattr(scheduler, "_is_leader", lambda: leader)
    runs = []

    async def fake_run_once():
        outcome = outcomes[len(runs)]
        runs.append(outcome)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
    monkeypatch.setattr(scheduler, "run_once", fake_run_once)
    asyncio.run(asyncio.wait_for(scheduler.run_daily(), 5))
    return runs


def test_successful_run_is_not_repeated(monkeypatch):
    assert daily(monkeypatch, True, [True]) == [True]


def test_failed_run_is_tried_again(monkeypatch):
    runs = daily(monkeypatch, True, [False, RuntimeError("GCS down"), True])
    assert len(runs) == 3 and runs[-1] is True


def test_gives_up_after_the_retries(monkeypatch, caplog):
    caplog_scheduler(monkeypatch)
    assert len(daily(monkeypatch, True, [False] * 10, retries=3)) == 4
    assert "failed 4 times" in caplog.text


def test_other_worker_runs_the_load(monkeypatch):
    assert daily(monkeypatch, False, [True]) == []


def test_database_down_counts_as_failure(monkeypatch, caplog):
    caplog_scheduler(monkeypatch)
    assert daily(monkeypatch, None, [True], retries=2) == []
    assert "failed 3 times" in caplog.text


def caplog_scheduler(monkeypatch):
    """The scheduler logger has its own handler and doesn't propagate; let caplog see it in tests."""
    monkeypatch.setattr(logging.getLogger("app.scheduler"), "propagate", True)


def fake_loader(monkeypatch, code: str):
    """Make the scheduled loader a small Python program instead of the real one."""
    real_exec = asyncio.create_subprocess_exec
    monkeypatch.setattr("app.scheduler.asyncio.create_subprocess_exec",
                        lambda *args: real_exec(sys.executable, "-c", code))


def test_loader_exit_code_is_reported(monkeypatch, caplog):
    caplog_scheduler(monkeypatch)
    fake_loader(monkeypatch, "raise SystemExit(1)")
    code = asyncio.run(LoaderScheduler(settings())._run_loader(2026, 10))
    assert code == 1
    assert "exited with code 1" in caplog.text


def test_hung_loader_is_stopped(monkeypatch, caplog):
    caplog_scheduler(monkeypatch)
    fake_loader(monkeypatch, "import time; time.sleep(60)")
    scheduler = LoaderScheduler(settings())
    scheduler.settings.loader_timeout_minutes = 0.01  # 0.6 s
    assert asyncio.run(scheduler._run_loader(2026, 10)) is None
    assert "was stopped" in caplog.text


def test_database_down_means_no_leader():
    scheduler = LoaderScheduler(settings(database_url="postgresql://nobody@127.0.0.1:1/none"))
    assert scheduler._is_leader() is None


URL = os.environ.get("TEST_DATABASE_URL")


@pytest.mark.skipif(not URL, reason="set TEST_DATABASE_URL to run database tests")
def test_only_one_worker_schedules():
    first, second = LoaderScheduler(settings(database_url=URL)), LoaderScheduler(settings(database_url=URL))
    try:
        assert first._is_leader() is True
        assert first._is_leader() is True  # keeps the lock on later days
        assert second._is_leader() is False
        first._leader_conn.close()  # the leading worker dies: its lock is freed
        assert second._is_leader() is True
    finally:
        for scheduler in (first, second):
            if scheduler._leader_conn is not None:
                scheduler._leader_conn.close()
