"""Keep the Swagger specs in qa/swagger/ in step with the code, and their examples truthful."""
import importlib.util
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas import SchemeMonthRequest, SchemeRow

SWAGGER = Path(__file__).resolve().parent.parent / "qa" / "swagger"
SPECS = {
    "/v1/schemes/current-month": ("Scheme_API1_Current_Month.openapi.json", SchemeMonthRequest),
    # "/v1/schemes/report": ("Scheme_API2_Report.openapi.json", DealerRequest),  # API 2 is on hold
}


def load(filename):
    return json.loads((SWAGGER / filename).read_text())


def builder():
    spec = importlib.util.spec_from_file_location("build_specs", SWAGGER / "build_specs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_specs_are_up_to_date():
    for filename, built in builder().build_all().items():
        assert load(filename) == built, f"{filename} is stale: run uv run python qa/swagger/build_specs.py"


@pytest.mark.parametrize("endpoint", SPECS)
def test_each_spec_only_has_its_own_api(endpoint):
    paths = set(load(SPECS[endpoint][0])["paths"])
    assert paths == {endpoint, "/health"}


@pytest.mark.parametrize("endpoint", SPECS)
def test_every_ref_resolves(endpoint):
    spec = load(SPECS[endpoint][0])
    for ref in builder().refs(spec):
        assert ref.rsplit("/", 1)[-1] in spec["components"]["schemas"], ref


@pytest.mark.parametrize("endpoint", SPECS)
def test_request_examples_are_accepted_or_rejected_as_named(endpoint):
    filename, model = SPECS[endpoint]
    examples = load(filename)["paths"][endpoint]["post"]["requestBody"]["content"]["application/json"]["examples"]
    assert any(not name.startswith("invalid_") for name in examples)
    for name, example in examples.items():
        if name.startswith("invalid_"):
            with pytest.raises(ValidationError):
                model.model_validate(example["value"])
        else:
            model.model_validate(example["value"])


def test_api1_response_examples_match_the_row_model():
    endpoint = "/v1/schemes/current-month"
    examples = load(SPECS[endpoint][0])["paths"][endpoint]["post"]["responses"]["200"]["content"]["application/json"]["examples"]
    for example in examples.values():
        for row in example["value"]:
            assert SchemeRow.model_validate(row).model_dump(mode="json", by_alias=True) == row


@pytest.mark.parametrize("endpoint", SPECS)
def test_error_responses_are_documented(endpoint):
    responses = load(SPECS[endpoint][0])["paths"][endpoint]["post"]["responses"]
    assert {"200", "401", "413", "422", "500", "503"} <= set(responses)


def test_no_spec_for_api2_while_on_hold():
    assert not (SWAGGER / "Scheme_API2_Report.openapi.json").exists()
    for filename, _ in SPECS.values():
        assert "/v1/schemes/report" not in load(filename)["paths"]
