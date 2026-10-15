"""Build the API 1 Postman collection and the environments in this folder.

    Scheme_API1_Current_Month.postman_collection.json   API 1
    Local / Dev .postman_environment.json                      environments

Edit requests and tests here, not in the JSON, then regenerate the JSON files:
    python3 qa/postman/build_collection.py
tests/test_postman_collection.py fails if an API endpoint has no request in a collection.
"""
import json, uuid
from pathlib import Path

OUT = Path(__file__).resolve().parent
API1 = "{{baseUrl}}/v1/schemes/current-month"
COMPONENTS = ["EB SLAB 1", "EB SLAB 2", "EB SLAB 3", "VALUE", "LUXURY", "PREMIUM 01", "PREMIUM 02", "EB FULL MONTH"]

def script(lines):
    return [{"listen": "test", "script": {"type": "text/javascript", "exec": lines}}]

def req(name, method, url, body=None, tests=(), auth=None, description="", raw_body=None, headers=None, examples=()):
    item = {
        "name": name,
        "request": {
            "method": method,
            "header": headers if headers is not None else ([{"key": "Content-Type", "value": "application/json"}] if method == "POST" else []),
            "url": {"raw": url, "host": [url.split("/")[0]], "path": url.split("/")[1:]},
            "description": description,
        },
        "event": script(list(tests)),
    }
    if auth:
        item["request"]["auth"] = auth
    if body is not None or raw_body is not None:
        item["request"]["body"] = {"mode": "raw", "raw": raw_body if raw_body is not None else unquote_vars(json.dumps(body, indent=2)),
                                   "options": {"raw": {"language": "json"}}}
    if examples:
        item["response"] = list(examples)
    return item

def unquote_vars(raw):
    # {{month}} and {{year}} are sent as JSON numbers, so drop the quotes json.dumps puts around them.
    return raw.replace('"{{month}}"', "{{month}}").replace('"{{year}}"', "{{year}}")

MONTH_VARS = {"month": "{{month}}", "year": "{{year}}"}
# API 1 needs a month. Unless the environment sets month/year, use the current month in India time.
CURRENT_MONTH_SCRIPT = [
    'const india = new Date(Date.now() + 5.5 * 3600 * 1000);',
    'if (!pm.environment.get("month")) pm.collectionVariables.set("month", india.getUTCMonth() + 1);',
    'if (!pm.environment.get("year")) pm.collectionVariables.set("year", india.getUTCFullYear());',
]

def example(name, status, code, body, request_body, url=API1):
    return {
        "name": name,
        "originalRequest": {"method": "POST", "header": [{"key": "Content-Type", "value": "application/json"}],
                            "body": {"mode": "raw", "raw": json.dumps(request_body, indent=2), "options": {"raw": {"language": "json"}}},
                            "url": {"raw": url, "host": ["{{baseUrl}}"], "path": url.split("/")[1:]}},
        "status": status, "code": code, "_postman_previewlanguage": "json",
        "header": [{"key": "Content-Type", "value": "application/json"}],
        "body": json.dumps(body, indent=2),
    }

NO_AUTH = {"type": "noauth"}
status = lambda code: f'pm.test("Status is {code}", () => pm.response.to.have.status({code}));'
is_array = 'pm.test("Body is a JSON array", () => pm.expect(pm.response.json()).to.be.an("array"));'
empty_array = 'pm.test("Body is an empty array", () => pm.expect(pm.response.json()).to.eql([]));'
not_echoed = lambda value: f'pm.test("Input is not echoed back", () => pm.expect(pm.response.text()).to.not.include({json.dumps(value)}));'

ROW_CHECKS = [
    status(200), is_array,
    'const rows = pm.response.json();',
    'pm.test("Dealer has rows (needs sample data loaded)", () => pm.expect(rows.length).to.be.above(0));',
    'const FIELDS = ["Customer","FromDate","ToDate","ComponentDescription","Qualification","GsrB","RebateRate","ClearCut","ShortfallAmount","ShortfallPct"];',
    f'const COMPONENTS = {json.dumps(COMPONENTS)};',
    'pm.test("Every row has exactly the template fields", () => rows.forEach(r => pm.expect(Object.keys(r)).to.have.members(FIELDS)));',
    'pm.test("Only the 8 template components are returned", () => rows.forEach(r => pm.expect(COMPONENTS).to.include(r.ComponentDescription)));',
    'pm.test("Customer matches the requested dealer", () => rows.forEach(r => pm.expect(r.Customer).to.eql(pm.collectionVariables.get("dealerCode") || pm.environment.get("dealerCode"))));',
    'pm.test("Dates are YYYY-MM-DD and FromDate <= ToDate", () => rows.forEach(r => { pm.expect(r.FromDate).to.match(/^\\d{4}-\\d{2}-\\d{2}$/); pm.expect(r.ToDate).to.match(/^\\d{4}-\\d{2}-\\d{2}$/); pm.expect(r.FromDate <= r.ToDate).to.be.true; }));',
    'pm.test("Amounts are numbers (or null)", () => rows.forEach(r => ["Qualification","GsrB","RebateRate","ClearCut","ShortfallAmount","ShortfallPct"].forEach(k => pm.expect(r[k] === null || typeof r[k] === "number").to.be.true)));',
    'pm.test("Rows are ordered by ToDate, then template order", () => { for (let i = 1; i < rows.length; i++) { const a = rows[i-1], b = rows[i]; pm.expect(a.ToDate < b.ToDate || (a.ToDate === b.ToDate && COMPONENTS.indexOf(a.ComponentDescription) <= COMPONENTS.indexOf(b.ComponentDescription))).to.be.true; } });',
    'const prefix = pm.variables.get("year") + "-" + String(pm.variables.get("month")).padStart(2, "0");',
    'pm.test("Rows belong to the requested month", () => rows.forEach(r => pm.expect(r.FromDate.startsWith(prefix) || r.ToDate.startsWith(prefix)).to.be.true));',
    'pm.test("EB FULL MONTH Qualification is its GSR (GsrB), like the EB slabs", () => rows.filter(r => r.ComponentDescription === "EB FULL MONTH").forEach(r => pm.expect(r.Qualification).to.eql(r.GsrB)));',
    'pm.collectionVariables.set("api1RowCount", rows.length);',
]

SAMPLE_ROWS = [
    {"Customer": "6100000001", "FromDate": "2026-10-01", "ToDate": "2026-10-11", "ComponentDescription": "EB SLAB 1", "Qualification": 405232.86, "GsrB": 405232.86, "RebateRate": 0.75, "ClearCut": 0.0, "ShortfallAmount": 0.0, "ShortfallPct": 0.0},
    {"Customer": "6100000001", "FromDate": "2026-10-01", "ToDate": "2026-10-31", "ComponentDescription": "LUXURY", "Qualification": 315.1, "GsrB": 159292.8, "RebateRate": 1.0, "ClearCut": 1592.93, "ShortfallAmount": 84.9, "ShortfallPct": 21.23},
]

health = req("Health check", "GET", "{{baseUrl}}/health", auth=NO_AUTH,
    description="Liveness check. No API key needed.",
    tests=[status(200), 'pm.test("Body is {ok: true}", () => pm.expect(pm.response.json()).to.eql({ok: true}));'])

SAME_COUNT = 'pm.test("Same row count as the first request", () => pm.expect(pm.response.json().length).to.eql(Number(pm.collectionVariables.get("api1RowCount"))));'
api1 = [
    req("Monthly schemes - dealer code as string", "POST", API1, {"dealer_code": "{{dealerCode}}", **MONTH_VARS}, ROW_CHECKS,
        description=("Returns the dealer's rows for the 8 template components in the given month, ordered by end date. "
                     "`month` and `year` default to the current month (India time) unless the environment sets them."),
        examples=[example("200 - rows for a dealer", "OK", 200, SAMPLE_ROWS, {"dealer_code": "6100000001", "month": 10, "year": 2026}),
                  example("200 - dealer with no data that month", "OK", 200, [], {"dealer_code": "6199999999", "month": 10, "year": 2026})]),
    req("Monthly schemes - dealer code as number", "POST", API1,
        raw_body='{\n  "dealer_code": {{dealerCode}},\n  "month": {{month}},\n  "year": {{year}}\n}',
        tests=[status(200), is_array, SAME_COUNT], description="The code may be sent as a JSON number."),
    req("Monthly schemes - leading zeros ignored", "POST", API1, {"dealer_code": "000{{dealerCode}}", **MONTH_VARS},
        tests=[status(200), SAME_COUNT], description="0006100000001 and 6100000001 are the same dealer."),
    req("Monthly schemes - month and year as strings", "POST", API1,
        raw_body='{\n  "dealer_code": "{{dealerCode}}",\n  "month": "{{month}}",\n  "year": "{{year}}"\n}',
        tests=[status(200), SAME_COUNT], description='"10" and 10 are both accepted.'),
    req("Monthly schemes - month with no data returns []", "POST", API1, {"dealer_code": "{{dealerCode}}", "month": 1, "year": 2000},
        tests=[status(200), empty_array]),
    req("Monthly schemes - unknown dealer returns []", "POST", API1, {"dealer_code": "{{unknownDealerCode}}", **MONTH_VARS},
        tests=[status(200), empty_array]),
]
api1_month_negative = [
    req("Missing month -> 422", "POST", API1, {"dealer_code": "{{dealerCode}}", "year": "{{year}}"}, tests=[status(422)]),
    req("Missing year -> 422", "POST", API1, {"dealer_code": "{{dealerCode}}", "month": "{{month}}"}, tests=[status(422)]),
    req("Month 13 -> 422", "POST", API1, {"dealer_code": "{{dealerCode}}", "month": 13, "year": "{{year}}"}, tests=[status(422)]),
    req("Year 1999 -> 422", "POST", API1, {"dealer_code": "{{dealerCode}}", "month": "{{month}}", "year": 1999}, tests=[status(422)]),
    req("Boolean month -> 422", "POST", API1, {"dealer_code": "{{dealerCode}}", "month": True, "year": "{{year}}"}, tests=[status(422)]),
]


def validation(url, name, body=None, raw=None, echoed=None, desc=""):
    return req(name, "POST", url, body, raw_body=raw, description=desc,
               tests=[status(422), 'pm.test("Error lists what failed", () => pm.expect(pm.response.json().detail).to.be.an("array"));']
               + ([not_echoed(echoed)] if echoed else []))


def negative(url, base=None):
    """`base` holds the API's other required fields (API 1: month and year)."""
    b = lambda body: {**body, **(base or {})}
    return [
        validation(url, "Missing dealer_code -> 422", b({})),
        validation(url, "Empty dealer_code -> 422", b({"dealer_code": ""})),
        validation(url, "Whitespace dealer_code -> 422", b({"dealer_code": "   "})),
        validation(url, "Null dealer_code -> 422", b({"dealer_code": None})),
        validation(url, "Special characters -> 422", b({"dealer_code": "6100-0001"})),
        validation(url, "Longer than 20 characters -> 422", b({"dealer_code": "1" * 21})),
        validation(url, "Invalid JSON -> 422", raw='{"dealer_code": '),
        req("GET instead of POST -> 405", "GET", url, tests=[status(405)]),
        req("Unknown route -> 404", "POST", "{{baseUrl}}/v1/schemes/unknown", {"dealer_code": "{{dealerCode}}"}, tests=[status(404)]),
    ]


auth_skip = 'if (pm.collectionVariables.get("authEnabled") !== "true" && pm.environment.get("authEnabled") !== "true") { pm.test("SKIPPED: auth is not enabled on this server (authEnabled=false)", () => {}); return; }'


def security(url, base=None):
    b = lambda body: {**body, **(base or {})}
    return [
        req("No API key -> 401", "POST", url, b({"dealer_code": "{{dealerCode}}"}), auth=NO_AUTH,
            tests=["(function () {", auth_skip, status(401), "})();"], description="Only when the server has API_KEY set."),
        req("Wrong API key -> 401", "POST", url, b({"dealer_code": "{{dealerCode}}"}),
            auth={"type": "apikey", "apikey": [{"key": "key", "value": "X-API-Key"}, {"key": "value", "value": "wrong-key"}, {"key": "in", "value": "header"}]},
            tests=["(function () {", auth_skip, status(401), "})();"]),
        validation(url, "SQL injection payload is rejected", b({"dealer_code": "1' OR '1'='1"}), echoed="1' OR '1'='1"),
        validation(url, "Script payload is rejected and not reflected", b({"dealer_code": "<script>alert(1)</script>"}), echoed="<script>"),
        req("Oversized body -> 413", "POST", url, raw_body=json.dumps({"dealer_code": "1" * 5000}), tests=[status(413)]),
        req("Security headers are present", "POST", url, b({"dealer_code": "{{dealerCode}}"}), tests=[
            status(200),
            'pm.test("X-Content-Type-Options: nosniff", () => pm.expect(pm.response.headers.get("X-Content-Type-Options")).to.eql("nosniff"));',
            'pm.test("Cache-Control: no-store", () => pm.expect(pm.response.headers.get("Cache-Control")).to.include("no-store"));',
            'pm.test("HSTS max-age >= 1 year", () => pm.expect(Number((pm.response.headers.get("Strict-Transport-Security") || "").match(/max-age=(\\d+)/)[1])).to.be.at.least(31536000));',
            'pm.test("Content-Security-Policy present", () => pm.expect(pm.response.headers.get("Content-Security-Policy")).to.be.a("string"));',
            'pm.test("X-Frame-Options: DENY", () => pm.expect(pm.response.headers.get("X-Frame-Options")).to.eql("DENY"));',
            'pm.test("No X-Powered-By header", () => pm.expect(pm.response.headers.has("X-Powered-By")).to.be.false);',
        ]),
    ]


def collection(key, name, summary, functional_name, functional, url, variables, base=None, extra_negative=(), prerequest=()):
    return {
        "info": {
            "_postman_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"scheme-api/postman/{key}")),  # stable across rebuilds
            "name": name,
            "description": (
                f"{summary}\n\n"
                "1. Import this collection and an environment (Local or Dev) from the same folder.\n"
                "2. Pick the environment, set `apiKey` if the server requires one.\n"
                "3. Run a request, or run the whole collection (Runner) to execute every test.\n\n"
                "Full guide: qa/README.md and docs/API_GUIDE.md in the repository."
            ),
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "auth": {"type": "apikey", "apikey": [{"key": "key", "value": "X-API-Key"}, {"key": "value", "value": "{{apiKey}}"}, {"key": "in", "value": "header"}]},
        "event": ([{"listen": "prerequest", "script": {"type": "text/javascript", "exec": list(prerequest)}}] if prerequest else []) + [{"listen": "test", "script": {"type": "text/javascript", "exec": [
            'pm.test("Responds within 2 seconds", () => pm.expect(pm.response.responseTime).to.be.below(2000));',
            'pm.test("No stack trace or internal details in the body", () => { const t = pm.response.text(); ["Traceback", ".py\\"", "psycopg", "SELECT "].forEach(s => pm.expect(t).to.not.include(s)); });',
        ]}}],
        "variable": variables,
        "item": [
            {"name": "1. Health", "item": [health]},
            {"name": f"2. {functional_name}", "item": functional},
            {"name": "3. Negative cases", "item": [*extra_negative, *negative(url, base)]},
            {"name": "4. Security", "item": security(url, base)},
        ],
    }


COLLECTIONS = {
    "Scheme_API1_Current_Month.postman_collection.json": collection(
        "collection-api1", "Scheme API 1 - Monthly schemes",
        ("API 1: POST /v1/schemes/current-month with {dealer_code, month, year} returns the dealer's rows for the "
         "8 template components in that month. month/year default to the current month (India time); set `month` "
         "and `year` in the environment to test another month."),
        "Monthly schemes", api1, API1,
        [{"key": "api1RowCount", "value": ""}, {"key": "month", "value": ""}, {"key": "year", "value": ""}],
        base=MONTH_VARS, extra_negative=api1_month_negative, prerequest=CURRENT_MONTH_SCRIPT),
}
for filename, data in COLLECTIONS.items():
    (OUT / filename).write_text(json.dumps(data, indent=2) + "\n")

def env(name, base, key, auth, dealer, sample):
    return {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"scheme-api/postman/{name}")), "name": name, "_postman_variable_scope": "environment", "values": [
        {"key": "baseUrl", "value": base, "type": "default", "enabled": True},
        {"key": "apiKey", "value": key, "type": "secret", "enabled": True},
        {"key": "authEnabled", "value": auth, "type": "default", "enabled": True},
        {"key": "dealerCode", "value": dealer, "type": "default", "enabled": True},
        {"key": "unknownDealerCode", "value": "6199999999", "type": "default", "enabled": True},
        {"key": "sampleData", "value": sample, "type": "default", "enabled": True},
    ]}
(OUT / "Local.postman_environment.json").write_text(json.dumps(env("Scheme API - Local", "http://localhost:8000", "local-test-key", "true", "6100000001", "true"), indent=2) + "\n")
(OUT / "Dev.postman_environment.json").write_text(json.dumps(env("Scheme API - Dev", "https://<dev-host-to-be-confirmed>", "", "false", "<a real dealer code>", "false"), indent=2) + "\n")
print("wrote", OUT.name + "/: API 1 collection + Local and Dev environments")
