from ingestion.sources import month_prefixes

TEMPLATE = "sap-exports/{year}/{month:02d}/"


def test_two_digit_month_has_one_candidate():
    assert month_prefixes(TEMPLATE, 2026, 10) == ["sap-exports/2026/10/"]


def test_single_digit_month_tries_padded_then_unpadded():
    assert month_prefixes(TEMPLATE, 2027, 1) == ["sap-exports/2027/01/", "sap-exports/2027/1/"]


def test_flat_folder_template_is_used_as_is():
    assert month_prefixes("sap-exports/", 2026, 10) == ["sap-exports/"]
