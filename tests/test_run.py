"""The loader reconnects after a dropped database connection and carries on."""
from collections import Counter
from contextlib import nullcontext

import psycopg
import pytest

from ingestion import run


@pytest.fixture
def no_wait(monkeypatch):
    monkeypatch.setattr(run, "RECONNECT_WAIT_SECONDS", 0)
    monkeypatch.setattr(run.psycopg, "connect", lambda *a, **k: nullcontext(object()))


def test_schemes_reconnects_and_resumes_after_connection_loss(monkeypatch, no_wait):
    calls = []

    def load(conn, year, month, reload):
        calls.append(reload)
        if len(calls) == 1:
            raise psycopg.OperationalError("the connection is closed")
        return Counter(files_loaded=3)

    monkeypatch.setattr(run, "load_schemes", load)
    assert run.main(["schemes", "--year", "2026", "--month", "9", "--reload"]) == 0
    assert calls == [True, True]  # --reload starts the month over after a reconnect


def test_gives_up_after_the_last_attempt(monkeypatch, no_wait):
    def load(*args):
        raise psycopg.OperationalError("server closed the connection")

    monkeypatch.setattr(run, "load_schemes", load)
    with pytest.raises(psycopg.OperationalError):
        run.main(["schemes", "--year", "2026", "--month", "9"])
