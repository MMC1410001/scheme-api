"""The bulk scheme upsert (COPY + one INSERT ... SELECT) gives exactly the result of upserting the
rows one by one, as the loader used to.

Needs a Postgres to write to, so it only runs when TEST_DATABASE_URL is set, e.g. the local one:
    TEST_DATABASE_URL=postgresql://acme:acme@localhost:5433/acme uv run pytest tests/test_load_db.py
Each run works in two throwaway schemas, dropped afterwards.
"""
import os
import random
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ingestion.load import SCHEME_FIELDS, SCHEME_KEY, save_scheme_file
from ingestion.run import take_loader_lock
from ingestion.sources import SourceFile

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set TEST_DATABASE_URL to run database tests")
SCHEMA_SQL = (Path(__file__).resolve().parent.parent / "sql" / "001_init.sql").read_text()

# The loader's previous, row-by-row upsert, written out in full (not built from the production
# constants, so a mistake there can't hide in both): the reference behaviour.
ROW_BY_ROW = """
INSERT INTO scheme_records (year, month, dealer_code, agreement, deal_suffix, component, valid_from,
    valid_to, qualification_basis, qualification, gsr_b, rebate_rate, clear_cut, shortfall_amount,
    shortfall_pct, last_updated_at, source_file, raw)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (year, month, dealer_code, agreement, deal_suffix) DO UPDATE SET
    component = EXCLUDED.component, valid_from = EXCLUDED.valid_from, valid_to = EXCLUDED.valid_to,
    qualification_basis = EXCLUDED.qualification_basis, qualification = EXCLUDED.qualification,
    gsr_b = EXCLUDED.gsr_b, rebate_rate = EXCLUDED.rebate_rate, clear_cut = EXCLUDED.clear_cut,
    shortfall_amount = EXCLUDED.shortfall_amount, shortfall_pct = EXCLUDED.shortfall_pct,
    last_updated_at = EXCLUDED.last_updated_at, source_file = EXCLUDED.source_file, raw = EXCLUDED.raw
WHERE EXCLUDED.last_updated_at >= scheme_records.last_updated_at
"""


def connect(schema, autocommit=True):
    conn = psycopg.connect(URL, autocommit=autocommit, row_factory=dict_row)
    conn.execute(f'SET search_path TO "{schema}"')
    return conn


@pytest.fixture
def schemas():
    names = [f"test_load_{os.getpid()}_{i}" for i in (1, 2)]
    conns = []
    for name in names:
        conn = psycopg.connect(URL, autocommit=True, row_factory=dict_row)
        conn.execute(f'CREATE SCHEMA "{name}"')
        conn.execute(f'SET search_path TO "{name}"')
        conn.execute(SCHEMA_SQL)
        conns.append(conn)
    yield conns
    for name, conn in zip(names, conns):
        conn.execute(f'DROP SCHEMA "{name}" CASCADE')
        conn.close()


def record(rng, n, dealer, deal_suffix, minute):
    return {
        "year": 2026, "month": 9, "dealer_code": dealer, "agreement": "200002155", "deal_suffix": deal_suffix,
        "component": rng.choice(["EB SLAB 1", "VALUE", "LUXURY", "EB FULL MONTH"]),
        "valid_from": date(2026, 9, 1), "valid_to": date(2026, 9, rng.randint(1, 30)),
        "qualification_basis": "GSR", "qualification": Decimal(rng.randint(-1000, 100000)) / 100,
        "gsr_b": Decimal(n), "rebate_rate": None, "clear_cut": Decimal("0.00"),
        "shortfall_amount": Decimal("1.5"), "shortfall_pct": Decimal(rng.randint(0, 100)),
        "last_updated_at": datetime(2026, 9, 20, 3, minute), "source_file": f"f{n}.csv",
        "raw": {"row": n, "note": "ünïcode, comma, \"quote\"\ttab\nnewline"},
    }


def source(n):
    return SourceFile(path=f"sap-exports/2026/09/f{n}.csv", generation=str(n),
                      updated_at=datetime(2026, 9, 20, tzinfo=timezone.utc), read=lambda: b"")


def table(conn):
    return conn.execute(
        f"SELECT {', '.join(SCHEME_FIELDS)} FROM scheme_records ORDER BY {', '.join(SCHEME_KEY)}"
    ).fetchall()


@pytest.mark.parametrize("seed", range(5))
def test_bulk_upsert_matches_row_by_row(schemas, seed):
    rng = random.Random(seed)
    row_by_row, bulk = schemas
    n = 0
    for file_no in range(4):  # later files update rows of earlier ones, sometimes with older data
        records = []
        for _ in range(rng.randint(0, 60)):
            n += 1
            # few dealers and suffixes, so keys repeat inside a file and across files; few
            # minutes, so "Last Updated" ties are common
            records.append(record(rng, n, f"61000000{rng.randint(1, 4)}", str(rng.randint(40, 42)), rng.randint(0, 3)))
        with row_by_row.transaction(), row_by_row.cursor() as cur:
            cur.executemany(ROW_BY_ROW, [tuple(Jsonb(r[f]) if f == "raw" else r[f] for f in SCHEME_FIELDS) for r in records])
        save_scheme_file(bulk, source(file_no), 2026, 9, records)
    assert table(bulk) == table(row_by_row)
    assert table(bulk), "the test should store some rows"


def stage_exists(conn):
    return conn.execute("SELECT to_regclass('pg_temp.scheme_stage') IS NOT NULL AS e").fetchone()["e"]


def test_staging_table_is_gone_after_each_file(schemas):
    _, bulk = schemas
    rng = random.Random(0)
    save_scheme_file(bulk, source(1), 2026, 9, [record(rng, i, "6100000001", "42", 0) for i in range(3)])
    assert not stage_exists(bulk)
    assert bulk.execute("SELECT count(*) AS n FROM scheme_records").fetchone()["n"] == 1  # same key 3 times


def test_same_result_on_a_connection_without_autocommit(schemas):
    row_by_row, bulk = schemas
    schema = bulk.execute("SELECT current_schema() AS s").fetchone()["s"]
    manual = connect(schema, autocommit=False)
    rng = random.Random(7)
    try:
        for file_no in range(3):
            records = [record(rng, 100 * file_no + i, f"61000000{rng.randint(1, 2)}", "42", rng.randint(0, 1)) for i in range(20)]
            manual.execute("SELECT 1")  # like is_loaded: opens the implicit transaction first
            save_scheme_file(manual, source(file_no), 2026, 9, records)
            with row_by_row.transaction(), row_by_row.cursor() as cur:
                cur.executemany(ROW_BY_ROW, [tuple(Jsonb(r[f]) if f == "raw" else r[f] for f in SCHEME_FIELDS) for r in records])
        manual.commit()
    finally:
        manual.close()
    assert table(bulk) == table(row_by_row)


def test_bad_row_fails_the_file_even_if_a_later_row_replaces_it(schemas):
    _, bulk = schemas
    rng = random.Random(0)
    bad, good = record(rng, 1, "6100000001", "42", 0), record(rng, 2, "6100000001", "42", 1)
    bad["component"] = "NOT A COMPONENT"
    with pytest.raises(psycopg.errors.CheckViolation):  # as the row-by-row upsert did
        save_scheme_file(bulk, source(1), 2026, 9, [bad, good])
    assert bulk.execute("SELECT count(*) AS n FROM scheme_records").fetchone()["n"] == 0


def test_table_change_during_a_session_is_picked_up(schemas):
    _, bulk = schemas
    rng = random.Random(0)
    save_scheme_file(bulk, source(1), 2026, 9, [record(rng, 1, "6100000001", "42", 0)])
    bulk.execute("ALTER TABLE scheme_records ALTER COLUMN source_file DROP NOT NULL")
    later = record(rng, 2, "6100000002", "42", 0)
    later["source_file"] = None
    save_scheme_file(bulk, source(2), 2026, 9, [later])  # failed before: the stage kept the old NOT NULL
    assert bulk.execute("SELECT count(*) AS n FROM scheme_records").fetchone()["n"] == 2


def test_only_one_loader_holds_the_lock():
    key = f"test-lock-{os.getpid()}"  # not the real loader's lock, which is database-wide
    first, second = (psycopg.connect(URL, autocommit=True, row_factory=dict_row) for _ in range(2))
    try:
        assert take_loader_lock(first, "run-a", key=key) is True
        assert take_loader_lock(second, "run-b", key=key) is False  # another run's lock is never taken
        first.close()  # the lock goes with the connection, even if the process dies
        assert take_loader_lock(second, "run-b", key=key) is True
    finally:
        first.close()
        second.close()


def test_reconnect_takes_back_this_runs_stale_lock():
    """After a network drop the old session can live on and hold the lock; the same run ends it."""
    key, run_name = f"test-lock-{os.getpid()}", f"test-run-{os.getpid()}"
    stale = psycopg.connect(URL, autocommit=True, row_factory=dict_row, application_name=run_name)
    other = psycopg.connect(URL, autocommit=True, row_factory=dict_row, application_name="someone-else")
    fresh = psycopg.connect(URL, autocommit=True, row_factory=dict_row, application_name=run_name)
    try:
        assert take_loader_lock(stale, run_name, key=key) is True
        assert take_loader_lock(fresh, run_name, key=key) is False  # first attempt: never reclaims
        assert take_loader_lock(fresh, run_name, reclaim=True, key=key) is True
        with pytest.raises(psycopg.OperationalError):
            stale.execute("SELECT 1")  # the stale session was ended
        other.execute("SELECT 1")  # other sessions are untouched
    finally:
        for conn in (stale, other, fresh):
            conn.close()


def test_new_column_with_a_default_still_loads(schemas):
    _, bulk = schemas
    bulk.execute("ALTER TABLE scheme_records ADD COLUMN loaded_at TIMESTAMPTZ NOT NULL DEFAULT now()")
    save_scheme_file(bulk, source(1), 2026, 9, [record(random.Random(0), 1, "6100000001", "42", 0)])
    assert bulk.execute("SELECT count(loaded_at) AS n FROM scheme_records").fetchone()["n"] == 1


def test_a_permanent_table_named_like_the_stage_is_never_used(schemas):
    _, bulk = schemas
    schema = bulk.execute("SELECT current_schema() AS s").fetchone()["s"]
    bulk.execute("CREATE TABLE scheme_stage (x INT)")
    bulk.execute("INSERT INTO scheme_stage VALUES (99)")
    bulk.execute(f'SET search_path TO "{schema}", pg_temp')  # temp tables searched last
    save_scheme_file(bulk, source(1), 2026, 9, [record(random.Random(0), 1, "6100000001", "42", 0)])
    assert bulk.execute(f'SELECT x FROM "{schema}".scheme_stage').fetchall() == [{"x": 99}]
    assert bulk.execute("SELECT count(*) AS n FROM scheme_records").fetchone()["n"] == 1
