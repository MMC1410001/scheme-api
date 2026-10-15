// Shared settings and checks for the Scheme API suites.
//
// Env:
//   API_BASE_URL   default http://localhost:8000
//   CLIENT_ID      OAuth2 client credentials exchanged once per worker at POST /v1/auth/token for
//   CLIENT_SECRET  a Bearer token (reused until < 60 s are left). Default local-client /
//                  local-test-secret (the .env.example values) only when API_BASE_URL is localhost;
//                  empty elsewhere. Set CLIENT_ID= (empty) to send no token.
//   AUTH_ENABLED   "true" when the server requires a token (enables the auth _SEC cases and the
//                  token-endpoint cases); a failed token request is then an error, not "auth off"
//   JWT_SECRET     the server's JWT_SECRET, to forge expired / wrong-scope / wrong-audience tokens
//                  (API1_TC_74-76_SEC). Default: the .env.example key on localhost only; elsewhere
//                  those cases are skipped. Never put a real environment's key here.
//   JWT_TTL_SECONDS  the server's token lifetime (default 3600), checked as expires_in
//   TOKEN_RATE_LIMIT  the server's TOKEN_RATE_LIMIT_PER_MINUTE; set it to run COMMON_TC_19_SEC,
//                  which uses up the token limit for a minute (run it on its own: -g COMMON_TC_19)
//   DEALER_CODE    a dealer with data in MONTH/YEAR. Default: sample dealer 6100000001; with
//                  SAMPLE_DATA=false, the month's main dealer from qa/test-data/dealers.json
//                  (gitignored; see pick_dealers.py), and one of the two is then required.
//   SAMPLE_DATA    "true" (default) when the server holds scripts/make_sample_data.py data;
//                  set "false" on real environments to skip the cases that assert sample values
//   MONTH, YEAR    month sent to API 1 (default: the current month in India time, where the
//                  sample data is)
//   RATE_LIMIT     the server's RATE_LIMIT_PER_SECOND; set it (with a low server value, e.g. 20)
//                  to run the rate-limit case API1_TC_69_SEC
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { test, expect } = require('@playwright/test');

const BASE_URL = process.env.API_BASE_URL || 'http://localhost:8000';
const IS_LOCAL = /^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?(\/|$)/.test(BASE_URL);
// The published local values from .env.example; the server refuses them outside APP_ENV=local.
const CLIENT_ID = process.env.CLIENT_ID ?? (IS_LOCAL ? 'local-client' : '');
const CLIENT_SECRET = process.env.CLIENT_SECRET ?? (IS_LOCAL ? 'local-test-secret' : '');
const JWT_SECRET = process.env.JWT_SECRET ?? (IS_LOCAL ? 'local-only-jwt-signing-key-not-for-servers-0001' : '');
const AUTH_ENABLED = process.env.AUTH_ENABLED === 'true';
const SAMPLE_DATA = process.env.SAMPLE_DATA !== 'false';
const NEGATIVE_DEALER = '6100000002'; // sample dealer whose values are negative
const UNKNOWN_DEALER = '6199999999';

const API1 = '/v1/schemes/current-month';
const TOKEN_PATH = '/v1/auth/token';
const ISSUER = 'scheme-api'; // also the audience
const SCOPE = 'schemes:read';
const nowInIndia = new Date(new Date().toLocaleString('en-US', { timeZone: 'Asia/Kolkata' }));
const MONTH = {
  month: Number(process.env.MONTH || nowInIndia.getMonth() + 1),
  year: Number(process.env.YEAR || nowInIndia.getFullYear()),
};

// Real dealers per month (client data, gitignored): {"months": {"2026-10": ["<main>", ...]}}.
function dealersFile() {
  const file = path.join(__dirname, '..', '..', 'test-data', 'dealers.json');
  return fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, 'utf8')).months || {} : {};
}
const MONTH_KEY = `${MONTH.year}-${String(MONTH.month).padStart(2, '0')}`;
const REAL_DEALER = SAMPLE_DATA ? undefined : (dealersFile()[MONTH_KEY] || [])[0]; // local runs keep the sample dealer
if (!SAMPLE_DATA && !process.env.DEALER_CODE && !REAL_DEALER) {
  throw new Error(`SAMPLE_DATA=false: set DEALER_CODE, or add ${MONTH_KEY} to qa/test-data/dealers.json (pick_dealers.py)`);
}
const DEALER = process.env.DEALER_CODE || REAL_DEALER || '6100000001';
const LEAK_MARKERS = ['Traceback', '.py"', 'psycopg', 'SELECT ', 'syntax error', 'ORA-', 'PG::', '"input"'];

// One token per worker process: POST /v1/auth/token has its own low per-IP limit
// (TOKEN_RATE_LIMIT_PER_MINUTE, default 30), so never fetch one per request.
let cachedToken = { token: '', expiresAt: 0 };
let pendingToken = null;

async function requestToken() {
  const response = await fetch(BASE_URL + TOKEN_PATH, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ grant_type: 'client_credentials', client_id: CLIENT_ID, client_secret: CLIENT_SECRET, scope: SCOPE }),
  });
  const text = await response.text();
  if (response.status === 200) {
    const body = JSON.parse(text);
    cachedToken = { token: body.access_token, expiresAt: Date.now() + body.expires_in * 1000 };
    return cachedToken.token;
  }
  // A server without auth refuses every client (401 invalid_client): carry on without a token.
  if (!AUTH_ENABLED && response.status === 401) return '';
  const hint = response.status === 429 ? ' (token rate limit: wait a minute, or raise TOKEN_RATE_LIMIT_PER_MINUTE)' : '';
  throw new Error(`POST ${TOKEN_PATH} as ${CLIENT_ID} returned ${response.status}${hint}: ${text}`);
}

// The worker's Bearer token ('' when no credentials are set or the server has auth off).
async function getToken() {
  if (!CLIENT_ID || !CLIENT_SECRET) {
    if (AUTH_ENABLED) throw new Error('AUTH_ENABLED=true needs CLIENT_ID and CLIENT_SECRET');
    return '';
  }
  if (cachedToken.token && Date.now() < cachedToken.expiresAt - 60_000) return cachedToken.token;
  pendingToken ??= requestToken().finally(() => { pendingToken = null; });
  return pendingToken;
}

async function authHeaders() {
  const token = await getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// A request context; with no `headers` it sends the worker's Bearer token, `{}` sends nothing.
async function newApi(playwright, headers) {
  return playwright.request.newContext({ baseURL: BASE_URL, extraHTTPHeaders: headers ?? await authHeaders() });
}

// An HS256 JWT, for forged-token cases. `claims` defaults to what the server issues.
const b64url = (value) => Buffer.from(typeof value === 'string' ? value : JSON.stringify(value)).toString('base64url');
function tokenClaims(overrides = {}) {
  const now = Math.floor(Date.now() / 1000);
  const claims = { iss: ISSUER, aud: ISSUER, sub: CLIENT_ID, scope: SCOPE, iat: now, nbf: now, exp: now + 3600,
    jti: crypto.randomUUID().replace(/-/g, ''), ...overrides };
  for (const [key, value] of Object.entries(claims)) if (value === undefined) delete claims[key];
  return claims;
}
function signJwt(claims, key, header = { alg: 'HS256', typ: 'JWT' }) {
  const unsigned = `${b64url(header)}.${b64url(claims)}`;
  if (header.alg === 'none') return `${unsigned}.`;
  return `${unsigned}.${crypto.createHmac('sha256', key).update(unsigned).digest('base64url')}`;
}

// The 401 a protected endpoint returns, per RFC 6750: body detail and WWW-Authenticate challenge.
async function expectUnauthorized(response, detail) {
  expect(response.status()).toBe(401);
  expect(await response.json()).toEqual({ detail });
  const challenge = response.headers()['www-authenticate'] || '';
  expect(challenge).toMatch(/^Bearer realm="scheme-api"/);
  if (detail === 'Not authenticated') expect(challenge).not.toContain('error=');
  else expect(challenge).toContain('error="invalid_token"');
  expect(response.headers()['x-content-type-options']).toBe('nosniff');
  expect(response.headers()['cache-control']).toContain('no-store');
}

// Runs `run` with a throwaway request context that sends exactly `headers`.
async function withHeaders(playwright, headers, run) {
  const ctx = await newApi(playwright, headers);
  try {
    await run(ctx);
  } finally {
    await ctx.dispose();
  }
}

// Opens one request context for the enclosing describe block; call client() inside tests.
function useClient() {
  let api;
  test.beforeAll(async ({ playwright }) => {
    api = await newApi(playwright);
  });
  test.afterAll(async () => {
    await api.dispose();
  });
  return () => api;
}

async function expectValidationError(response, payload) {
  expect(response.status()).toBe(422);
  const text = await response.text();
  expect(JSON.parse(text).detail).toBeInstanceOf(Array);
  if (payload) expect(text).not.toContain(payload);
  for (const marker of LEAK_MARKERS) expect(text).not.toContain(marker);
}

// The request rules of the API (body {"dealer_code": ...}, Bearer token, limits, headers).
// Registers the same edge, negative and security cases for one endpoint, numbered from `start`:
// <prefix>_TC_<n> (and _SEC for security), so each endpoint reports its own results.
// `baseBody` holds the other required fields (API 1: month and year); every JSON body includes it.
function registerRequestContract({ prefix, path, start, baseBody = {} }) {
  let n = start;
  const id = (sec = false) => `${prefix}_TC_${n++}${sec ? '_SEC' : ''}`;

  test.describe('Request contract', () => {
    const client = useClient();
    const call = (body, options = {}) => client().post(path, { data: { ...baseBody, ...body }, ...options });
    const withBase = (body) => ({ ...baseBody, ...body });

    test.describe('Edge Cases @edge', () => {
      test(`${id()} 20-character dealer code (maximum) is accepted`, async () => {
        expect((await call({ dealer_code: '1'.repeat(20) })).status()).toBe(200);
      });

      test(`${id()} Alphanumeric dealer code is accepted`, async () => {
        expect((await call({ dealer_code: 'ABC123' })).status()).toBe(200);
      });

      test(`${id()} Unknown extra fields in the body are ignored`, async () => {
        const first = await call({ dealer_code: DEALER });
        expect(first.status()).toBe(200);
        const plain = await first.json();
        const response = await call({ dealer_code: DEALER, period: '2020-01', extra: true });
        expect(response.status()).toBe(200);
        expect(await response.json()).toEqual(plain);
      });

      test(`${id()} All-zero dealer code is treated as dealer "0" (no data)`, async () => {
        const response = await call({ dealer_code: '0000' });
        expect(response.status()).toBe(200);
        expect(await response.json()).toEqual([]);
      });

      test(`${id()} Repeated identical requests return identical results`, async () => {
        const [a, b] = [await call({ dealer_code: DEALER }), await call({ dealer_code: DEALER })];
        expect([a.status(), b.status()]).toEqual([200, 200]);
        expect(await b.json()).toEqual(await a.json());
      });
    });

    test.describe('Negative Cases @negative', () => {
      test(`${id()} Missing dealer_code returns 422`, async () => {
        await expectValidationError(await call({}));
      });

      test(`${id()} Empty dealer_code returns 422`, async () => {
        await expectValidationError(await call({ dealer_code: '' }));
      });

      test(`${id()} Whitespace-only dealer_code returns 422`, async () => {
        await expectValidationError(await call({ dealer_code: '   ' }));
      });

      test(`${id()} null dealer_code returns 422`, async () => {
        await expectValidationError(await call({ dealer_code: null }));
      });

      test(`${id()} Boolean dealer_code returns 422`, async () => {
        await expectValidationError(await call({ dealer_code: true }));
      });

      test(`${id()} Array / object dealer_code returns 422`, async () => {
        await expectValidationError(await call({ dealer_code: [DEALER] }));
        await expectValidationError(await call({ dealer_code: { code: DEALER } }));
      });

      test(`${id()} 21-character dealer code returns 422`, async () => {
        await expectValidationError(await call({ dealer_code: '1'.repeat(21) }));
      });

      test(`${id()} Dealer code with a hyphen or space inside returns 422`, async () => {
        await expectValidationError(await call({ dealer_code: '6100-0001' }));
        await expectValidationError(await call({ dealer_code: '6100 0001' }));
      });

      test(`${id()} Invalid JSON returns 422`, async () => {
        // A Buffer, because Playwright would send a string that isn't valid JSON as a quoted JSON string.
        const response = await client().post(path, { data: Buffer.from('{"dealer_code": '), headers: { 'Content-Type': 'application/json' } });
        await expectValidationError(response);
        expect((await response.json()).detail[0].type).toBe('json_invalid');
      });

      test(`${id()} Empty body returns 422`, async () => {
        await expectValidationError(await client().post(path, { headers: { 'Content-Type': 'application/json' } }));
      });

      test(`${id()} Non-JSON content type returns 422`, async () => {
        const response = await client().post(path, {
          data: `dealer_code=${DEALER}`, headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        });
        await expectValidationError(response);
      });

      test(`${id()} GET on the POST endpoint returns 405`, async () => {
        expect((await client().get(path)).status()).toBe(405);
      });
    });

    test.describe('Security @security', () => {
      test.describe('Authentication (OAuth2 Bearer token)', () => {
        test.beforeEach(() => test.skip(!AUTH_ENABLED, 'server does not require a token (AUTH_ENABLED != true)'));
        const post = (ctx, url = path) => ctx.post(url, { data: withBase({ dealer_code: DEALER }) });

        test(`${id(true)} Missing or empty Authorization returns 401 Not authenticated + Bearer challenge`, async ({ playwright }) => {
          for (const headers of [{}, { Authorization: '' }, { Authorization: 'Bearer' }]) {
            await withHeaders(playwright, headers, async (ctx) => expectUnauthorized(await post(ctx), 'Not authenticated'));
          }
        });

        test(`${id(true)} Garbage Bearer token returns 401 Invalid token`, async ({ playwright }) => {
          for (const token of ['not-a-jwt', 'a.b.c', 'eyJhbGciOiJIUzI1NiJ9.e30.', `${'x'.repeat(2000)}`]) {
            await withHeaders(playwright, { Authorization: `Bearer ${token}` }, async (ctx) =>
              expectUnauthorized(await post(ctx), 'Invalid token'));
          }
        });

        test(`${id(true)} Valid token with a tampered signature or payload returns 401 Invalid token`, async ({ playwright }) => {
          const [header, payload, signature] = (await getToken()).split('.');
          const claims = JSON.parse(Buffer.from(payload, 'base64url').toString());
          const tampered = [
            `${header}.${payload}.${signature[0] === 'A' ? 'B' : 'A'}${signature.slice(1)}`,
            `${header}.${Buffer.from(JSON.stringify({ ...claims, sub: 'someone-else' })).toString('base64url')}.${signature}`,
            `${header}.${payload}`,
          ];
          for (const token of tampered) {
            await withHeaders(playwright, { Authorization: `Bearer ${token}` }, async (ctx) =>
              expectUnauthorized(await post(ctx), 'Invalid token'));
          }
        });

        test(`${id(true)} Token in the query string is not accepted (header only)`, async ({ playwright }) => {
          const token = encodeURIComponent(await getToken());
          await withHeaders(playwright, {}, async (ctx) =>
            expectUnauthorized(await post(ctx, `${path}?access_token=${token}`), 'Not authenticated'));
        });

        test(`${id(true)} X-API-Key alone (the old scheme) returns 401`, async ({ playwright }) => {
          await withHeaders(playwright, { 'X-API-Key': CLIENT_SECRET || 'local-test-key' }, async (ctx) =>
            expectUnauthorized(await post(ctx), 'Not authenticated'));
        });
      });

      test.describe('Injection & input handling', () => {
        const payloads = {
          SQLi: ["1' OR '1'='1", '1; DROP TABLE scheme_records;--', '1 UNION SELECT * FROM ingested_files'],
          XSS: ['<script>alert(1)</script>', '"><img src=x onerror=alert(1)>'],
          'Path traversal': ['../../etc/passwd', '..%2F..%2Fetc%2Fpasswd'],
          'Null byte': ['6100\u00000001'],
          CRLF: ['6100\r\nX-Injected: yes'],
          Unicode: ['６１００００００01', '6100000001​'], // full-width digits, zero-width space
        };
        for (const [name, list] of Object.entries(payloads)) {
          test(`${id(true)} ${name} payloads are rejected with 422 and not reflected`, async () => {
            for (const payload of list) {
              const response = await call({ dealer_code: payload });
              await expectValidationError(response, payload);
              expect(response.headers()['x-injected']).toBeUndefined();
            }
          });
        }

        test(`${id(true)} 5000-character dealer code is rejected (413/422), never 500`, async () => {
          expect([413, 422]).toContain((await call({ dealer_code: 'A'.repeat(5000) })).status());
        });

        test(`${id(true)} 1 MB body is rejected with 413`, async () => {
          const response = await client().post(path, {
            data: JSON.stringify(withBase({ dealer_code: '1', pad: 'x'.repeat(1_000_000) })), headers: { 'Content-Type': 'application/json' },
          });
          expect(response.status()).toBe(413);
        });

        test(`${id(true)} Deeply nested JSON does not cause a 500`, async () => {
          const nested = '['.repeat(5000) + ']'.repeat(5000);
          const response = await client().post(path, {
            data: `{"dealer_code": "1", "month": 1, "year": 2026, "x": ${nested}}`, headers: { 'Content-Type': 'application/json' },
          });
          expect(response.status()).toBeLessThan(500);
        });
      });

      test.describe('Method abuse', () => {
        test(`${id(true)} PUT / DELETE / PATCH return 405`, async () => {
          for (const method of ['put', 'delete', 'patch']) expect((await client()[method](path)).status(), method).toBe(405);
        });

        test(`${id(true)} TRACE / OPTIONS do not expose data`, async () => {
          for (const method of ['TRACE', 'OPTIONS']) expect((await client().fetch(path, { method })).status(), method).toBe(405);
        });
      });

      test.describe('Response headers (CWE-693) & information disclosure (CWE-200/209)', () => {
        let headers;
        let headerList; // every header line as sent, to catch duplicates that headers() merges
        test.beforeAll(async () => {
          const response = await call({ dealer_code: DEALER });
          headers = response.headers();
          headerList = response.headersArray();
        });

        test(`${id(true)} X-Content-Type-Options is nosniff`, async () => {
          expect(headers['x-content-type-options']).toBe('nosniff');
        });

        test(`${id(true)} Strict-Transport-Security max-age >= 31536000, sent once`, async () => {
          // Browsers apply only the first HSTS header (RFC 6797 8.1), so a proxy adding a second one
          // (e.g. nginx and the app both setting it) is a misconfiguration.
          expect(headerList.filter((h) => h.name.toLowerCase() === 'strict-transport-security')).toHaveLength(1);
          const maxAge = Number((headers['strict-transport-security'] || '').match(/max-age=(\d+)/)?.[1]);
          expect(maxAge).toBeGreaterThanOrEqual(31536000);
        });

        test(`${id(true)} Cache-Control is no-store on dealer data and on errors`, async () => {
          expect(headers['cache-control']).toContain('no-store');
          expect((await call({})).headers()['cache-control']).toContain('no-store');
        });

        test(`${id(true)} Content-Security-Policy and X-Frame-Options are set`, async () => {
          expect(headers['content-security-policy']).toContain("default-src 'none'");
          expect(headers['x-frame-options']).toBe('DENY');
        });

        test(`${id(true)} Server header does not reveal a product version; no X-Powered-By`, async () => {
          expect(headers['server'] || '').not.toMatch(/\/\d|\d+\.\d+/);
          expect(headers['x-powered-by']).toBeUndefined();
        });

        test(`${id(true)} CORS: a foreign Origin is not allowed`, async () => {
          const response = await call({ dealer_code: DEALER }, { headers: { Origin: 'https://evil.example.com' } });
          expect(response.headers()['access-control-allow-origin']).toBeUndefined();
        });
      });

      test.describe('Rate limiting', () => {
        test(`${id(true)} 50 rapid requests cause no 500s (records whether 429 is enforced)`, async () => {
          const responses = await Promise.all(Array.from({ length: 50 }, () => call({ dealer_code: DEALER })));
          const statuses = responses.map((r) => r.status());
          expect(statuses.filter((s) => s >= 500)).toEqual([]);
          test.info().annotations.push({
            type: 'rate-limit',
            // The per-IP limit (2,500/s by default) is far above 50; API1_TC_69_SEC tests it with a low limit.
            description: statuses.includes(429) ? '429 enforced' : 'under the per-IP rate limit',
          });
        });
      });
    });
  });
}

module.exports = {
  test, expect, AUTH_ENABLED, CLIENT_ID, CLIENT_SECRET, JWT_SECRET, SAMPLE_DATA, DEALER, NEGATIVE_DEALER,
  UNKNOWN_DEALER, BASE_URL, API1, TOKEN_PATH, SCOPE, MONTH, newApi, withHeaders, getToken, authHeaders,
  tokenClaims, signJwt, useClient, expectValidationError, expectUnauthorized, registerRequestContract,
};
