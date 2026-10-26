"""Optional copy of the log in daily files (LOG_FILE), next to the container's own output.

LOG_FILE=/var/log/scheme-api/scheme-api.log writes scheme-api-2026-10-23.log in that folder: one
file per day in TIMEZONE, so "today at 05:00" is one file to open, and files older than
LOG_FILE_KEEP_DAYS are deleted. The API's workers and the loader it starts append to the same day's
file; each line is a single append, so lines from different processes don't mix.

The console output (what Loki collects) is unchanged: the file gets the same lines, with the date,
time and timezone on each.
"""
import logging
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import Settings

# Loggers that may not pass their lines on to the root logger (uvicorn's, as uvicorn sets them up,
# and app/scheduler.py's).
OWN_HANDLER_LOGGERS = ("uvicorn", "uvicorn.access", "app.scheduler")


class ZoneFormatter(logging.Formatter):
    def __init__(self, zone: ZoneInfo):
        super().__init__("%(asctime)s %(levelname)s %(name)s: %(message)s")
        self.zone = zone

    def formatTime(self, record, datefmt=None):
        return datetime.fromtimestamp(record.created, self.zone).isoformat(sep=" ", timespec="milliseconds")


class DailyFileHandler(logging.Handler):
    def __init__(self, path: str | Path, keep_days: int, zone: str):
        super().__init__()
        self.path = Path(path)
        self.keep_days = keep_days
        self.zone = ZoneInfo(zone)
        self.setFormatter(ZoneFormatter(self.zone))
        self._day: date | None = None
        self._fd: int | None = None

    def file_for(self, day: date) -> Path:
        return self.path.with_name(f"{self.path.stem}-{day.isoformat()}{self.path.suffix}")

    def open_day(self, day: date) -> None:
        """Switch to day's file (created if missing) and delete files past LOG_FILE_KEEP_DAYS."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.file_for(day), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        if self._fd is not None:
            os.close(self._fd)
        self._fd, self._day = fd, day
        if self.keep_days > 0:
            self.delete_old(day - timedelta(days=self.keep_days - 1))

    def delete_old(self, oldest_kept: date) -> None:
        pattern = re.compile(rf"{re.escape(self.path.stem)}-(\d{{4}}-\d{{2}}-\d{{2}}){re.escape(self.path.suffix)}")
        for file in self.path.parent.iterdir():
            match = pattern.fullmatch(file.name)
            if match and date.fromisoformat(match[1]) < oldest_kept:
                file.unlink(missing_ok=True)  # another process may have deleted it first

    def emit(self, record: logging.LogRecord) -> None:
        try:
            day = datetime.fromtimestamp(record.created, self.zone).date()
            self.acquire()
            try:
                if day != self._day:
                    self.open_day(day)
                os.write(self._fd, (self.format(record) + "\n").encode())
            finally:
                self.release()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        super().close()


def log_to_file(settings: Settings) -> None:
    """Also write the log to LOG_FILE's daily files. A folder that can't be written is a warning, not a
    reason to stop the API: the console log still has everything."""
    root = logging.getLogger()
    if not settings.log_file or any(isinstance(h, DailyFileHandler) for h in root.handlers):
        return
    handler = DailyFileHandler(settings.log_file, settings.log_file_keep_days, settings.timezone)
    try:
        handler.open_day(datetime.now(handler.zone).date())
    except OSError as exc:
        logging.getLogger("app").warning("LOG_FILE=%s can't be written (%s): logging to the console only",
                                         settings.log_file, exc)
        return
    if not root.handlers:
        # Without handlers Python prints warnings and errors to stderr on its own; adding the file
        # handler stops that, so keep printing them the same way.
        console = logging.StreamHandler()
        console.setLevel(logging.WARNING)
        root.addHandler(console)
    root.addHandler(handler)
    for name in OWN_HANDLER_LOGGERS:
        if not logging.getLogger(name).propagate:  # else its lines reach the root's handler already
            logging.getLogger(name).addHandler(handler)
