"""API 2 report rules: which period and version a report file is, and how its values are stored.

Reports land in GCS as
    automated-generated-files/output/<year>/<month>/<name>_v<version>.xlsx
e.g. automated-generated-files/output/2024/01/automated-generated-files_output_2024_01_output_January_2024_grouped_v1.xlsx
"""
import math
import re
import struct
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

_PERIOD = re.compile(r"(?:^|/)(\d{4})/(\d{1,2})/[^/]+$")
_VERSION = re.compile(r"_v(\d+)\.[A-Za-z]+$")


@dataclass(frozen=True)
class ReportPeriod:
    year: int | None
    month: int | None
    version: int | None


def report_period(path: str) -> ReportPeriod:
    """Read the year/month folder and the _v<N> version from a report's path (None when absent)."""
    period = _PERIOD.search(path)
    year, month = (int(period[1]), int(period[2])) if period else (None, None)
    if month is not None and not 1 <= month <= 12:
        year = month = None
    version = _VERSION.search(path)
    return ReportPeriod(year, month, int(version[1]) if version else None)


def clean_value(value: Any) -> Any:
    """Make one cell JSON-safe, and drop the float32 noise the report generator leaves in numbers.

    The generator saves numbers as 32-bit floats, so 0.4 arrives as 0.4000000059604645 and
    66.86 as 66.86000061035156. Such values are rounded to 7 significant digits, keeping at
    least 2 decimals so amounts never lose paise. Numbers that are not float32 values are untouched.
    """
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        if value != 0 and _is_float32(value):
            digits_before_point = max(0, math.floor(math.log10(abs(value))) + 1)
            return round(value, max(2, 7 - digits_before_point))
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _is_float32(value: float) -> bool:
    try:
        return struct.unpack("f", struct.pack("f", value))[0] == value
    except OverflowError:
        return False
