"""API 1's month rule against a real Postgres: a row belongs to every month its validity period
overlaps, whichever GCS month folder it was loaded from; the newest update of a component
and start date wins, even when SAP changed the end date or re-issued it under a new agreement.

Runs only when TEST_DATABASE_URL is set (see tests/test_load_db.py), in a throwaway schema.
"""
import os
import random
from contextlib import nullcontext
from datetime import date, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.repository import Repository
from ingestion.load import prune_all

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


def add(repo, folder, agreement, component, valid_from, valid_to, updated, gsr=1, dealer=DEALER, clear_cut=None):
    repo.pool.conn.execute(
        """INSERT INTO scheme_records (year, month, dealer_code, agreement, deal_suffix, component,
               valid_from, valid_to, qualification_basis, gsr_b, clear_cut, last_updated_at, source_file, raw)
           VALUES (%s, %s, %s, %s, '1', %s, %s, %s, 'GSR', %s, %s, %s, 'f.csv', %s)""",
        (*folder, dealer, agreement, component, valid_from, valid_to, gsr, clear_cut, updated, Jsonb({})))


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


def test_a_corrected_end_date_replaces_the_old_period(repo):
    # Seen on dev (Sept 2026): SAP sent EB SLAB 2 for 1-18 Sep, then corrected it to 1-19 Sep.
    add(repo, (2026, 9), "A-COR", "EB SLAB 2", date(2026, 9, 1), date(2026, 9, 18), datetime(2026, 9, 24), gsr=1)
    add(repo, (2026, 9), "A-COR", "EB SLAB 2", date(2026, 9, 1), date(2026, 9, 19), datetime(2026, 9, 26), gsr=2)
    rows = repo.month_schemes(DEALER, 2026, 9)
    assert [(r["gsr_b"], r["to_date"]) for r in rows] == [(2, date(2026, 9, 19))]


def test_the_newest_row_wins_whatever_folder_or_load_order(repo):
    # The newer correction sits in an older folder and the older row in a newer one.
    add(repo, (2026, 10), "A-COR", "VALUE", date(2026, 9, 1), date(2026, 9, 30), datetime(2026, 9, 20), gsr=1)
    add(repo, (2026, 9), "A-COR", "VALUE", date(2026, 9, 1), date(2026, 9, 29), datetime(2026, 9, 25), gsr=2)
    assert [r["gsr_b"] for r in repo.month_schemes(DEALER, 2026, 9)] == [2]


def test_an_old_end_date_does_not_bring_back_a_corrected_row(repo):
    # The correction ends in August, so the scheme is no longer in September: the old row, which
    # reached into September, must not be returned for September either.
    add(repo, (2026, 9), "A-COR", "LUXURY", date(2026, 8, 20), date(2026, 9, 5), datetime(2026, 9, 1), gsr=1)
    add(repo, (2026, 9), "A-COR", "LUXURY", date(2026, 8, 20), date(2026, 8, 31), datetime(2026, 9, 3), gsr=2)
    assert repo.month_schemes(DEALER, 2026, 9) == []
    assert [r["gsr_b"] for r in repo.month_schemes(DEALER, 2026, 8)] == [2]


def test_a_scheme_re_issued_under_a_new_agreement_returns_only_the_newest(repo):
    # Seen on dev: the same dealer's schemes sent again under a new agreement number, same dates.
    add(repo, (2026, 9), "0200002196", "EB SLAB 1", date(2026, 9, 1), date(2026, 9, 11), datetime(2026, 9, 24), gsr=1)
    add(repo, (2026, 9), "0200002248", "EB SLAB 1", date(2026, 9, 1), date(2026, 9, 12), datetime(2026, 9, 28), gsr=2)
    add(repo, (2026, 9), "0200002196", "VALUE", date(2026, 9, 1), date(2026, 9, 30), datetime(2026, 9, 24), gsr=3)
    add(repo, (2026, 9), "0200002248", "VALUE", date(2026, 9, 1), date(2026, 9, 30), datetime(2026, 9, 28), gsr=4)
    rows = repo.month_schemes(DEALER, 2026, 9)
    assert [(r["component_description"], r["gsr_b"], r["to_date"]) for r in rows] == [
        ("EB SLAB 1", 2, date(2026, 9, 12)), ("VALUE", 4, date(2026, 9, 30))]


def test_equal_update_times_pick_the_higher_agreement(repo):
    add(repo, (2026, 9), "0200002248", "VALUE", date(2026, 9, 1), date(2026, 9, 30), datetime(2026, 9, 28), gsr=2)
    add(repo, (2026, 9), "0200002196", "VALUE", date(2026, 9, 1), date(2026, 9, 30), datetime(2026, 9, 28), gsr=1)
    assert [r["gsr_b"] for r in repo.month_schemes(DEALER, 2026, 9)] == [2]


def test_same_agreement_with_another_start_date_is_a_separate_row(repo):
    add(repo, (2026, 9), "A-COR", "EB SLAB 1", date(2026, 9, 1), date(2026, 9, 11), datetime(2026, 9, 12), gsr=1)
    add(repo, (2026, 9), "A-COR", "EB SLAB 1", date(2026, 9, 12), date(2026, 9, 30), datetime(2026, 9, 13), gsr=2)
    assert [r["gsr_b"] for r in repo.month_schemes(DEALER, 2026, 9)] == [1, 2]


def test_every_component_comes_back_the_10_first_then_the_rest_by_name(repo):
    sep, upd = (date(2026, 9, 1), date(2026, 9, 30)), datetime(2026, 9, 20)
    for agreement, component in (("A1", "ROLLER"), ("A2", "LUXURY"), ("A3", "ALWD EB 2"), ("A4", "EB SLAB 1")):
        add(repo, (2026, 9), agreement, component, *sep, upd)
    assert [c for c, _ in components(repo.month_schemes(DEALER, 2026, 9))] == [
        "EB SLAB 1", "LUXURY", "ALWD EB 2", "ROLLER"]


def test_other_components_still_follow_end_date_order(repo):
    add(repo, (2026, 9), "A1", "ROLLER", date(2026, 9, 1), date(2026, 9, 10), datetime(2026, 9, 20))
    add(repo, (2026, 9), "A2", "LUXURY", date(2026, 9, 1), date(2026, 9, 30), datetime(2026, 9, 20))
    assert [c for c, _ in components(repo.month_schemes(DEALER, 2026, 9))] == ["ROLLER", "LUXURY"]


def test_rows_come_by_clear_cut_highest_first_and_empty_clear_cut_last(repo):
    sep, upd = (date(2026, 9, 1), date(2026, 9, 30)), datetime(2026, 9, 20)
    for agreement, component, clear_cut in (("A1", "EB SLAB 1", 0), ("A2", "ROLLER", 2951), ("A3", "LUXURY", None),
                                            ("A4", "VALUE", -120.5), ("A5", "SANDPAPER", 15000.25)):
        add(repo, (2026, 9), agreement, component, *sep, upd, clear_cut=clear_cut)
    rows = repo.month_schemes(DEALER, 2026, 9)
    assert [(r["component_description"], r["clear_cut"]) for r in rows] == [
        ("SANDPAPER", 15000.25), ("ROLLER", 2951), ("EB SLAB 1", 0), ("VALUE", -120.5), ("LUXURY", None)]


def test_equal_clear_cut_keeps_the_template_order(repo):
    """Most components have a ClearCut of 0: those keep the end date, template, then name order."""
    add(repo, (2026, 9), "A1", "ROLLER", date(2026, 9, 1), date(2026, 9, 30), datetime(2026, 9, 20), clear_cut=0)
    add(repo, (2026, 9), "A2", "LUXURY", date(2026, 9, 1), date(2026, 9, 30), datetime(2026, 9, 20), clear_cut=0)
    add(repo, (2026, 9), "A3", "EB SLAB 1", date(2026, 9, 1), date(2026, 9, 11), datetime(2026, 9, 20), clear_cut=0)
    add(repo, (2026, 9), "A4", "PREMIUM 01", date(2026, 9, 1), date(2026, 9, 30), datetime(2026, 9, 20), clear_cut=5)
    assert [c for c, _ in components(repo.month_schemes(DEALER, 2026, 9))] == [
        "PREMIUM 01", "EB SLAB 1", "LUXURY", "ROLLER"]


def test_equal_everything_else_picks_the_later_end_date(repo):
    add(repo, (2026, 9), "A1", "VALUE", date(2026, 9, 1), date(2026, 9, 18), datetime(2026, 9, 2, 10), gsr=1)
    repo.pool.conn.execute("UPDATE scheme_records SET deal_suffix = '2'")
    add(repo, (2026, 9), "A1", "VALUE", date(2026, 9, 1), date(2026, 9, 19), datetime(2026, 9, 2, 10), gsr=2)
    assert [r["gsr_b"] for r in repo.month_schemes(DEALER, 2026, 9)] == [2]


@pytest.mark.parametrize("seed", range(5))
def test_deleting_older_versions_never_changes_an_answer(repo, seed):
    """The loader deletes the versions this query would never return (ingestion/load.py)."""
    rng = random.Random(seed)
    conn = repo.pool.conn
    for n in range(300):
        start = rng.choice([date(2026, 8, 1), date(2026, 8, 20), date(2026, 9, 1), date(2026, 10, 1), None])
        end = None if start is None or rng.random() < 0.1 else start + timedelta(days=rng.choice([0, 17, 18, 30, 45]))
        conn.execute(
            """INSERT INTO scheme_records (year, month, dealer_code, agreement, deal_suffix, component,
                   valid_from, valid_to, qualification_basis, gsr_b, last_updated_at, source_file, raw)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'GSR', %s, %s, 'f.csv', %s) ON CONFLICT DO NOTHING""",
            (2026, rng.choice([8, 9, 10]), rng.choice([DEALER, "6100000002"]), rng.choice(["A1", "A2"]),
             rng.choice(["1", "2"]), rng.choice(["VALUE", "LUXURY", "EB SLAB 1", "ROLLER"]), start, end, n,
             datetime(2026, 9, 1, rng.randint(0, 2)), Jsonb({})))
    months = [(2026, m) for m in (7, 8, 9, 10, 11)]
    def answers():
        return {(d, *ym): repo.month_schemes(d, *ym) for d in (DEALER, "6100000002") for ym in months}

    before = answers()
    assert prune_all(conn) > 0
    after = answers()
    assert after == before
    assert sum(map(len, before.values())) > 0
