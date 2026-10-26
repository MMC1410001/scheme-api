"""Business rules for the SAP scheme export (sap-exports_*.csv)."""
from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.constants import qualification_basis, qualification_column
from app.normalize import normalize_dealer_code

# Our field -> flattened header name in the SAP export.
COLUMNS = {
    "dealer_code": "customer",
    "agreement": "agreement",
    "deal_suffix": "deal_suffix",
    "component": "comp_desc",
    "valid_from": "valid_from",
    "valid_to": "to",
    "gsr_q": "gsr_q",
    "invoiced_qty_q": "invoiced_quantity_q",
    "gsr_b": "gsr_b",
    "rebate_rate": "rebate_rate",
    "clear_cut": "clear_cut_cn",
    "shortfall_amount": "short_fall",
    "shortfall_pct": "short_fall_pct",
    "next_slab_rate": "next_slab_rate",
    "last_updated_date": "last_updated_date",
    "last_updated_time": "last_updated_time",
}

DATE_FORMATS = ("%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y")
EMPTY_DATES = {"00/00/0000", "00.00.0000"}


def check_columns(headers: Iterable[str]) -> None:
    """Fail loudly if the file doesn't have the columns we expect, instead of loading empty values."""
    headers = set(headers)
    missing = [name for name in COLUMNS.values() if name not in headers]
    if missing:
        raise ValueError(f"Missing columns {missing}. Columns in file: {sorted(headers)}")


def to_record(row: dict[str, Any], year: int, month: int, source_file: str) -> dict[str, Any] | None:
    """Convert one SAP row into a scheme_records row, or None for a row without a component."""

    def col(key: str) -> Any:
        return row.get(COLUMNS[key])

    component = _text(col("component"))
    if not component:
        return None

    dealer_code = normalize_dealer_code(col("dealer_code"))
    if not dealer_code:
        raise ValueError("missing customer code")
    agreement, deal_suffix = _text(col("agreement")), _text(col("deal_suffix"))
    if not agreement or not deal_suffix:
        raise ValueError("missing agreement or deal suffix")

    basis = qualification_basis(component)
    qualification = col(qualification_column(component))

    return {
        "year": year,
        "month": month,
        "dealer_code": dealer_code,
        "agreement": agreement,
        "deal_suffix": deal_suffix,
        "component": component,
        "valid_from": parse_date(col("valid_from")),
        "valid_to": parse_date(col("valid_to")),
        "qualification_basis": basis,
        "qualification": parse_number(qualification),
        "gsr_b": parse_number(col("gsr_b")),
        "rebate_rate": parse_number(col("rebate_rate")),
        "clear_cut": parse_number(col("clear_cut")),
        "shortfall_amount": parse_number(col("shortfall_amount")),
        "shortfall_pct": parse_number(col("shortfall_pct")),
        "next_slab_rate": parse_number(col("next_slab_rate")),
        "invoiced_qty": parse_number(col("invoiced_qty_q")),
        "last_updated_at": parse_last_updated(col("last_updated_date"), col("last_updated_time")),
        "source_file": source_file,
        "raw": to_jsonable(row),
    }


def parse_date(value: Any) -> date | None:
    if _is_blank(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if text in EMPTY_DATES:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognised date {value!r}")


def parse_last_updated(date_value: Any, time_value: Any) -> datetime:
    """SAP's last-update stamp decides which version of a row is the latest, so it is required."""
    day = parse_date(date_value)
    if day is None:
        raise ValueError("missing Last Updated Date")
    time_text = str(time_value or "00:00:00").strip() or "00:00:00"
    try:
        clock = datetime.strptime(time_text, "%H:%M:%S").time()
    except ValueError:
        raise ValueError(f"unrecognised Last Updated Time {time_value!r}") from None
    return datetime.combine(day, clock)


def parse_number(value: Any) -> Decimal | None:
    """Parse SAP numbers: "12345.67 ", "1,234.50", and negatives written as "1234.56-".
    NaN and Infinity are not amounts: they make the row bad rather than being stored."""
    if _is_blank(value) or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        number = Decimal(str(value))
    else:
        text = str(value).strip().replace(",", "").replace("%", "").strip()
        if text.upper() in {"-", "NA", "N/A"}:
            return None
        if text.endswith("-"):
            text = "-" + text[:-1]
        try:
            number = Decimal(text)
        except InvalidOperation:
            raise ValueError(f"not a number: {value!r}") from None
    if not number.is_finite():
        raise ValueError(f"not a number: {value!r}")
    return number


def to_jsonable(row: dict[str, Any]) -> dict[str, Any]:
    """Make a row safe to store as JSON (dates and decimals become strings)."""
    result = {}
    for key, value in row.items():
        if isinstance(value, (date, datetime)):
            value = value.isoformat()
        elif isinstance(value, Decimal):
            value = str(value)
        result[key] = value
    return result


def _text(value: Any) -> str | None:
    return None if _is_blank(value) else str(value).strip()


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())
