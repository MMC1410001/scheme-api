"""Build the Postman collections (one per API) and the environments in this folder.

    Scheme_API1_Current_Month.postman_collection.json   API 1
    (Scheme_API2_Report.postman_collection.json         API 2 - on hold, not generated)
    Local / Dev .postman_environment.json                      shared by both collections

Edit requests and tests here, not in the JSON, then regenerate the JSON files:
    python3 qa/postman/build_collection.py
tests/test_postman_collection.py fails if an API endpoint has no request in a collection.
"""
import json, uuid
from pathlib import Path

OUT = Path(__file__).resolve().parent
API1 = "{{baseUrl}}/v1/schemes/current-month"
API2 = "{{baseUrl}}/v1/schemes/report"
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

sample_only = 'if (pm.environment.get("sampleData") !== "true") { pm.test("SKIPPED: needs sample data (sampleData=false)", () => {}); return; }'
API2_ROW_CHECKS = [
    status(200), is_array,
    'const rows = pm.response.json();',
    'pm.test("Exactly one row for the dealer (needs a report loaded)", () => pm.expect(rows.length).to.eql(1));',
    'const row = rows[0] || {};',
    'pm.test("customer matches the requested dealer", () => pm.expect(row.customer).to.eql(pm.environment.get("dealerCode")));',
    'pm.test("Columns start with zone, region, depot_code, customer, customer_name", () => pm.expect(Object.keys(row).slice(0, 5)).to.eql(["zone","region","depot_code","customer","customer_name"]));',
    'pm.test("Every key is snake_case", () => Object.keys(row).forEach(k => pm.expect(k).to.match(/^[a-z0-9]+(_[a-z0-9]+)*$/)));',
    'pm.test("Values are numbers, strings or null", () => Object.entries(row).forEach(([k, v]) => pm.expect(v === null || ["number","string"].includes(typeof v), k).to.be.true));',
    'pm.test("No float32 noise in numbers", () => Object.entries(row).forEach(([k, v]) => { if (typeof v === "number" && !Number.isInteger(v)) pm.expect(v === Number(v.toPrecision(7)) || v === Number(v.toFixed(2)), k + " = " + v).to.be.true; }));',
    'pm.collectionVariables.set("api2Row", JSON.stringify(rows));',
    '(function () {', sample_only,
    'pm.test("[sample data] v2 of the latest month is served (125000.5, not v1\'s 120000)", () => pm.expect(row.value_slab_gsr_q_value).to.eql(125000.5));',
    'pm.test("[sample data] float32 noise removed: 0.4 and 66.86", () => { pm.expect(row.value_slab_value_cn_pct).to.eql(0.4); pm.expect(row.value_slab_short_fall_pct_value).to.eql(66.86); });',
    'pm.test("[sample data] Empty cell is null", () => pm.expect(row.early_bird_clear_cut).to.eql(null));',
    '})();',
]
SAME_AS_API2 = 'pm.test("Same body as the string request", () => pm.expect(JSON.stringify(pm.response.json())).to.eql(pm.collectionVariables.get("api2Row")));'
SAMPLE_REPORT_ROW = {
    "zone": "Central", "region": "Bihar North", "depot_code": "1302", "customer": "6100000001",
    "customer_name": "Sample Paints", "value_slab_gsr_q_value": 125000.5, "value_slab_gsr_b_value": 125000.5,
    "value_slab_value_cn_pct": 0.4, "value_slab_value_clear_cut": 0, "value_slab_short_fall_value": 33431.36,
    "value_slab_short_fall_pct_value": 66.86, "early_bird_gsr_q_eb_1": 336479.88, "early_bird_clear_cut": None,
    "luxury_clear_cut": 1592.93,
}
api2 = [
    req("Dealer report - dealer code as string", "POST", API2, {"dealer_code": "{{dealerCode}}"}, API2_ROW_CHECKS,
        description=("The dealer's row from the latest generated report (newest month folder, then highest _v<N>). "
                     "Keys are the report's two header rows joined and snake_cased, in file order; empty cells are null. "
                     "The real report has ~870 columns; the example shows a few."),
        examples=[example("200 - the dealer's row (columns shortened)", "OK", 200, [SAMPLE_REPORT_ROW], {"dealer_code": "6100000001"}, API2),
                  example("200 - dealer not in the report", "OK", 200, [], {"dealer_code": "6199999999"}, API2)]),
    req("Dealer report - dealer code as number", "POST", API2, raw_body='{\n  "dealer_code": {{dealerCode}}\n}',
        tests=[status(200), SAME_AS_API2], description="The code may be sent as a JSON number."),
    req("Dealer report - leading zeros ignored", "POST", API2, {"dealer_code": "000{{dealerCode}}"},
        tests=[status(200), SAME_AS_API2], description="0006100000001 and 6100000001 are the same dealer."),
    req("Dealer report - unknown dealer returns []", "POST", API2, {"dealer_code": "{{unknownDealerCode}}"},
        tests=[status(200), empty_array]),
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
    # API 2 is on hold (2026-10-15): its collection is not generated. Uncomment (and the endpoint) to restore.
    # "Scheme_API2_Report.postman_collection.json": collection(
    #     "collection-api2", "Scheme API 2 - Dealer report",
    #     "API 2: POST /v1/schemes/report returns the dealer's row from the latest generated report.",
    #     "Dealer report", api2, API2, [{"key": "api2Row", "value": ""}]),
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
print("wrote", OUT.name + "/: API 1 collection + Local and Dev environments (API 2 on hold)")
