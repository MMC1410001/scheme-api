// Shared settings for every k6 script. Nothing secret lives here: pass the key via API_KEY.
//
//   BASE_URL       default http://localhost:8000
//   API_KEY        sent as X-API-Key
//   DEALER_PREFIX  dealer codes are <prefix><8-digit n>; default "62" = perf data from
//                  `scripts/make_sample_data.py --perf-dealers N`
//   DEALER_COUNT   how many perf dealers exist (default 20000)
//   UNKNOWN_RATE   share of requests for dealers with no data (default 0.05)
//   API            api1 (default); api2 and both are refused while API 2 is on hold (see traffic.js)
//   MONTH, YEAR    month sent to API 1 (default: the current month in India time)
import { check } from 'k6';
import exec from 'k6/execution';

export const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const API_KEY = __ENV.API_KEY || '';
const DEALER_PREFIX = __ENV.DEALER_PREFIX || '62';
const DEALER_COUNT = parseInt(__ENV.DEALER_COUNT || '20000');
const UNKNOWN_RATE = parseFloat(__ENV.UNKNOWN_RATE || '0.05');

export const API1 = `${BASE_URL}/v1/schemes/current-month`;
export const API2 = `${BASE_URL}/v1/schemes/report`;

const nowInIndia = new Date(Date.now() + 5.5 * 3600 * 1000);
export const MONTH = {
  month: parseInt(__ENV.MONTH || String(nowInIndia.getUTCMonth() + 1)),
  year: parseInt(__ENV.YEAR || String(nowInIndia.getUTCFullYear())),
};

export const params = (name) => ({
  headers: { 'Content-Type': 'application/json', ...(API_KEY ? { 'X-API-Key': API_KEY } : {}) },
  tags: { name }, // groups metrics per endpoint in the summary
  timeout: '15s',
});

// Spread VUs over the dealer pool so the test isn't served from one hot row.
export function pickDealer() {
  if (Math.random() < UNKNOWN_RATE) return '6199999999';
  const n = ((exec.vu.idInTest * 7919 + exec.scenario.iterationInTest) % DEALER_COUNT) + 1;
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

export function checkApi2(res) {
  const rows = jsonArray(res);
  return check(res, {
    'API 2 status is 200': (r) => r.status === 200,
    'API 2 body is an array': () => rows !== null,
    'API 2 returns at most one row, with customer': () => rows !== null && rows.length <= 1 && rows.every((row) => 'customer' in row),
  });
}

export const summaryTrendStats = ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'];

// Pass/fail criteria. A dealer lookup is a single indexed query, so we hold it to a tight p95.
export const thresholds = {
  http_req_failed: ['rate<0.01'],                                 // < 1% errors
  'http_req_duration{name:current-month}': ['p(95)<500', 'p(99)<1000'],
  // 'http_req_duration{name:report}': ['p(95)<500', 'p(99)<1000'],  // API 2 is on hold
  checks: ['rate>0.99'],                                          // > 99% of checks pass
};

// Writes a JSON summary to reports/<script>[-<API>]-summary.json next to the console output.
export function summaryTo(name) {
  const suffix = __ENV.API ? `-${__ENV.API.toLowerCase()}` : '';
  return (data) => ({
    [`reports/${name}${suffix}-summary.json`]: JSON.stringify(data, null, 2),
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
