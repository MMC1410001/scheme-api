from datetime import datetime, timezone

import pytest

from ingestion.reports import ReportPeriod, clean_value, report_period
# from ingestion.run import _report_dealer_index, _report_rank  # API 2 is on hold
from ingestion.sources import SourceFile

REAL_PATH = (
    "automated-generated-files/output/2024/01/"
    "automated-generated-files_output_2024_01_output_January_2024_grouped_v1.xlsx"
)


def source(path, minute=0):
    return SourceFile(path, "1", datetime(2026, 10, 1, 10, minute, tzinfo=timezone.utc), lambda: b"")


def test_period_and_version_come_from_the_folder_and_file_name():
    assert report_period(REAL_PATH) == ReportPeriod(2024, 1, 1)
    assert report_period("automated-generated-files/output/2026/1/report_v12.xlsx") == ReportPeriod(2026, 1, 12)


@pytest.mark.parametrize("path", ["automated-generated-files/output/report.xlsx",
                                  "automated-generated-files/output/2026/13/report.xlsx"])
def test_period_is_unknown_outside_a_year_month_folder(path):
    assert report_period(path) == ReportPeriod(None, None, None)


# API 2 is on hold (2026-10-15): this tests the report loader, which is commented out.
# def test_latest_report_is_newest_month_then_highest_version_then_newest_upload():
#     files = [
#         source("automated-generated-files/output/2026/09/r_v3.xlsx", minute=59),
#         source("automated-generated-files/output/2026/10/r_v2.xlsx"),
#         source("automated-generated-files/output/2026/10/r_v10.xlsx"),  # v10 beats v2 (not text order)
#         source("automated-generated-files/output/2026/10/r_v1.xlsx", minute=30),
#         source("automated-generated-files/output/undated.xlsx", minute=59),
#         source("automated-generated-files/output/2025/12/r_v9.xlsx"),
#     ]
#     assert max(files, key=_report_rank).path.endswith("2026/10/r_v10.xlsx")


@pytest.mark.parametrize(("raw", "clean"), [
    (0.4000000059604645, 0.4),          # float32 noise from the report generator
    (66.86000061035156, 66.86),
    (0.8299999833106995, 0.83),
    (16568.640625, 16568.64),
    (1182950.75, 1182950.75),           # amounts keep at least 2 decimals
    (-37593.6015625, -37593.6),
    (0.1, 0.1),                         # not a float32 value: unchanged
    (1234567.891, 1234567.891),
    (0.0, 0.0),
    (float("nan"), None),
    (42, 42),
    ("Bihar North", "Bihar North"),
    (None, None),
])
def test_clean_value(raw, clean):
    assert clean_value(raw) == clean


# API 2 is on hold (2026-10-15): this tests the report loader, which is commented out.
# def test_dealer_column_must_exist():
#     assert _report_dealer_index(["zone", "customer"], "customer") == 1
#     with pytest.raises(ValueError, match="No 'customer' column"):
#         _report_dealer_index(["zone", "dealer"], "customer")
#     with pytest.raises(ValueError, match="no header"):
#         _report_dealer_index([], "customer")
