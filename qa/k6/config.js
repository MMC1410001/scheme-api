// Shared settings for every k6 script. Nothing secret lives here: pass credentials via env.
//
//   BASE_URL       default http://localhost:8000
//   CLIENT_ID      OAuth2 client credentials. setup() exchanges them ONCE at POST /v1/auth/token
//   CLIENT_SECRET  and every VU sends that Bearer token (the token endpoint allows only
//                  TOKEN_RATE_LIMIT_PER_MINUTE requests per IP, default 30). Default
//                  local-client / local-test-secret on localhost only; empty = no token (auth off).
//                  A token lasts JWT_TTL_SECONDS (1 h): keep runs shorter than that.
//   DEALER_PREFIX  dealer codes are <prefix><8-digit n>; default "62" = perf data from
//                  `scripts/make_sample_data.py --perf-dealers N`
//   DEALER_COUNT   how many perf dealers exist (default 20000)
//   DEALER_CODES   comma-separated real dealer codes; when set, used instead of the generated
//                  <prefix><n> codes (for Dev/UAT, which don't have the perf dealers)
//   DEALERS_FILE   "true" = use the month's codes from ../test-data/dealers.json (gitignored;
//                  see pick_dealers.py) instead of DEALER_CODES
//   UNKNOWN_RATE   share of requests for dealers with no data (default 0.05)
//   MONTH, YEAR    month sent to API 1 (default: the current month in India time)
import http from 'k6/http';
import { check } from 'k6';
import exec from 'k6/execution';

export const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const IS_LOCAL = /^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?(\/|$)/.test(BASE_URL);
const CLIENT_ID = __ENV.CLIENT_ID !== undefined ? __ENV.CLIENT_ID : (IS_LOCAL ? 'local-client' : '');
const CLIENT_SECRET = __ENV.CLIENT_SECRET !== undefined ? __ENV.CLIENT_SECRET : (IS_LOCAL ? 'local-test-secret' : '');
const DEALER_PREFIX = __ENV.DEALER_PREFIX || '62';
const DEALER_COUNT = parseInt(__ENV.DEALER_COUNT || '20000');
const UNKNOWN_RATE = parseFloat(__ENV.UNKNOWN_RATE || '0.05');

export const API1 = `${BASE_URL}/v1/schemes/current-month`;

const nowInIndia = new Date(Date.now() + 5.5 * 3600 * 1000);
export const MONTH = {
  month: parseInt(__ENV.MONTH || String(nowInIndia.getUTCMonth() + 1)),
  year: parseInt(__ENV.YEAR || String(nowInIndia.getUTCFullYear())),
};

function realDealers() {
  if (__ENV.DEALERS_FILE !== 'true') return (__ENV.DEALER_CODES || '').split(',').map((c) => c.trim()).filter(Boolean);
  const key = `${MONTH.year}-${String(MONTH.month).padStart(2, '0')}`;
  const codes = (JSON.parse(open('../test-data/dealers.json')).months || {})[key] || [];
  if (!codes.length) throw new Error(`no dealers for ${key} in qa/test-data/dealers.json`);
  return codes;
}
const DEALER_CODES = realDealers();

// Call from each script's setup(): one token for the whole test, handed to every VU as setup data.
// A server with auth off refuses every client (401), so the test then runs without a token.
export function authSetup() {
  if (!CLIENT_ID || !CLIENT_SECRET) return { token: '' };
  const res = http.post(`${BASE_URL}/v1/auth/token`,
    { grant_type: 'client_credentials', client_id: CLIENT_ID, client_secret: CLIENT_SECRET, scope: 'schemes:read' },
    { tags: { name: 'token' } });
  if (res.status === 200) return { token: res.json('access_token') };
  if (res.status === 401 && __ENV.AUTH_ENABLED !== 'true') {
    console.warn('token request refused (401): running without a token (server auth off?)');
    return { token: '' };
  }
  throw new Error(`POST /v1/auth/token returned ${res.status}: ${res.body}`);
}

export const params = (name, token) => ({
  headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
  tags: { name }, // groups metrics per endpoint in the summary
  timeout: '15s',
});

// Spread VUs over the dealer pool so the test isn't served from one hot row.
export function pickDealer() {
  if (Math.random() < UNKNOWN_RATE) return '6199999999';
  const i = exec.vu.idInTest * 7919 + exec.scenario.iterationInTest;
  if (DEALER_CODES.length) return DEALER_CODES[i % DEALER_CODES.length];
  const n = (i % DEALER_COUNT) + 1;
  return `${DEALER_PREFIX}${String(n).padStart(8, '0')}`;
}

// A timed-out or reset request has no body; treat that as a failed check, not a script error.
function jsonArray(r) {
  try {
    const body = r.json();
    return Array.isArray(body) ? body : null;
  } catch (e) {
    return null;
  }
}

export function checkApi1(res) {
  const rows = jsonArray(res);
  return check(res, {
    'API 1 status is 200': (r) => r.status === 200,
    'API 1 body is an array': () => rows !== null,
    'API 1 rows have the template fields': () => rows !== null && rows.every((row) => 'ComponentDescription' in row && 'GsrB' in row),
  });
}

export const summaryTrendStats = ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'];

// Pass/fail criteria. A dealer lookup is a single indexed query, so we hold it to a tight p95.
export const thresholds = {
  http_req_failed: ['rate<0.01'],                                 // < 1% errors
  'http_req_duration{name:current-month}': ['p(95)<500', 'p(99)<1000'],
  checks: ['rate>0.99'],                                          // > 99% of checks pass
};

// Writes a JSON summary to reports/<script>-summary.json next to the console output.
export function summaryTo(name) {
  return (data) => ({
    [`reports/${name}-summary.json`]: JSON.stringify(data, null, 2),
    stdout: '\n' + textSummary(data),
  });
}

function textSummary(data) {
  const m = data.metrics;
  const fmt = (metric, stat) => (m[metric] && m[metric].values[stat] !== undefined ? m[metric].values[stat].toFixed(2) : 'n/a');
  const lines = [
    `requests:        ${m.http_reqs ? m.http_reqs.values.count : 0} (${fmt('http_reqs', 'rate')}/s)`,
    `failed:          ${(m.http_req_failed ? m.http_req_failed.values.rate * 100 : 0).toFixed(2)}%`,
    `checks passed:   ${(m.checks ? m.checks.values.rate * 100 : 0).toFixed(2)}%`,
    `latency p50/p95/p99 (ms): ${fmt('http_req_duration', 'med')} / ${fmt('http_req_duration', 'p(95)')} / ${fmt('http_req_duration', 'p(99)')}`,
    `max VUs:         ${m.vus_max ? m.vus_max.values.max : 'n/a'}`,
  ];
  const failed = Object.entries(data.metrics)
    .filter(([, v]) => v.thresholds && Object.values(v.thresholds).some((t) => !t.ok))
    .map(([k]) => k);
  lines.push(failed.length ? `THRESHOLDS FAILED: ${failed.join(', ')}` : 'all thresholds passed');
  return lines.join('\n') + '\n';
}
