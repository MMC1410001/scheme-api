"""Request and response examples shown in Swagger (/docs) and in the qa/swagger specs.

Values are made up (they match scripts/make_sample_data.py), never client data. Request examples
whose key starts with "invalid_" must be rejected with 422; tests/test_swagger_specs.py checks both.
"""
from app.schemas import ErrorMessage, ValidationErrorResponse

API1_REQUESTS = {
    "valid": {"summary": "Dealer and month", "value": {"dealer_code": "6100000001", "month": 10, "year": 2026}},
    "numbers_as_strings": {
        "summary": "Dealer code as a number, month and year as strings",
        "value": {"dealer_code": 6100000001, "month": "10", "year": "2026"},
    },
    "leading_zeros": {
        "summary": "Leading zeros and spaces are ignored",
        "value": {"dealer_code": "  0006100000001 ", "month": 10, "year": 2026},
    },
    "invalid_month": {"summary": "Month 13 (422)", "value": {"dealer_code": "6100000001", "month": 13, "year": 2026}},
    "invalid_missing_year": {"summary": "Year missing (422)", "value": {"dealer_code": "6100000001", "month": 10}},
}

API2_REQUESTS = {
    "valid": {"summary": "Dealer code", "value": {"dealer_code": "6100000001"}},
    "number": {"summary": "Dealer code as a number", "value": {"dealer_code": 6100000001}},
    "invalid_characters": {"summary": "Characters other than letters and digits (422)", "value": {"dealer_code": "6100-0001"}},
}


def _row(component, to_date, qualification, gsr_b, rate, clear_cut=0.0, shortfall=0.0, shortfall_pct=0.0):
    return {"Customer": "6100000001", "FromDate": "2026-10-01", "ToDate": to_date, "ComponentDescription": component,
            "Qualification": qualification, "GsrB": gsr_b, "RebateRate": rate, "ClearCut": clear_cut,
            "ShortfallAmount": shortfall, "ShortfallPct": shortfall_pct}


API1_ROWS = {
    "rows": {
        "summary": "Dealer with data (3 of the 8 components shown)",
        "value": [
            _row("EB SLAB 1", "2026-10-11", 405232.86, 405232.86, 0.75),
            _row("LUXURY", "2026-10-31", 315.1, 159292.8, 1.0, 1592.93, 84.9, 21.23),
            _row("EB FULL MONTH", "2026-10-31", 405232.86, 405232.86, 2.25),
        ],
    },
    "empty": {"summary": "No data for this dealer or month", "value": []},
}

API2_ROWS = {
    "row": {
        "summary": "Dealer in the latest report (first columns shown; the real report has ~870)",
        "value": [{
            "zone": "Central", "region": "Bihar North", "depot_code": "1302", "customer": "6100000001",
            "customer_name": "Sample Paints", "value_slab_gsr_q_value": 125000.5, "value_slab_gsr_b_value": 125000.5,
            "value_slab_value_cn_pct": 0.4, "early_bird_gsr_q_eb_1": 336479.88, "early_bird_clear_cut": None,
            "luxury_volume_q_luxury": 315,
        }],
    },
    "empty": {"summary": "No report loaded, or the dealer is not in it", "value": []},
}


def _error(description, detail, **extra):
    return {"model": ErrorMessage, "description": description,
            "content": {"application/json": {"example": {"detail": detail}}}, **extra}


ERRORS = {
    401: _error("X-API-Key missing or wrong (only when the server has API_KEY set)", "Invalid or missing API key"),
    413: _error("Request body larger than MAX_BODY_BYTES", "Request body too large"),
    422: {
        "model": ValidationErrorResponse,
        "description": "The body failed validation",
        "content": {"application/json": {"example": {"detail": [
            {"loc": ["body", "dealer_code"], "msg": "Field required", "type": "missing"}]}}},
    },
    500: _error("Unexpected server error; details are logged, not returned", "Internal server error"),
    503: _error("No free database connection; retry after the Retry-After seconds", "Service busy, please retry",
                headers={"Retry-After": {"description": "Seconds to wait", "schema": {"type": "integer"}}}),
}


def ok(examples):
    return {200: {"description": "The dealer's rows (empty list when there are none)",
                  "content": {"application/json": {"examples": examples}}}}
