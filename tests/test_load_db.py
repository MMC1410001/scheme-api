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

from ingestion.load import SCHEME_FIELDS, SCHEME_KEY, is_loaded, save_scheme_file
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
    shortfall_pct, next_slab_rate, last_updated_at, source_file, raw)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (year, month, dealer_code, agreement, deal_suffix,
             (coalesce(valid_from, '-infinity'::date)), (coalesce(valid_to, '-infinity'::date))) DO UPDATE SET
    component = EXCLUDED.component, valid_from = EXCLUDED.valid_from, valid_to = EXCLUDED.valid_to,
    qualification_basis = EXCLUDED.qualification_basis, qualification = EXCLUDED.qualification,
    gsr_b = EXCLUDED.gsr_b, rebate_rate = EXCLUDED.rebate_rate, clear_cut = EXCLUDED.clear_cut,
    shortfall_amount = EXCLUDED.shortfall_amount, shortfall_pct = EXCLUDED.shortfall_pct,
    next_slab_rate = EXCLUDED.next_slab_rate, last_updated_at = EXCLUDED.last_updated_at, source_file = EXCLUDED.source_file, raw = EXCLUDED.raw
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
        "component": rng.choice(["EB SLAB 1", "VALUE", "VALUE %", "VALUE FA", "LUXURY", "EB FULL MONTH"]),
        # few periods, some without dates, so the same agreement and suffix appears with several periods
        "valid_from": rng.choice([date(2026, 8, 1), date(2026, 9, 1), None]),
        "valid_to": rng.choice([date(2026, 8, 31), date(2026, 9, 30), None]),
        "qualification_basis": "GSR", "qualification": Decimal(rng.randint(-1000, 100000)) / 100,
        "gsr_b": Decimal(n), "rebate_rate": None, "clear_cut": Decimal("0.00"),
        "shortfall_amount": Decimal("1.5"), "shortfall_pct": Decimal(rng.randint(0, 100)),
        "next_slab_rate": Decimal(rng.randint(0, 300)) / 100,
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
    same_key = [{**record(rng, i, "6100000001", "42", 0), "valid_from": date(2026, 9, 1), "valid_to": date(2026, 9, 30)}
                for i in range(3)]
    save_scheme_file(bulk, source(1), 2026, 9, same_key)
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


def test_upgrade_adds_next_slab_rate_and_fills_it_from_the_stored_sap_row(schemas):
    conn, _ = schemas
    conn.execute("ALTER TABLE scheme_records DROP COLUMN next_slab_rate")  # a table from before the column
    values = {"1": " 0.75 ", "2": "1.25-", "3": "1,000.50", "4": "", "5": None}
    for suffix, value in values.items():
        raw = {"comp_desc": "LUXURY"} if value is None else {"next_slab_rate": value}
        conn.execute(
            """INSERT INTO scheme_records (year, month, dealer_code, agreement, deal_suffix, component,
                   qualification_basis, last_updated_at, source_file, raw)
               VALUES (2026, 9, '6100000001', '200002155', %s, 'LUXURY', 'INVOICE_QTY', now(), 'f.csv', %s)""",
            (suffix, Jsonb(raw)),
        )
    for valid_from, value in ((date(2026, 8, 1), "0.5"), (date(2026, 9, 1), "0.9")):  # two periods, one old key
        conn.execute(
            """INSERT INTO scheme_records (year, month, dealer_code, agreement, deal_suffix, component, valid_from,
                   qualification_basis, last_updated_at, source_file, raw)
               VALUES (2026, 9, '6100000001', '200002155', '6', 'LUXURY', %s, 'INVOICE_QTY', now(), 'f.csv', %s)""",
            (valid_from, Jsonb({"next_slab_rate": value})),
        )
    conn.execute(SCHEMA_SQL)
    conn.execute(SCHEMA_SQL)  # running it again changes nothing
    rows = conn.execute("SELECT deal_suffix, valid_from, next_slab_rate FROM scheme_records").fetchall()
    assert {(r["deal_suffix"], r["valid_from"]): r["next_slab_rate"] for r in rows} == {
        ("1", None): Decimal("0.75"), ("2", None): Decimal("-1.25"), ("3", None): Decimal("1000.50"),
        ("4", None): None, ("5", None): None,
        ("6", date(2026, 8, 1)): Decimal("0.5"), ("6", date(2026, 9, 1)): Decimal("0.9"),
    }


def period(rng, n, valid_from, valid_to, minute=0, dealer="6100000001"):
    return {**record(rng, n, dealer, "42", minute), "valid_from": valid_from, "valid_to": valid_to}


def stored(conn):
    return conn.execute("SELECT valid_from, valid_to, gsr_b FROM scheme_records ORDER BY valid_from NULLS FIRST").fetchall()


def test_two_periods_of_one_agreement_in_one_folder_are_both_kept(schemas):
    """The September folder holds a late update of August's scheme and September's, same agreement and suffix."""
    _, bulk = schemas
    rng = random.Random(0)
    aug, sep = (date(2026, 8, 1), date(2026, 8, 31)), (date(2026, 9, 1), date(2026, 9, 30))
    save_scheme_file(bulk, source(1), 2026, 9, [period(rng, 1, *aug), period(rng, 2, *sep)])
    assert [r["gsr_b"] for r in stored(bulk)] == [1, 2]
    save_scheme_file(bulk, source(2), 2026, 9, [period(rng, 3, *aug, minute=5)])  # newer August update
    assert [r["gsr_b"] for r in stored(bulk)] == [3, 2]  # replaces August only
    save_scheme_file(bulk, source(3), 2026, 9, [period(rng, 4, *sep, minute=0)])  # same time as stored: wins
    save_scheme_file(bulk, source(4), 2026, 9, [period(rng, 5, *sep)])
    assert [r["gsr_b"] for r in stored(bulk)] == [3, 5]


@pytest.mark.parametrize("dates", [(None, None), (date(2026, 9, 1), None), (None, date(2026, 9, 30))])
def test_rows_with_empty_dates_are_still_one_row(schemas, dates):
    _, bulk = schemas
    rng = random.Random(0)
    save_scheme_file(bulk, source(1), 2026, 9, [period(rng, 1, *dates), period(rng, 2, *dates, minute=1)])
    save_scheme_file(bulk, source(2), 2026, 9, [period(rng, 3, *dates, minute=0)])  # older: ignored
    assert [r["gsr_b"] for r in stored(bulk)] == [2]


def test_a_file_with_bad_rows_is_recorded_as_failed_and_not_retried(schemas):
    _, bulk = schemas
    rng = random.Random(0)
    save_scheme_file(bulk, source(1), 2026, 9, [period(rng, 1, date(2026, 9, 1), date(2026, 9, 30))], rows_bad=2)
    entry = bulk.execute("SELECT status, rows_bad, row_count FROM ingested_files").fetchone()
    assert entry == {"status": "failed", "rows_bad": 2, "row_count": 1}
    assert bulk.execute("SELECT count(*) AS n FROM scheme_records").fetchone()["n"] == 1  # good rows are kept
    assert is_loaded(bulk, "scheme", source(1))  # same file version: not read again
    save_scheme_file(bulk, source(1), 2026, 9, [period(rng, 1, date(2026, 9, 1), date(2026, 9, 30))])  # --reload, clean
    assert bulk.execute("SELECT status, rows_bad FROM ingested_files").fetchone() == {"status": "loaded", "rows_bad": 0}


def test_an_unreadable_file_is_recorded_as_failed_with_no_rows_and_not_retried(schemas):
    """QA 2026-10-16 #5: a file that can't be read at all is recorded, so later runs skip it."""
    _, bulk = schemas
    save_scheme_file(bulk, source(1), 2026, 9, [], failed=True)
    entry = bulk.execute("SELECT status, rows_bad, row_count FROM ingested_files").fetchone()
    assert entry == {"status": "failed", "rows_bad": 0, "row_count": 0}
    assert bulk.execute("SELECT count(*) AS n FROM scheme_records").fetchone()["n"] == 0
    assert is_loaded(bulk, "scheme", source(1))


# The tables as they were before the dates joined the key and files got a status.
OLD_SCHEMA = """
CREATE TABLE ingested_files (
    id BIGSERIAL PRIMARY KEY, kind TEXT NOT NULL CHECK (kind = 'scheme'), gcs_path TEXT NOT NULL,
    generation TEXT NOT NULL, source_updated_at TIMESTAMPTZ NOT NULL, year INT, month INT,
    row_count INT NOT NULL DEFAULT 0, loaded_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE (kind, gcs_path)
);
CREATE TABLE scheme_records (
    year INT NOT NULL, month INT NOT NULL, dealer_code TEXT NOT NULL, agreement TEXT NOT NULL,
    deal_suffix TEXT NOT NULL,
    component TEXT NOT NULL CHECK (component IN ('EB SLAB 1', 'EB SLAB 2', 'EB SLAB 3', 'VALUE',
                                                 'LUXURY', 'PREMIUM 01', 'PREMIUM 02', 'EB FULL MONTH')),
    valid_from DATE, valid_to DATE,
    qualification_basis TEXT NOT NULL CHECK (qualification_basis IN ('GSR', 'INVOICE_QTY')),
    qualification NUMERIC, gsr_b NUMERIC, rebate_rate NUMERIC, clear_cut NUMERIC, shortfall_amount NUMERIC,
    shortfall_pct NUMERIC, next_slab_rate NUMERIC, last_updated_at TIMESTAMP NOT NULL, source_file TEXT NOT NULL,
    raw JSONB NOT NULL,
    PRIMARY KEY (year, month, dealer_code, agreement, deal_suffix)
);
"""


def test_upgrade_from_the_key_without_dates(schemas):
    conn, _ = schemas
    schema = conn.execute("SELECT current_schema() AS s").fetchone()["s"]
    conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
    conn.execute(f'CREATE SCHEMA "{schema}"')
    conn.execute(OLD_SCHEMA)
    rng = random.Random(0)
    old_row = {**period(rng, 1, date(2026, 8, 1), date(2026, 8, 31)), "component": "VALUE"}  # the old CHECK's list
    conn.execute(f"INSERT INTO scheme_records ({', '.join(SCHEME_FIELDS)}) VALUES ({', '.join(['%s'] * len(SCHEME_FIELDS))})",
                 [Jsonb(old_row[f]) if f == "raw" else old_row[f] for f in SCHEME_FIELDS])
    conn.execute("INSERT INTO ingested_files (kind, gcs_path, generation, source_updated_at) VALUES ('scheme', 'old.csv', '1', now())")

    conn.execute(SCHEMA_SQL)
    conn.execute(SCHEMA_SQL)  # running it again changes nothing

    assert conn.execute("SELECT count(*) AS n FROM pg_constraint WHERE conrelid = 'scheme_records'::regclass AND contype = 'p'"
                        ).fetchone()["n"] == 0
    assert conn.execute("SELECT to_regclass('scheme_records_key') IS NOT NULL AS e").fetchone()["e"]
    assert conn.execute("SELECT status, rows_bad FROM ingested_files").fetchone() == {"status": "loaded", "rows_bad": 0}
    save_scheme_file(conn, source(1), 2026, 9, [period(rng, 2, date(2026, 9, 1), date(2026, 9, 30))])  # same old key
    assert [r["gsr_b"] for r in stored(conn)] == [1, 2]
    # The old component CHECK is replaced, so the components added later load too.
    added = [{**period(rng, n, date(2026, 9, 1), date(2026, 9, 30)), "deal_suffix": f"1{n}", "component": c}
             for n, c in ((3, "VALUE %"), (4, "VALUE FA"))]
    save_scheme_file(conn, source(2), 2026, 9, added)
    assert [r["component"] for r in conn.execute(
        "SELECT component FROM scheme_records WHERE deal_suffix IN ('13', '14') ORDER BY component")] == ["VALUE %", "VALUE FA"]


def test_running_the_schema_again_adds_no_constraints(schemas):
    # Before PostgreSQL 13, ADD COLUMN IF NOT EXISTS ... CHECK added the CHECK again on every run.
    conn, _ = schemas
    count = "SELECT count(*) AS n FROM pg_constraint WHERE conrelid IN ('ingested_files'::regclass, 'scheme_records'::regclass)"
    before = conn.execute(count).fetchone()["n"]
    for _ in range(3):
        conn.execute(SCHEMA_SQL)
    assert conn.execute(count).fetchone()["n"] == before
