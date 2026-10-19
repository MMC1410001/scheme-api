"""Loader schedule inside the API: new SAP files reach the database without a separate cron job.

Once a day at LOADER_DAILY_AT (09:00, India time: SAP's files land around 04:30-05:00) the API runs
`python -m ingestion.run schemes` for the current month in a child process, so a slow or crashing
load never blocks or takes down request handling. In the first LOADER_CATCH_UP_DAYS days of a month
it also loads the previous month, so files of its last day are not missed.

If the run fails (GCS or database unreachable, a bad file, too slow) it is tried again up to
LOADER_RETRIES times, LOADER_RETRY_MINUTES apart; files already loaded are skipped, so nothing is
loaded twice.

With several workers (or several servers on one database) only one runs the load: each tries a
Postgres advisory lock on its own connection, and the holder runs it. If that worker dies its
connection closes, the lock is freed, and another worker takes over the next day. The loader keeps
its own lock too, so a manual run and a scheduled run never overlap.
"""
import asyncio
import logging
import sys
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import psycopg

from app.config import Settings

log = logging.getLogger("app.scheduler")
# uvicorn leaves the root logger at WARNING; every run is logged (INFO) so the server log shows it ran.
if not log.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    log.addHandler(_handler)
    log.setLevel(logging.INFO)
    log.propagate = False
SCHEDULER_LOCK_KEY = "acme-scheme-loader-scheduler"


def months_to_load(today: date, catch_up_days: int) -> list[tuple[int, int]]:
    """This month, plus the previous one during the first catch_up_days days of the month."""
    months = [(today.year, today.month)]
    if today.day <= catch_up_days:
        months.insert(0, (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1))
    return months


def next_run_at(now: datetime, daily_at: time) -> datetime:
    """The next daily_at strictly after now (now carries the schedule's timezone)."""
    run = now.replace(hour=daily_at.hour, minute=daily_at.minute, second=0, microsecond=0)
    return run if run > now else run + timedelta(days=1)


class LoaderScheduler:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.zone = ZoneInfo(settings.timezone)
        self._task: asyncio.Task | None = None
        self._leader_conn: psycopg.Connection | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="loader-scheduler")
        log.info("Loader schedule on: daily at %s %s", self.settings.loader_daily_at, self.settings.timezone)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._leader_conn is not None:
            await asyncio.to_thread(self._leader_conn.close)
            self._leader_conn = None

    async def _loop(self) -> None:
        daily_at = time.fromisoformat(self.settings.loader_daily_at)
        while True:
            now = datetime.now(self.zone)
            await asyncio.sleep((next_run_at(now, daily_at) - now).total_seconds())
            await self.run_daily()

    async def run_daily(self) -> None:
        """Today's load, tried again on failure. Returns quietly if another worker runs it."""
        tries = 1 + self.settings.loader_retries
        for attempt in range(1, tries + 1):
            try:
                leader = await asyncio.to_thread(self._is_leader)
                if leader is False:
                    return  # another worker runs the load
                if leader and await self.run_once():
                    return
            except asyncio.CancelledError:
                raise
            except Exception:  # never let one bad run end the schedule
                log.exception("Scheduled load failed")
            if attempt < tries:
                log.warning("Scheduled load: try %d of %d failed; trying again in %d min",
                            attempt, tries, self.settings.loader_retry_minutes)
                await asyncio.sleep(self.settings.loader_retry_minutes * 60)
        log.error("Scheduled load failed %d times; next try at %s tomorrow. Run "
                  "`python -m ingestion.run schemes` by hand to load today's files sooner",
                  tries, self.settings.loader_daily_at)

    def _is_leader(self) -> bool | None:
        """Hold the scheduler lock on a connection of our own; reconnect if that connection broke.
        None: the database could not be reached (a failure, tried again)."""
        conn = self._leader_conn
        if conn is not None:
            try:
                conn.execute("SELECT 1")
                return True
            except psycopg.Error:
                conn.close()
                self._leader_conn = None
        try:
            conn = psycopg.connect(self.settings.database_url, autocommit=True, connect_timeout=10,
                                   keepalives=1, keepalives_idle=30, application_name="scheme-loader-scheduler")
        except psycopg.Error as exc:
            log.error("Loader schedule: database unreachable (%s)", str(exc).strip())
            return None
        if conn.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (SCHEDULER_LOCK_KEY,)).fetchone()[0]:
            self._leader_conn = conn
            log.info("Loader schedule: this worker runs the scheduled loads")
            return True
        conn.close()  # another worker schedules
        return False

    async def run_once(self) -> bool:
        """Load each month due today; True when every load succeeded."""
        today = datetime.now(self.zone).date()
        codes = [await self._run_loader(year, month)
                 for year, month in months_to_load(today, self.settings.loader_catch_up_days)]
        return all(code == 0 for code in codes)

    async def _run_loader(self, year: int, month: int) -> int | None:
        args = [sys.executable, "-m", "ingestion.run", "schemes", "--year", str(year), "--month", str(month)]
        log.info("Scheduled load of %d-%02d started", year, month)
        process = await asyncio.create_subprocess_exec(*args)  # its log lines go to the API's output
        try:
            code = await asyncio.wait_for(process.wait(), self.settings.loader_timeout_minutes * 60)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            log.error("Scheduled load of %d-%02d took over %d min and was stopped",
                      year, month, self.settings.loader_timeout_minutes)
            return None
        except asyncio.CancelledError:  # API shutting down: don't leave the child running
            process.kill()
            await process.wait()
            raise
        if code == 0:
            log.info("Scheduled load of %d-%02d finished", year, month)
        else:
            log.error("Scheduled load of %d-%02d exited with code %d (see the ingestion log lines above)",
                      year, month, code)
        return code
