from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app
from app.repository import get_repository

SCHEME_ROW = {
    "customer": "6100009999",
    "from_date": date(2026, 9, 1),
    "to_date": date(2026, 9, 30),
    "component_description": "LUXURY",
    "qualification": Decimal("250.5"),
    "gsr_b": Decimal("120000.8"),
    "rebate_rate": Decimal("1"),
    "clear_cut": Decimal("1200.01"),
    "shortfall_amount": Decimal("49.5"),
    "shortfall_pct": Decimal("16.5"),
    "next_slab_rate": Decimal("1.25"),
}


MONTH = {"month": 9, "year": 2026}


class FakeRepository:
    def __init__(self):
        self.calls = []

    def month_schemes(self, dealer_code, year, month):
        self.calls.append((dealer_code, year, month))
        return [SCHEME_ROW]


@pytest.fixture
def repo():
    fake = FakeRepository()
    app.dependency_overrides[get_repository] = lambda: fake
    yield fake
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    return TestClient(app)


def test_api1_returns_template_columns_for_the_requested_month(client, repo):
    response = client.post("/v1/schemes/current-month", json={"dealer_code": " 6100009999 ", "month": 9, "year": 2026})

    assert response.status_code == 200
    assert repo.calls == [("6100009999", 2026, 9)]
    assert response.json() == [
        {
            "Customer": "6100009999",
            "FromDate": "2026-09-01",
            "ToDate": "2026-09-30",
            "ComponentDescription": "LUXURY",
            "Qualification": 250.5,
            "GsrB": 120000.8,
            "RebateRate": 1.0,
            "ClearCut": 1200.01,
            "ShortfallAmount": 49.5,
            "ShortfallPct": 16.5,
            "NextSlabRate": 1.25,
        }
    ]


def test_leading_zeros_in_dealer_code_are_ignored(client, repo):
    client.post("/v1/schemes/current-month", json={"dealer_code": "0006100009999", **MONTH})
    assert repo.calls[0][0] == "6100009999"


@pytest.mark.parametrize("body", [{}, {"dealer_code": ""}, {"dealer_code": "   "}])
def test_dealer_code_is_required(client, repo, body):
    assert client.post("/v1/schemes/current-month", json={**body, **MONTH}).status_code == 422


def test_month_and_year_may_be_numeric_strings(client, repo):
    response = client.post("/v1/schemes/current-month", json={"dealer_code": "1001", "month": "09", "year": "2026"})
    assert response.status_code == 200
    assert repo.calls == [("1001", 2026, 9)]


@pytest.mark.parametrize("change", [
    {"month": None}, {"year": None},                       # missing
    {"month": 0}, {"month": 13}, {"month": -1},            # out of range
    {"year": 1999}, {"year": 2101}, {"year": 26},
    {"month": True}, {"year": False},                      # booleans are not numbers
    {"month": 9.5}, {"month": "Sept"}, {"month": ""}, {"month": [9]},
])
def test_invalid_month_or_year_returns_422(client, repo, change):
    body = {"dealer_code": "1001", **MONTH, **change}
    body = {k: v for k, v in body.items() if v is not None}
    response = client.post("/v1/schemes/current-month", json=body)
    assert response.status_code == 422
    assert repo.calls == []


def test_bearer_token_is_enforced_when_configured(client, repo):
    from tests.test_auth import SETTINGS, bearer

    app.dependency_overrides[get_settings] = lambda: SETTINGS
    assert client.post("/v1/schemes/current-month", json={"dealer_code": "1001", **MONTH}).status_code == 401
    ok = client.post("/v1/schemes/current-month", json={"dealer_code": "1001", **MONTH}, headers=bearer())
    assert ok.status_code == 200


@pytest.mark.parametrize("month, expected", [
    ("9", 9), ("09", 9), ('"9"', 9), ('"09"', 9), ("01", 1), ("10", 10), ("12", 12), ('"12"', 12),
])
def test_month_as_one_or_two_digits_is_accepted(client, repo, month, expected):
    raw = '{"dealer_code": "1001", "month": %s, "year": 2026}' % month
    response = client.post("/v1/schemes/current-month", content=raw, headers={"Content-Type": "application/json"})
    assert response.status_code == 200
    assert repo.calls == [("1001", 2026, expected)]


@pytest.mark.parametrize("month", [
    "009", '"009"', '"010"',                       # more than two digits
    "-9", "-09", '"-9"', '"+9"',                   # signs
    "9.0", '"9.0"', "09.5", "1e1",                 # decimals and exponents
    "0", "00", '"0"', '"00"', "13", '"13"',        # outside 1-12
    '" 9 "',
])
def test_month_other_than_1_to_12_in_one_or_two_digits_returns_422(client, repo, month):
    raw = '{"dealer_code": "1001", "month": %s, "year": 2026}' % month
    response = client.post("/v1/schemes/current-month", content=raw, headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert repo.calls == []


def test_padding_does_not_rescue_otherwise_bad_bodies(client, repo):
    raw = '{"dealer_code": "1001", "month": 09, "year": 2026'
    response = client.post("/v1/schemes/current-month", content=raw, headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert repo.calls == []


@pytest.mark.parametrize("year", ["2026", '"2026"', "2000", "2100"])
def test_year_as_four_digits_is_accepted(client, repo, year):
    raw = '{"dealer_code": "1001", "month": 9, "year": %s}' % year
    response = client.post("/v1/schemes/current-month", content=raw, headers={"Content-Type": "application/json"})
    assert response.status_code == 200
    assert repo.calls == [("1001", int(year.strip('"')), 9)]


@pytest.mark.parametrize("year", [
    "02026", '"02026"', '"26"', "26",              # not four digits
    "-2026", '"-2026"', '"+2026"',                 # signs
    "2026.0", '"2026.0"', "2.026e3",               # decimals and exponents
    '" 2026 "', "1999", "2101", '"2101"', "true",
])
def test_year_other_than_four_digits_2000_to_2100_returns_422(client, repo, year):
    raw = '{"dealer_code": "1001", "month": 9, "year": %s}' % year
    response = client.post("/v1/schemes/current-month", content=raw, headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert repo.calls == []
