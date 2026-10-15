"""API 1's month rule against a real Postgres: a row belongs to every month its validity period
overlaps, whichever GCS month folder it was loaded from; the newest update of an agreement wins.

Runs only when TEST_DATABASE_URL is set (see tests/test_load_db.py), in a throwaway schema.
"""
import os
from contextlib import nullcontext
from datetime import date, datetime
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.repository import Repository

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set TEST_DATABASE_URL to run database tests")
SCHEMA_SQL = (Path(__file__).resolve().parent.parent / "sql" / "001_init.sql").read_text()
DEALER = "6100000001"


class OneConnectionPool:
    def __init__(self, conn):
        self.conn = conn

    def connection(self):
        return nullcontext(self.conn)


@pytest.fixture
def repo():
    schema = f"test_month_{os.getpid()}"
    conn = psycopg.connect(URL, autocommit=True, row_factory=dict_row)
    conn.execute(f'CREATE SCHEMA "{schema}"')
    conn.execute(f'SET search_path TO "{schema}"')
    conn.execute(SCHEMA_SQL)
    yield Repository(OneConnectionPool(conn))
    conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
    conn.close()


def add(repo, folder, agreement, component, valid_from, valid_to, updated, gsr=1, dealer=DEALER):
    repo.pool.conn.execute(
        """INSERT INTO scheme_records (year, month, dealer_code, agreement, deal_suffix, component,
               valid_from, valid_to, qualification_basis, gsr_b, last_updated_at, source_file, raw)
           VALUES (%s, %s, %s, %s, '1', %s, %s, %s, 'GSR', %s, %s, 'f.csv', %s)""",
        (*folder, dealer, agreement, component, valid_from, valid_to, gsr, updated, Jsonb({})))


def components(rows):
    return [(r["component_description"], r["from_date"].isoformat() if r["from_date"] else None) for r in rows]


def test_last_months_rows_in_this_months_folder_belong_to_last_month(repo):
    # A month folder can also hold SAP updates of the previous month's schemes.
    add(repo, (2026, 9), "A-AUG", "EB SLAB 1", date(2026, 8, 1), date(2026, 8, 11), datetime(2026, 9, 2))
    add(repo, (2026, 9), "A-SEP", "EB SLAB 1", date(2026, 9, 1), date(2026, 9, 11), datetime(2026, 9, 12))
    assert components(repo.month_schemes(DEALER, 2026, 9)) == [("EB SLAB 1", "2026-09-01")]
    assert components(repo.month_schemes(DEALER, 2026, 8)) == [("EB SLAB 1", "2026-08-01")]


def test_the_newest_update_of_an_agreement_wins_across_folders(repo):
    add(repo, (2026, 8), "A-AUG", "VALUE", date(2026, 8, 1), date(2026, 8, 31), datetime(2026, 8, 30), gsr=100)
    add(repo, (2026, 9), "A-AUG", "VALUE", date(2026, 8, 1), date(2026, 8, 31), datetime(2026, 9, 2), gsr=150)
    rows = repo.month_schemes(DEALER, 2026, 8)
    assert [r["gsr_b"] for r in rows] == [150]


def test_an_agreement_reused_for_another_month_keeps_both_rows(repo):
    add(repo, (2026, 8), "A-RUN", "VALUE", date(2026, 8, 1), date(2026, 8, 31), datetime(2026, 8, 30), gsr=100)
    add(repo, (2026, 9), "A-RUN", "VALUE", date(2026, 9, 1), date(2026, 9, 30), datetime(2026, 9, 29), gsr=200)
    # Same agreement and suffix again, for a period overlapping both months: all three are real rows.
    add(repo, (2026, 10), "A-RUN", "LUXURY", date(2026, 8, 20), date(2026, 9, 10), datetime(2026, 10, 1), gsr=1)
    assert [r["gsr_b"] for r in repo.month_schemes(DEALER, 2026, 8)] == [100, 1]
    assert [r["gsr_b"] for r in repo.month_schemes(DEALER, 2026, 9)] == [1, 200]


def test_a_scheme_spanning_months_is_in_each_month_it_overlaps(repo):
    add(repo, (2026, 8), "A-Q", "LUXURY", date(2026, 8, 15), date(2026, 9, 15), datetime(2026, 8, 15))
    for month in (8, 9):
        assert components(repo.month_schemes(DEALER, 2026, month)) == [("LUXURY", "2026-08-15")]
    assert repo.month_schemes(DEALER, 2026, 10) == []


def test_rows_without_dates_fall_back_to_their_folder_month(repo):
    add(repo, (2026, 9), "A-ND", "PREMIUM 01", None, None, datetime(2026, 9, 3))
    assert components(repo.month_schemes(DEALER, 2026, 9)) == [("PREMIUM 01", None)]
    assert repo.month_schemes(DEALER, 2026, 8) == []


def test_only_the_requested_dealer_and_template_order(repo):
    add(repo, (2026, 9), "A-1", "EB FULL MONTH", date(2026, 9, 1), date(2026, 9, 30), datetime(2026, 9, 3))
    add(repo, (2026, 9), "A-2", "VALUE", date(2026, 9, 1), date(2026, 9, 30), datetime(2026, 9, 3))
    add(repo, (2026, 9), "A-3", "EB SLAB 1", date(2026, 9, 1), date(2026, 9, 11), datetime(2026, 9, 3))
    add(repo, (2026, 9), "A-4", "VALUE", date(2026, 9, 1), date(2026, 9, 30), datetime(2026, 9, 3), dealer="6100000002")
    assert [c for c, _ in components(repo.month_schemes(DEALER, 2026, 9))] == ["EB SLAB 1", "VALUE", "EB FULL MONTH"]


def test_the_lookup_uses_the_dealer_index(repo):
    repo.pool.conn.execute("SET enable_seqscan = off")  # tiny table: force the planner to show its index choice
    plan = repo.pool.conn.execute(
        "EXPLAIN SELECT * FROM scheme_records WHERE dealer_code = %s AND valid_from <= %s", (DEALER, date(2026, 9, 30))
    ).fetchall()
    assert "scheme_records_dealer_valid_from" in " ".join(r["QUERY PLAN"] for r in plan)


def test_one_agreement_with_two_periods_in_one_folder_answers_each_month(repo):
    # Same agreement and suffix, both stored under the September folder (the key includes the period).
    add(repo, (2026, 9), "A-RUN", "EB SLAB 1", date(2026, 8, 1), date(2026, 8, 11), datetime(2026, 9, 2), gsr=8)
    add(repo, (2026, 9), "A-RUN", "EB SLAB 1", date(2026, 9, 1), date(2026, 9, 11), datetime(2026, 9, 12), gsr=9)
    assert [r["gsr_b"] for r in repo.month_schemes(DEALER, 2026, 8)] == [8]
    assert [r["gsr_b"] for r in repo.month_schemes(DEALER, 2026, 9)] == [9]
