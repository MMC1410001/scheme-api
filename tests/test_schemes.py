from datetime import date, datetime
from decimal import Decimal

import pytest

from app.constants import COMPONENTS
from ingestion.excel import read_rows
from ingestion.schemes import check_columns, parse_date, parse_last_updated, parse_number, to_record

# The SAP export columns the code reads (the real export has more; extra columns are ignored).
SAP_HEADER = [
    "Client", "Agreement", "Customer", "Valid from", "To", "Deal Suffix", "Comp desc",
    "GSR (Q)", "Invoiced Quantity(Q)", "GSR (B)", "Rebate Rate", "Clear Cut CN",
    "Short Fall", "Short Fall %", "CN Date", "Last Updated Date", "Last Updated Time",
]


def sap_row(**values) -> dict:
    """One row as the CSV reader produces it, with made-up values in SAP's formats."""
    defaults = {
        "Client": "100", "Agreement": "0200000001", "Customer": "6100009999",
        "Valid from": "01/08/2026", "To": "31/08/2026", "Deal Suffix": "06", "Comp desc": "LUXURY",
        "GSR (Q)": "12000.50 ", "Invoiced Quantity(Q)": "25.000 ", "GSR (B)": "12000.50 ",
        "Rebate Rate": "3.50 ", "Clear Cut CN": "420.02 ", "Short Fall": "75.00 ",
        "Short Fall %": "75.00 ", "CN Date": "00/00/0000",
        "Last Updated Date": "15/08/2026", "Last Updated Time": "10:20:56",
    }
    defaults.update(values)
    line = ",".join(defaults[h] for h in SAP_HEADER)
    return read_rows(f"{','.join(SAP_HEADER)}\n{line}\n".encode(), "f.csv", header_rows=1)[0]


def test_sap_header_has_every_column_we_need():
    check_columns(sap_row().keys())


def test_check_columns_names_what_is_missing():
    row = sap_row()
    del row["clear_cut_cn"]
    with pytest.raises(ValueError, match="clear_cut_cn"):
        check_columns(row.keys())


def test_luxury_and_premium_qualify_on_invoiced_quantity():
    record = to_record(sap_row(), 2026, 8, "f.csv")
    assert record["component"] == "LUXURY"
    assert record["qualification_basis"] == "INVOICE_QTY"
    assert record["qualification"] == Decimal("25.000")
    assert record["gsr_b"] == Decimal("12000.50")
    assert record["clear_cut"] == Decimal("420.02")
    assert (record["valid_from"], record["valid_to"]) == (date(2026, 8, 1), date(2026, 8, 31))
    assert record["last_updated_at"] == datetime(2026, 8, 15, 10, 20, 56)
    assert (record["agreement"], record["deal_suffix"]) == ("0200000001", "06")


@pytest.mark.parametrize("component", ["EB SLAB 1", "EB SLAB 2", "EB SLAB 3", "VALUE"])
def test_eb_slabs_and_value_qualify_on_gsr_q(component):
    record = to_record(sap_row(**{"Comp desc": component, "GSR (Q)": "12000.50 ", "GSR (B)": "999.00 "}), 2026, 8, "f.csv")
    assert record["qualification_basis"] == "GSR"
    assert record["qualification"] == Decimal("12000.50")


def test_eb_full_month_takes_its_gsr_from_gsr_b_like_the_slabs():
    # SAP leaves GSR (Q) empty on EB FULL MONTH rows; on slab rows GSR (Q) equals GSR (B).
    record = to_record(sap_row(**{"Comp desc": "EB FULL MONTH", "GSR (Q)": "0.00 ", "GSR (B)": "405232.86 "}), 2026, 8, "f.csv")
    assert record["qualification_basis"] == "GSR"
    assert record["qualification"] == Decimal("405232.86")
    assert record["gsr_b"] == Decimal("405232.86")


@pytest.mark.parametrize("component", ["ALWD EB2", "ALWD VALUE SCH", "ADDN LUX INT", "ADDTNL PREMIUM 01", "ROLLER"])
def test_look_alike_components_are_dropped(component):
    assert to_record(sap_row(**{"Comp desc": component}), 2026, 8, "f.csv") is None


def test_all_eight_template_components_are_kept():
    for component in COMPONENTS:
        assert to_record(sap_row(**{"Comp desc": component}), 2026, 8, "f.csv") is not None


def test_missing_customer_is_an_error():
    with pytest.raises(ValueError, match="customer"):
        to_record(sap_row(Customer=""), 2026, 8, "f.csv")


def test_missing_last_updated_is_an_error():
    with pytest.raises(ValueError, match="Last Updated"):
        to_record(sap_row(**{"Last Updated Date": ""}), 2026, 8, "f.csv")


@pytest.mark.parametrize(
    "value, expected",
    [
        ("12000.50 ", Decimal("12000.50")),
        ("1234.56-", Decimal("-1234.56")),  # SAP puts the minus sign at the end
        ("1,234.50", Decimal("1234.50")),
        (-500, Decimal("-500")),
        ("", None),
        ("-", None),
    ],
)
def test_parse_number(value, expected):
    assert parse_number(value) == expected


def test_bad_number_is_an_error():
    with pytest.raises(ValueError):
        parse_number("12a")


def test_dates_are_day_first_and_zero_dates_are_empty():
    assert parse_date("05/08/2026") == date(2026, 8, 5)
    assert parse_date("00/00/0000") is None
    with pytest.raises(ValueError):
        parse_date("2026/31/31")


def test_parse_last_updated():
    assert parse_last_updated("16/08/2026", "03:29:47") == datetime(2026, 8, 16, 3, 29, 47)
