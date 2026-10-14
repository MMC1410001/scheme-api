"""Keep the Postman collections in step with the code: every API endpoint must have a request."""
import json
from pathlib import Path

from app.main import app

POSTMAN = Path(__file__).resolve().parent.parent / "qa" / "postman"
COLLECTIONS = {
    "/v1/schemes/current-month": POSTMAN / "Scheme_API1_Current_Month.postman_collection.json",
    # "/v1/schemes/report": POSTMAN / "Scheme_API2_Report.postman_collection.json",  # API 2 is on hold
}


def requests_in(items):
    for item in items:
        if "item" in item:
            yield from requests_in(item["item"])
        else:
            yield item


def all_requests():
    for path in COLLECTIONS.values():
        yield from requests_in(json.loads(path.read_text())["item"])


def request_path(r):
    return "/" + "/".join(r["request"]["url"]["path"])


def test_every_endpoint_has_a_postman_request():
    covered = {(r["request"]["method"], request_path(r)) for r in all_requests()}
    missing = [
        f"{method.upper()} {path}"
        for path, methods in app.openapi()["paths"].items()
        for method in methods
        if (method.upper(), path) not in covered
    ]
    assert not missing, f"Add these to qa/postman/build_collection.py and rebuild: {missing}"


def test_every_request_has_tests():
    untested = [r["name"] for r in all_requests() if not r.get("event")]
    assert not untested


def test_each_collection_only_calls_its_own_api():
    for endpoint, path in COLLECTIONS.items():
        other = {request_path(r) for r in requests_in(json.loads(path.read_text())["item"])} & (set(COLLECTIONS) - {endpoint})
        assert not other, f"{path.name} calls {other}"


def test_no_collection_calls_api2_while_on_hold():
    assert not (POSTMAN / "Scheme_API2_Report.postman_collection.json").exists()
    assert "/v1/schemes/report" not in {request_path(r) for r in all_requests()}
