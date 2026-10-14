// Shared settings and checks for the Scheme API suites.
//
// Env:
//   API_BASE_URL   default http://localhost:8000
//   API_KEY        sent as X-API-Key (needed when the server has API_KEY set)
//   AUTH_ENABLED   "true" when the server requires an API key (enables the auth _SEC cases)
//   DEALER_CODE    a dealer with data this month and in the latest report (default: sample dealer 6100000001)
//   SAMPLE_DATA    "true" (default) when the server holds scripts/make_sample_data.py data;
//                  set "false" on real environments to skip the cases that assert sample values
//   MONTH, YEAR    month sent to API 1 (default: the current month in India time, where the
//                  sample data is)
const { test, expect } = require('@playwright/test');

const API_KEY = process.env.API_KEY || '';
const AUTH_ENABLED = process.env.AUTH_ENABLED === 'true';
const SAMPLE_DATA = process.env.SAMPLE_DATA !== 'false';
const DEALER = process.env.DEALER_CODE || '6100000001';
const NEGATIVE_DEALER = '6100000002'; // sample dealer whose values are negative
const UNKNOWN_DEALER = '6199999999';
const BASE_URL = process.env.API_BASE_URL || 'http://localhost:8000';

const API1 = '/v1/schemes/current-month';
const nowInIndia = new Date(new Date().toLocaleString('en-US', { timeZone: 'Asia/Kolkata' }));
const MONTH = {
  month: Number(process.env.MONTH || nowInIndia.getMonth() + 1),
  year: Number(process.env.YEAR || nowInIndia.getFullYear()),
};
const API2 = '/v1/schemes/report';
const LEAK_MARKERS = ['Traceback', '.py"', 'psycopg', 'SELECT ', 'syntax error', 'ORA-', 'PG::', '"input"'];

function newApi(playwright, headers = API_KEY ? { 'X-API-Key': API_KEY } : {}) {
  return playwright.request.newContext({ baseURL: BASE_URL, extraHTTPHeaders: headers });
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

// The request rules both APIs share (body {"dealer_code": ...}, API key, limits, headers).
// Registers the same edge, negative and security cases for one endpoint, numbered from `start`:
// <prefix>_TC_<n> (and _SEC for security), so API 1 and API 2 each report their own results.
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
        const plain = await (await call({ dealer_code: DEALER })).json();
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
        const first = await (await call({ dealer_code: DEALER })).json();
        expect(await (await call({ dealer_code: DEALER })).json()).toEqual(first);
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
        const response = await client().post(path, { data: '{"dealer_code": ', headers: { 'Content-Type': 'application/json' } });
        await expectValidationError(response);
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
      test.describe('Authentication', () => {
        test.beforeEach(() => test.skip(!AUTH_ENABLED, 'server has no API_KEY set (AUTH_ENABLED != true)'));

        const withHeaders = async (playwright, headers, run) => {
          const ctx = await newApi(playwright, headers);
          try {
            await run(ctx);
          } finally {
            await ctx.dispose();
          }
        };

        test(`${id(true)} Missing X-API-Key returns 401`, async ({ playwright }) => {
          await withHeaders(playwright, {}, async (ctx) =>
            expect((await ctx.post(path, { data: withBase({ dealer_code: DEALER }) })).status()).toBe(401));
        });

        test(`${id(true)} Wrong X-API-Key returns 401`, async ({ playwright }) => {
          await withHeaders(playwright, { 'X-API-Key': 'wrong-key' }, async (ctx) =>
            expect((await ctx.post(path, { data: withBase({ dealer_code: DEALER }) })).status()).toBe(401));
        });

        test(`${id(true)} Empty X-API-Key returns 401`, async ({ playwright }) => {
          await withHeaders(playwright, { 'X-API-Key': '' }, async (ctx) =>
            expect((await ctx.post(path, { data: withBase({ dealer_code: DEALER }) })).status()).toBe(401));
        });

        test(`${id(true)} API key in the query string is not accepted`, async ({ playwright }) => {
          const key = encodeURIComponent(API_KEY);
          await withHeaders(playwright, {}, async (ctx) =>
            expect((await ctx.post(`${path}?api_key=${key}&x-api-key=${key}`, { data: withBase({ dealer_code: DEALER }) })).status()).toBe(401));
        });

        test(`${id(true)} Key with different case/padding is rejected`, async ({ playwright }) => {
          await withHeaders(playwright, { 'X-API-Key': `${API_KEY.toUpperCase()}x` }, async (ctx) =>
            expect((await ctx.post(path, { data: withBase({ dealer_code: DEALER }) })).status()).toBe(401));
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
        test.beforeAll(async () => {
          headers = (await call({ dealer_code: DEALER })).headers();
        });

        test(`${id(true)} X-Content-Type-Options is nosniff`, async () => {
          expect(headers['x-content-type-options']).toBe('nosniff');
        });

        test(`${id(true)} Strict-Transport-Security max-age >= 31536000`, async () => {
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
            description: statuses.includes(429) ? '429 enforced' : 'no rate limiting at the app (expected at gateway)',
          });
        });
      });
    });
  });
}

module.exports = {
  test, expect, API_KEY, AUTH_ENABLED, SAMPLE_DATA, DEALER, NEGATIVE_DEALER, UNKNOWN_DEALER, BASE_URL,
  API1, API2, MONTH, newApi, useClient, expectValidationError, registerRequestContract,
};
