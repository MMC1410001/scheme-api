"""Write one Swagger (OpenAPI 3.1) file per API, cut from the app's own spec.

    uv run python qa/swagger/build_specs.py

Open a file in Swagger Editor (https://editor.swagger.io, File > Import file) or any OpenAPI tool
(Postman, Insomnia, a code generator). tests/test_swagger_specs.py fails when these files are stale.
"""
import copy
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(OUT.parent.parent))  # run as a file, so make the app package importable

from app.main import app  # noqa: E402

SERVERS = [
    {"url": "http://localhost:8000", "description": "Local (README: Getting started)"},
    {"url": "https://{host}", "description": "Dev (host to be confirmed)",
     "variables": {"host": {"default": "dev-host-to-be-confirmed"}}},
]
HOW_TO = (
    "\n\n**Try it:** pick a server, click **Authorize** and enter the X-API-Key (not needed while the "
    "server has no API_KEY set), then **Try it out** on the request and pick an example.\n\n"
    "Full guide: docs/API_GUIDE.md in the repository."
)
SPECS = {
    "Scheme_API1_Current_Month.openapi.json": (
        "/v1/schemes/current-month", "Scheme API 1 - Monthly schemes",
        "POST {dealer_code, month, year} returns the dealer's rows for the 8 template components "
        "(EB SLAB 1-3, VALUE, LUXURY, PREMIUM 01/02, EB FULL MONTH) in that month.",
    ),
    # API 2 is on hold (2026-10-15): no spec is generated. Uncomment (and the endpoint) to restore.
    # "Scheme_API2_Report.openapi.json": (
    #     "/v1/schemes/report", "Scheme API 2 - Dealer report",
    #     "POST {dealer_code} returns the dealer's row from the latest generated report "
    #     "(newest month folder, then highest version).",
    # ),
}


def refs(node):
    """Every $ref under node."""
    if isinstance(node, dict):
        if "$ref" in node:
            yield node["$ref"]
        for value in node.values():
            yield from refs(value)
    elif isinstance(node, list):
        for value in node:
            yield from refs(value)


def build(full, path, title, summary):
    paths = {p: full["paths"][p] for p in (path, "/health")}
    schemas, todo = {}, list(refs(paths))
    while todo:  # keep only the schemas this API uses, following refs inside schemas too
        name = todo.pop().rsplit("/", 1)[-1]
        if name not in schemas:
            schemas[name] = full["components"]["schemas"][name]
            todo += refs(schemas[name])
    return {
        "openapi": full["openapi"],
        "info": {**full["info"], "title": title, "description": summary + HOW_TO},
        "servers": SERVERS,
        "paths": copy.deepcopy(paths),
        "components": {"schemas": dict(sorted(schemas.items())),
                       "securitySchemes": full["components"]["securitySchemes"]},
    }


def build_all():
    full = app.openapi()
    return {filename: build(full, *spec) for filename, spec in SPECS.items()}


if __name__ == "__main__":
    for filename, spec in build_all().items():
        (OUT / filename).write_text(json.dumps(spec, indent=2) + "\n")
    print("wrote", OUT.name + "/:", ", ".join(SPECS))
