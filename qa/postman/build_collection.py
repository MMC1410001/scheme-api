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

def req(name, method, url, body=None, tests=(), auth=None, description="", raw_body=None, headers=None, examples=(),
        form=None, prerequest=()):
    """form: urlencoded body as [(key, value), ...] (the token endpoint); prerequest: this request's own script."""
    if headers is None:
        headers = ([{"key": "Content-Type", "value": "application/x-www-form-urlencoded" if form is not None else "application/json"}]
                   if method == "POST" else [])
    item = {
        "name": name,
        "request": {
            "method": method,
            "header": headers,
            "url": {"raw": url, "host": [url.split("/")[0]], "path": url.split("/")[1:]},
            "description": description,
        },
        "event": ([{"listen": "prerequest", "script": {"type": "text/javascript", "exec": list(prerequest)}}] if prerequest else [])
                 + script(list(tests)),
    }
    if auth:
        item["request"]["auth"] = auth
    if form is not None:
        item["request"]["body"] = {"mode": "urlencoded", "urlencoded": [{"key": k, "value": v, "type": "text"} for k, v in form]}
    elif body is not None or raw_body is not None:
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
    'pm.collectionVariables.set("monthPadded", String(pm.variables.get("month")).padStart(2, "0"));',
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
    'const FIELDS = ["Customer","FromDate","ToDate","ComponentDescription","Qualification","GsrB","RebateRate","ClearCut","ShortfallAmount","ShortfallPct","NextSlabRate"];',
    f'const COMPONENTS = {json.dumps(COMPONENTS)};',
    'pm.test("Every row has exactly the template fields", () => rows.forEach(r => pm.expect(Object.keys(r)).to.have.members(FIELDS)));',
    'pm.test("Only the 8 template components are returned", () => rows.forEach(r => pm.expect(COMPONENTS).to.include(r.ComponentDescription)));',
    'pm.test("Customer matches the requested dealer", () => rows.forEach(r => pm.expect(r.Customer).to.eql(pm.collectionVariables.get("dealerCode") || pm.environment.get("dealerCode"))));',
    'pm.test("Dates are YYYY-MM-DD and FromDate <= ToDate", () => rows.forEach(r => { pm.expect(r.FromDate).to.match(/^\\d{4}-\\d{2}-\\d{2}$/); pm.expect(r.ToDate).to.match(/^\\d{4}-\\d{2}-\\d{2}$/); pm.expect(r.FromDate <= r.ToDate).to.be.true; }));',
    'pm.test("Amounts are numbers (or null)", () => rows.forEach(r => ["Qualification","GsrB","RebateRate","ClearCut","ShortfallAmount","ShortfallPct","NextSlabRate"].forEach(k => pm.expect(r[k] === null || typeof r[k] === "number").to.be.true)));',
    'pm.test("Rows are ordered by ToDate, then template order", () => { for (let i = 1; i < rows.length; i++) { const a = rows[i-1], b = rows[i]; pm.expect(a.ToDate < b.ToDate || (a.ToDate === b.ToDate && COMPONENTS.indexOf(a.ComponentDescription) <= COMPONENTS.indexOf(b.ComponentDescription))).to.be.true; } });',
    'const y = Number(pm.variables.get("year")), m = Number(pm.variables.get("month"));',
    'const monthStart = y + "-" + String(m).padStart(2, "0") + "-01", monthEnd = new Date(Date.UTC(y, m, 0)).toISOString().slice(0, 10);',
    'pm.test("Every row is valid in the requested month (validity overlaps it)", () => rows.forEach(r => pm.expect(r.FromDate <= monthEnd && r.ToDate >= monthStart).to.be.true));',
    'pm.test("EB FULL MONTH Qualification is its GSR (GsrB), like the EB slabs", () => rows.filter(r => r.ComponentDescription === "EB FULL MONTH").forEach(r => pm.expect(r.Qualification).to.eql(r.GsrB)));',
    'pm.collectionVariables.set("api1RowCount", rows.length);',
]

SAMPLE_ROWS = [
    {"Customer": "6100000001", "FromDate": "2026-10-01", "ToDate": "2026-10-11", "ComponentDescription": "EB SLAB 1", "Qualification": 405232.86, "GsrB": 405232.86, "RebateRate": 0.75, "ClearCut": 0.0, "ShortfallAmount": 0.0, "ShortfallPct": 0.0, "NextSlabRate": 1.0},
    {"Customer": "6100000001", "FromDate": "2026-10-01", "ToDate": "2026-10-31", "ComponentDescription": "LUXURY", "Qualification": 315.1, "GsrB": 159292.8, "RebateRate": 1.0, "ClearCut": 1592.93, "ShortfallAmount": 84.9, "ShortfallPct": 21.23, "NextSlabRate": 1.5},
]

health = req("Health check", "GET", "{{baseUrl}}/health", auth=NO_AUTH,
    description="Liveness check. No token needed.",
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
    req("Monthly schemes - month zero-padded (09)", "POST", API1,
        raw_body='{\n  "dealer_code": "{{dealerCode}}",\n  "month": {{monthPadded}},\n  "year": {{year}}\n}',
        tests=[status(200), SAME_COUNT], description="Month as two digits, unquoted: 09 is accepted as 9 (for October onwards this sends 10, 11, 12)."),
    req("Monthly schemes - Content-Type in another case (Application/JSON) with month 09", "POST", API1,
        raw_body='{\n  "dealer_code": "{{dealerCode}}",\n  "month": {{monthPadded}},\n  "year": {{year}}\n}',
        headers=[{"key": "Content-Type", "value": "Application/JSON"}], tests=[status(200), SAME_COUNT],
        description="Media types are case-insensitive: a zero-padded month must work with Application/JSON too (QA 2026-10-16 #8)."),
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
] + [
    # Month is 1-12 as one or two digits; year is four digits. Raw bodies, so values go out exactly as written.
    req(f"{field.capitalize()} {value} -> 422", "POST", API1, tests=[status(422)],
        raw_body='{\n  "dealer_code": "{{dealerCode}}",\n  "month": %s,\n  "year": %s\n}' % ((value, "{{year}}") if field == "month" else ("{{month}}", value)))
    for field, value in [
        ("month", "-9"), ("month", "-09"), ("month", '"+9"'), ("month", "9.0"), ("month", '"9.0"'),
        ("month", "009"), ("month", '"009"'), ("month", "00"), ("month", '" 9 "'),
        ("month", "010"), ("month", "0010"), ("month", '"0010"'), ("month", '"012"'),
        ("year", "02026"), ("year", '"26"'), ("year", '"+2026"'), ("year", "2026.0"), ("year", '" 2026 "'),
    ]
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
skipped = lambda *lines: ["(function () {", auth_skip, *lines, "})();"]

TOKEN_URL = "{{baseUrl}}/v1/auth/token"
# Collection pre-request: one token per run, reused until 60 s before it expires. POST /v1/auth/token
# has its own low per-IP limit (TOKEN_RATE_LIMIT_PER_MINUTE, default 30), so never one per request.
TOKEN_SCRIPT = [
    '(function () {',
    '  if (String(pm.variables.get("authEnabled")) === "false") return; // server has auth off',
    '  const clientId = pm.variables.get("clientId"), clientSecret = pm.variables.get("clientSecret");',
    '  if (!clientId || !clientSecret) { console.warn("clientId/clientSecret not set: requests go without a token"); return; }',
    '  const expiresAt = Number(pm.collectionVariables.get("accessTokenExpiresAt") || 0);',
    '  if (pm.collectionVariables.get("accessToken") && Date.now() < expiresAt - 60000) return;',
    '  pm.sendRequest({',
    '    url: pm.variables.replaceIn("{{baseUrl}}") + "/v1/auth/token", method: "POST",',
    '    header: [{ key: "Content-Type", value: "application/x-www-form-urlencoded" }],',
    '    body: { mode: "urlencoded", urlencoded: [',
    '      { key: "grant_type", value: "client_credentials" }, { key: "client_id", value: clientId },',
    '      { key: "client_secret", value: clientSecret }, { key: "scope", value: "schemes:read" }] },',
    '  }, (err, res) => {',
    '    if (err || res.code !== 200) {',
    '      pm.collectionVariables.set("accessToken", ""); pm.collectionVariables.set("accessTokenExpiresAt", "");',
    '      console.error("Token request failed:", err || (res.code + " " + res.text()));',
    '      return;',
    '    }',
    '    const body = res.json();',
    '    pm.collectionVariables.set("accessToken", body.access_token);',
    '    pm.collectionVariables.set("accessTokenExpiresAt", String(Date.now() + body.expires_in * 1000));',
    '  });',
    '})();',
]
bearer = lambda token: {"type": "bearer", "bearer": [{"key": "token", "value": token, "type": "string"}]}
basic_auth = lambda user, password: {"type": "basic", "basic": [{"key": "username", "value": user, "type": "string"},
                                                               {"key": "password", "value": password, "type": "string"}]}
GOOD_FORM = [("grant_type", "client_credentials"), ("client_id", "{{clientId}}"), ("client_secret", "{{clientSecret}}")]
GOOD_JSON = '{\n  "grant_type": "client_credentials",\n  "client_id": "{{clientId}}",\n  "client_secret": "{{clientSecret}}"\n}'
NO_STORE = [
    'pm.test("Cache-Control: no-store", () => pm.expect(pm.response.headers.get("Cache-Control")).to.include("no-store"));',
    'pm.test("Pragma: no-cache", () => pm.expect(pm.response.headers.get("Pragma")).to.eql("no-cache"));',
]
TOKEN_OK = [
    status(200), *NO_STORE,
    'const t = pm.response.json();',
    'pm.test("Body is exactly access_token, token_type, expires_in, scope", () => pm.expect(Object.keys(t)).to.have.members(["access_token", "token_type", "expires_in", "scope"]));',
    'pm.test("token_type is Bearer, scope is schemes:read", () => { pm.expect(t.token_type).to.eql("Bearer"); pm.expect(t.scope).to.eql("schemes:read"); });',
    'pm.test("expires_in is 3600 (JWT_TTL_SECONDS)", () => pm.expect(t.expires_in).to.eql(3600));',
    'pm.test("access_token is a JWT for this client", () => { const parts = t.access_token.split("."); pm.expect(parts).to.have.length(3); const c = JSON.parse(atob(parts[1].replace(/-/g, "+").replace(/_/g, "/"))); pm.expect(c.sub).to.eql(pm.variables.get("clientId")); pm.expect(c.aud).to.eql("scheme-api"); pm.expect(c.exp - c.iat).to.eql(t.expires_in); });',
    'pm.test("Client secret is not echoed", () => pm.expect(pm.response.text()).to.not.include(pm.variables.get("clientSecret")));',
]


def oauth_error(code, error):
    """RFC 6749 section 5.2 error body; 401 also carries a Basic challenge."""
    return [status(code), *NO_STORE,
            f'pm.test("error is {error}", () => pm.expect(pm.response.json().error).to.eql("{error}"));',
            'pm.test("Body is {error, error_description}", () => pm.expect(Object.keys(pm.response.json())).to.have.members(["error", "error_description"]));']\
        + (['pm.test("WWW-Authenticate: Basic challenge", () => pm.expect(pm.response.headers.get("WWW-Authenticate")).to.match(/^Basic realm=/));'] if code == 401 else [])


def token_req(name, tests, form=GOOD_FORM, auth=NO_AUTH, method="POST", raw_body=None, headers=None, description=""):
    return req(name, method, TOKEN_URL, form=None if raw_body is not None or method == "GET" else form, raw_body=raw_body,
               headers=headers, auth=auth, tests=skipped(*tests), description=description)


without = lambda *keys: [(k, v) for k, v in GOOD_FORM if k not in keys]
auth_cases = [
    token_req("Token - form-encoded credentials -> 200", TOKEN_OK, form=GOOD_FORM + [("scope", "schemes:read")],
              description="RFC 6749 client credentials grant. The collection's own pre-request script does the same and stores {{accessToken}}."),
    token_req("Token - JSON body -> 200", TOKEN_OK, raw_body=GOOD_JSON, headers=[{"key": "Content-Type", "value": "application/json"}]),
    token_req("Token - HTTP Basic client authentication -> 200", TOKEN_OK, form=[("grant_type", "client_credentials")],
              auth=basic_auth("{{clientId}}", "{{clientSecret}}")),
    token_req("Token - wrong client_secret -> 401 invalid_client", oauth_error(401, "invalid_client"),
              form=without("client_secret") + [("client_secret", "wrong-secret-qa")]),
    token_req("Token - unknown client_id -> 401 invalid_client", oauth_error(401, "invalid_client"),
              form=[("grant_type", "client_credentials"), ("client_id", "unknown-client-qa"), ("client_secret", "{{clientSecret}}")]),
    token_req("Token - missing grant_type -> 400 invalid_request", oauth_error(400, "invalid_request"), form=without("grant_type")),
    token_req("Token - grant_type password -> 400 unsupported_grant_type", oauth_error(400, "unsupported_grant_type"),
              form=[("grant_type", "password")] + without("grant_type")),
    token_req("Token - invalid scope -> 400 invalid_scope", oauth_error(400, "invalid_scope"), form=GOOD_FORM + [("scope", "admin")]),
    token_req("Token - Basic and body credentials together -> 400 invalid_request", oauth_error(400, "invalid_request"),
              auth=basic_auth("{{clientId}}", "{{clientSecret}}")),
    token_req("Token - JSON parameter sent twice -> 400 invalid_request", oauth_error(400, "invalid_request"),
              raw_body='{\n  "grant_type": "client_credentials",\n  "client_id": "{{clientId}}",\n  "client_secret": "wrong-secret-qa",\n  "client_secret": "{{clientSecret}}"\n}',
              headers=[{"key": "Content-Type", "value": "application/json"}],
              description="A repeated JSON key used to keep the last value, so a second secret won (QA 2026-10-16 #9)."),
    token_req("Token - GET -> 405", [status(405)], method="GET", headers=[]),
    token_req("Token - response is not cacheable (no-store, Pragma no-cache)", [
        status(200), *NO_STORE,
        'pm.test("X-Content-Type-Options: nosniff", () => pm.expect(pm.response.headers.get("X-Content-Type-Options")).to.eql("nosniff"));']),
]

DENIED = lambda detail: [
    status(401),
    f'pm.test("Body is {{detail: \\"{detail}\\"}}", () => pm.expect(pm.response.json()).to.eql({{detail: "{detail}"}}));',
    'pm.test("WWW-Authenticate: Bearer challenge", () => pm.expect(pm.response.headers.get("WWW-Authenticate")).to.match(/^Bearer realm="scheme-api"/));',
    'pm.test("X-Content-Type-Options: nosniff", () => pm.expect(pm.response.headers.get("X-Content-Type-Options")).to.eql("nosniff"));',
] + (['pm.test("WWW-Authenticate has error=\\"invalid_token\\"", () => pm.expect(pm.response.headers.get("WWW-Authenticate")).to.include(\'error="invalid_token"\'));']
     if detail != "Not authenticated" else [])
# Flips the first character of the stored token's signature (needs a token: authEnabled=true).
TAMPER_SCRIPT = [
    'const parts = String(pm.collectionVariables.get("accessToken") || "x.y.z").split(".");',
    'parts[2] = (parts[2][0] === "A" ? "B" : "A") + parts[2].slice(1);',
    'pm.variables.set("tamperedToken", parts.join("."));',
]


def security(url, base=None):
    b = lambda body: {**body, **(base or {})}
    data = b({"dealer_code": "{{dealerCode}}"})
    return [
        req("No token -> 401 + WWW-Authenticate", "POST", url, data, auth=NO_AUTH, tests=skipped(*DENIED("Not authenticated")),
            description="Only when the server requires a token (authEnabled=true)."),
        req("Garbage token -> 401", "POST", url, data, auth=bearer("not-a-jwt"), tests=skipped(*DENIED("Invalid token"))),
        req("Tampered token signature -> 401", "POST", url, data, auth=bearer("{{tamperedToken}}"), prerequest=TAMPER_SCRIPT,
            tests=skipped(*DENIED("Invalid token")), description="A real token with one character of its signature changed."),
        req("X-API-Key only (old scheme) -> 401", "POST", url, data, auth=NO_AUTH,
            headers=[{"key": "Content-Type", "value": "application/json"}, {"key": "X-API-Key", "value": "{{clientSecret}}"}],
            tests=skipped(*DENIED("Not authenticated")), description="X-API-Key is no longer accepted."),
        req("Basic scheme on the data endpoint -> 401", "POST", url, data, auth=basic_auth("{{clientId}}", "{{clientSecret}}"),
            tests=skipped(*DENIED("Not authenticated")), description="Client credentials are only for /v1/auth/token; data needs a Bearer token."),
        req("Non-ASCII Bearer token -> 401, not 500", "POST", url, data, auth=bearer("kéy"),
            tests=skipped(*DENIED("Invalid token")),
            description="A non-ASCII credential used to crash the old key check (500)."),
        validation(url, "SQL injection payload is rejected", b({"dealer_code": "1' OR '1'='1"}), echoed="1' OR '1'='1"),
        validation(url, "Script payload is rejected and not reflected", b({"dealer_code": "<script>alert(1)</script>"}), echoed="<script>"),
        req("Swagger UI loads a pinned release with integrity hashes", "GET", "{{baseUrl}}/docs", auth=NO_AUTH, tests=[
            'if (pm.response.code === 404) { pm.test("SKIPPED: docs are off on this server (DOCS_ENABLED=false)", () => {}); return; }',
            status(200),
            'const html = pm.response.text();',
            'pm.test("Not the floating @5 release", () => pm.expect(html).to.not.include("swagger-ui-dist@5/"));',
            'pm.test("Script pinned with SRI", () => pm.expect(html).to.match(/<script src="https:\\/\\/cdn\\.jsdelivr\\.net\\/npm\\/swagger-ui-dist@\\d+\\.\\d+\\.\\d+\\/swagger-ui-bundle\\.js" integrity="sha384-[^"]+" crossorigin="anonymous">/));',
            'pm.test("Stylesheet pinned with SRI", () => pm.expect(html).to.match(/href="https:\\/\\/cdn\\.jsdelivr\\.net\\/npm\\/swagger-ui-dist@\\d+\\.\\d+\\.\\d+\\/swagger-ui\\.css" integrity="sha384-[^"]+" crossorigin="anonymous">/));',
            'pm.test("CSP allows only that release", () => pm.expect(pm.response.headers.get("Content-Security-Policy")).to.match(/script-src [^;]*https:\\/\\/cdn\\.jsdelivr\\.net\\/npm\\/swagger-ui-dist@\\d+\\.\\d+\\.\\d+\\/;/));',
        ], description="QA 2026-10-16 #14: the docs page used swagger-ui-dist@5 (any 5.x) with no integrity check."),
        req("Oversized body -> 413", "POST", url, raw_body=json.dumps({"dealer_code": "1" * 5000}), tests=[status(413)]),
        req("Security headers are present", "POST", url, b({"dealer_code": "{{dealerCode}}"}), tests=[
            status(200),
            'pm.test("X-Content-Type-Options: nosniff", () => pm.expect(pm.response.headers.get("X-Content-Type-Options")).to.eql("nosniff"));',
            'pm.test("Cache-Control: no-store", () => pm.expect(pm.response.headers.get("Cache-Control")).to.include("no-store"));',
            'pm.test("HSTS sent once (browsers use only the first)", () => pm.expect(pm.response.headers.filter(h => h.key.toLowerCase() === "strict-transport-security").length).to.eql(1));',
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
                "2. Pick the environment and set `clientId` and `clientSecret` (Local has the .env.example values). "
                "The collection fetches one Bearer token from /v1/auth/token and reuses it until a minute before it expires; "
                "set `authEnabled = false` for a server without auth.\n"
                "3. Run a request, or run the whole collection (Runner) to execute every test.\n\n"
                "Full guide: qa/README.md and docs/API_GUIDE.md in the repository."
            ),
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "auth": bearer("{{accessToken}}"),
        "event": [{"listen": "prerequest", "script": {"type": "text/javascript", "exec": [*TOKEN_SCRIPT, *prerequest]}}] + [{"listen": "test", "script": {"type": "text/javascript", "exec": [
            'pm.test("Responds within 2 seconds", () => pm.expect(pm.response.responseTime).to.be.below(2000));',
            'pm.test("No stack trace or internal details in the body", () => { const t = pm.response.text(); ["Traceback", ".py\\"", "psycopg", "SELECT "].forEach(s => pm.expect(t).to.not.include(s)); });',
        ]}}],
        "variable": [*variables, {"key": "accessToken", "value": ""}, {"key": "accessTokenExpiresAt", "value": ""}],
        "item": [
            {"name": "1. Health", "item": [health]},
            {"name": f"2. {functional_name}", "item": functional},
            {"name": "3. Negative cases", "item": [*extra_negative, *negative(url, base)]},
            {"name": "4. Security", "item": security(url, base)},
            {"name": "5. Auth", "item": auth_cases},
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

def env(name, base, client_id, client_secret, auth, dealer, sample):
    return {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"scheme-api/postman/{name}")), "name": name, "_postman_variable_scope": "environment", "values": [
        {"key": "baseUrl", "value": base, "type": "default", "enabled": True},
        {"key": "clientId", "value": client_id, "type": "default", "enabled": True},
        {"key": "clientSecret", "value": client_secret, "type": "secret", "enabled": True},
        {"key": "authEnabled", "value": auth, "type": "default", "enabled": True},
        {"key": "dealerCode", "value": dealer, "type": "default", "enabled": True},
        {"key": "unknownDealerCode", "value": "6199999999", "type": "default", "enabled": True},
        {"key": "sampleData", "value": sample, "type": "default", "enabled": True},
    ]}
(OUT / "Local.postman_environment.json").write_text(json.dumps(env("Scheme API - Local", "http://localhost:8000", "local-client", "local-test-secret", "true", "6100000001", "true"), indent=2) + "\n")
(OUT / "Dev.postman_environment.json").write_text(json.dumps(env("Scheme API - Dev", "https://dev.example.com", "", "", "true", "<a real dealer code>", "false"), indent=2) + "\n")
print("wrote", OUT.name + "/: API 1 collection + Local and Dev environments")
