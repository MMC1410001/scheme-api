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
}


MONTH = {"month": 9, "year": 2026}


class FakeRepository:
    def __init__(self):
        self.calls = []

    def month_schemes(self, dealer_code, year, month):
        self.calls.append((dealer_code, year, month))
        return [SCHEME_ROW]

    def latest_report(self):
        return {"id": 7, "gcs_path": "automated-generated-files/output/2026/09/r_v1.xlsx",
                "year": 2026, "month": 9, "version": 1, "columns": ["customer", "value_slab_gsr_q_value"]}

    def report_rows(self, report, dealer_code):
        self.calls.append((report["id"], dealer_code))
        return [{"customer": "1001", "value_slab_gsr_q_value": 1200}]


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
        }
    ]


def test_leading_zeros_in_dealer_code_are_ignored(client, repo):
    client.post("/v1/schemes/current-month", json={"dealer_code": "0006100009999", **MONTH})
    assert repo.calls[0][0] == "6100009999"


def test_api2_is_on_hold_and_not_served(client, repo):
    # API 2 is on hold (2026-10-15): its endpoint is commented out in app/routes.py.
    assert client.post("/v1/schemes/report", json={"dealer_code": "1001"}).status_code == 404
    assert "/v1/schemes/report" not in app.openapi()["paths"]
    assert repo.calls == []


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


def test_api_key_is_enforced_when_configured(client, repo):
    app.dependency_overrides[get_settings] = lambda: Settings(api_key="secret")

    assert client.post("/v1/schemes/current-month", json={"dealer_code": "1001", **MONTH}).status_code == 401
    ok = client.post("/v1/schemes/current-month", json={"dealer_code": "1001", **MONTH}, headers={"X-API-Key": "secret"})
    assert ok.status_code == 200
