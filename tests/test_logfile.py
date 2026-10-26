"""Log files (app/logfile.py): one file per day in TIMEZONE, old ones deleted, the console log unchanged,
and a folder that can't be written doesn't stop the API."""
import logging
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.config import Settings
from app.logfile import OWN_HANDLER_LOGGERS, DailyFileHandler, log_to_file

IST = ZoneInfo("Asia/Kolkata")


def record(when: datetime, message: str = "hello", name: str = "app.test") -> logging.LogRecord:
    rec = logging.LogRecord(name, logging.WARNING, __file__, 1, message, None, None)
    rec.created = when.timestamp()
    return rec


@pytest.fixture
def handler(tmp_path):
    h = DailyFileHandler(tmp_path / "scheme-api.log", keep_days=3, zone="Asia/Kolkata")
    yield h
    h.close()


@pytest.fixture
def clean_loggers():
    """log_to_file adds handlers to shared loggers: take them off again after each test."""
    names = ("", *OWN_HANDLER_LOGGERS)
    before = {name: list(logging.getLogger(name).handlers) for name in names}
    yield
    for name in names:
        logger = logging.getLogger(name)
        for h in logger.handlers[:]:
            if h not in before[name]:
                logger.removeHandler(h)
                h.close()


def test_a_line_goes_to_its_days_file_with_the_india_time(tmp_path, handler):
    handler.emit(record(datetime(2026, 10, 23, 5, 0, 0, tzinfo=IST), "Scheduled load of 2026-10 started"))
    text = (tmp_path / "scheme-api-2026-10-23.log").read_text()
    assert text == "2026-10-23 05:00:00.000+05:30 WARNING app.test: Scheduled load of 2026-10 started\n"


def test_the_day_follows_the_timezone_not_utc(tmp_path, handler):
    # 23:30 UTC on the 22nd is 05:00 on the 23rd in India.
    handler.emit(record(datetime(2026, 10, 22, 23, 30, tzinfo=ZoneInfo("UTC"))))
    assert [f.name for f in tmp_path.iterdir()] == ["scheme-api-2026-10-23.log"]


def test_a_new_day_starts_a_new_file_and_old_files_are_deleted(tmp_path, handler):
    (tmp_path / "scheme-api-2026-10-19.log").write_text("old\n")
    (tmp_path / "other-2026-10-01.log").write_text("not ours\n")
    handler.emit(record(datetime(2026, 10, 20, 23, 59, tzinfo=IST), "day 1"))
    handler.emit(record(datetime(2026, 10, 23, 0, 1, tzinfo=IST), "day 3"))
    # keep_days=3 on the 23rd keeps the 21st, 22nd and 23rd.
    assert sorted(f.name for f in tmp_path.iterdir()) == ["other-2026-10-01.log", "scheme-api-2026-10-23.log"]
    assert "day 3" in (tmp_path / "scheme-api-2026-10-23.log").read_text()


def test_keep_days_zero_keeps_every_file(tmp_path):
    (tmp_path / "scheme-api-2020-01-01.log").write_text("old\n")
    h = DailyFileHandler(tmp_path / "scheme-api.log", keep_days=0, zone="Asia/Kolkata")
    h.emit(record(datetime(2026, 10, 23, 5, tzinfo=IST)))
    h.close()
    assert (tmp_path / "scheme-api-2020-01-01.log").exists()


def test_lines_are_appended_to_an_existing_days_file(tmp_path, handler):
    (tmp_path / "scheme-api-2026-10-23.log").write_text("from another worker\n")
    handler.emit(record(datetime(2026, 10, 23, 5, tzinfo=IST), "mine"))
    lines = (tmp_path / "scheme-api-2026-10-23.log").read_text().splitlines()
    assert lines[0] == "from another worker" and lines[1].endswith("mine")


def test_log_to_file_copies_app_scheduler_and_uvicorn_lines(tmp_path, clean_loggers, monkeypatch):
    # As uvicorn sets up its loggers: INFO, own handler, lines not passed on to the root.
    uvicorn_log = logging.getLogger("uvicorn")
    monkeypatch.setattr(uvicorn_log, "propagate", False)
    monkeypatch.setattr(uvicorn_log, "handlers", [logging.NullHandler()])
    monkeypatch.setattr(uvicorn_log, "level", logging.INFO)
    uvicorn_log.manager._clear_cache()
    log_to_file(Settings(_env_file=None, log_file=str(tmp_path / "logs" / "scheme-api.log")))
    log_to_file(Settings(_env_file=None, log_file=str(tmp_path / "logs" / "scheme-api.log")))  # once only
    logging.getLogger("app.scheduler").warning("Loader schedule off")
    logging.getLogger("uvicorn.error").info("Started server process [1]")
    logging.getLogger("app.cache").error("Redis unreachable")
    today = datetime.now(IST).date()
    lines = (tmp_path / "logs" / f"scheme-api-{today}.log").read_text().splitlines()
    assert [line.split(" ", 2)[2] for line in lines] == [
        "WARNING app.scheduler: Loader schedule off",
        "INFO uvicorn.error: Started server process [1]",
        "ERROR app.cache: Redis unreachable",
    ]


def test_without_log_file_nothing_changes(clean_loggers):
    before = list(logging.getLogger().handlers)
    log_to_file(Settings(_env_file=None))
    assert logging.getLogger().handlers == before


def test_a_folder_that_cant_be_written_is_a_warning_not_a_crash(tmp_path, clean_loggers, caplog):
    blocker = tmp_path / "a-file"
    blocker.write_text("")
    with caplog.at_level(logging.WARNING, "app"):
        log_to_file(Settings(_env_file=None, log_file=str(blocker / "scheme-api.log")))
    assert "can't be written" in caplog.text
    assert not any(isinstance(h, DailyFileHandler) for h in logging.getLogger().handlers)


def test_console_still_shows_warnings_when_the_file_is_added(tmp_path):
    """Python prints warnings to stderr only while the root logger has no handlers; adding the file
    handler must not make them disappear from the container's output. Fresh process: no pytest handlers."""
    code = (
        "import logging; from app.config import Settings; from app.logfile import log_to_file;"
        f"log_to_file(Settings(_env_file=None, log_file={str(tmp_path / 'x.log')!r}));"
        "logging.getLogger('app.cache').warning('Redis unreachable'); logging.getLogger('app').info('quiet')"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert result.stderr == "Redis unreachable\n"
