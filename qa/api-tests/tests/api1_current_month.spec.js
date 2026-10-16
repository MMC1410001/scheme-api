// API 1 — POST /v1/schemes/current-month: the dealer's rows for the 10 template components in the
// requested month. Body: {"dealer_code": ..., "month": 1-12, "year": 2000-2100}.
// Settings (API_BASE_URL, CLIENT_ID, CLIENT_SECRET, DEALER_CODE, SAMPLE_DATA, MONTH, YEAR, ...) are described in helpers.js.
const crypto = require('crypto');
const {
  test, expect, AUTH_ENABLED, CLIENT_ID, CLIENT_SECRET, JWT_SECRET, SAMPLE_DATA, DEALER, NEGATIVE_DEALER,
  UNKNOWN_DEALER, API1, MONTH, withHeaders, getToken, tokenClaims, signJwt, useClient, expectValidationError,
  expectUnauthorized, registerRequestContract,
} = require('./helpers');

const FIELDS = ['Customer', 'FromDate', 'ToDate', 'ComponentDescription', 'Qualification', 'GsrB',
  'RebateRate', 'ClearCut', 'ShortfallAmount', 'ShortfallPct', 'NextSlabRate'];
const AMOUNTS = ['Qualification', 'GsrB', 'RebateRate', 'ClearCut', 'ShortfallAmount', 'ShortfallPct', 'NextSlabRate'];
const COMPONENTS = ['EB SLAB 1', 'EB SLAB 2', 'EB SLAB 3', 'VALUE', 'VALUE %', 'VALUE FA', 'LUXURY', 'PREMIUM 01',
  'PREMIUM 02', 'EB FULL MONTH'];
const MONTH_PREFIX = `${MONTH.year}-${String(MONTH.month).padStart(2, '0')}`;
const MONTH_END = `${MONTH_PREFIX}-${String(new Date(Date.UTC(MONTH.year, MONTH.month, 0)).getUTCDate()).padStart(2, '0')}`;

test.describe('API 1 - Monthly schemes', () => {
  const client = useClient();
  // Every call sends the test month unless the body overrides it.
  const api1 = (body) => client().post(API1, { data: { ...MONTH, ...body } });
  // Asserts 200 and an array first, so a server error (e.g. 503) fails here instead of two equal
  // error bodies passing a "same result as" comparison.
  const rowsFor = async (dealer) => {
    const response = await api1({ dealer_code: dealer });
    expect(response.status(), `dealer_code ${JSON.stringify(dealer)}`).toBe(200);
    const rows = await response.json();
    expect(Array.isArray(rows)).toBe(true);
    return rows;
  };

  // ===========================================================================
  // FUNCTIONAL — API1_TC_1 to API1_TC_11 (valid requests, expecting 200)
  // ===========================================================================
  test.describe('Functional @functional', () => {
    test('API1_TC_1 Returns 200 and a non-empty array for a dealer with data in the month', async () => {
      const response = await api1({ dealer_code: DEALER });
      expect(response.status()).toBe(200);
      const rows = await response.json();
      expect(Array.isArray(rows)).toBe(true);
      expect(rows.length).toBeGreaterThan(0);
    });

    test('API1_TC_2 Rows have exactly the 11 fields (template + NextSlabRate)', async () => {
      for (const row of await rowsFor(DEALER)) expect(Object.keys(row).sort()).toEqual([...FIELDS].sort());
    });

    test('API1_TC_3 Only the 10 template components are returned', async () => {
      for (const row of await rowsFor(DEALER)) expect(COMPONENTS).toContain(row.ComponentDescription);
    });

    test('API1_TC_4 Rows are ordered by ToDate, then template component order', async () => {
      const rows = await rowsFor(DEALER);
      for (let i = 1; i < rows.length; i++) {
        const [a, b] = [rows[i - 1], rows[i]];
        const ordered = a.ToDate < b.ToDate ||
          (a.ToDate === b.ToDate && COMPONENTS.indexOf(a.ComponentDescription) <= COMPONENTS.indexOf(b.ComponentDescription));
        expect(ordered, `${a.ComponentDescription} (${a.ToDate}) before ${b.ComponentDescription} (${b.ToDate})`).toBe(true);
      }
    });

    test('API1_TC_5 Customer in every row equals the requested dealer', async () => {
      for (const row of await rowsFor(DEALER)) expect(row.Customer).toBe(DEALER);
    });

    test('API1_TC_6 Accepts the dealer code as a JSON number', async () => {
      const asString = await rowsFor(DEALER);
      const response = await api1({ dealer_code: Number(DEALER) });
      expect(response.status()).toBe(200);
      expect(await response.json()).toEqual(asString);
    });

    test('API1_TC_7 Ignores leading zeros in the dealer code', async () => {
      expect(await rowsFor(`000${DEALER}`)).toEqual(await rowsFor(DEALER));
    });

    test('API1_TC_8 Trims spaces around the dealer code', async () => {
      expect(await rowsFor(`  ${DEALER}  `)).toEqual(await rowsFor(DEALER));
    });

    test('API1_TC_9 Returns 200 [] for a dealer with no data', async () => {
      const response = await api1({ dealer_code: UNKNOWN_DEALER });
      expect(response.status()).toBe(200);
      expect(await response.json()).toEqual([]);
    });

    test('API1_TC_10 Accepts month and year as numeric strings', async () => {
      const response = await api1({ dealer_code: DEALER, month: String(MONTH.month).padStart(2, '0'), year: String(MONTH.year) });
      expect(response.status()).toBe(200);
      expect(await response.json()).toEqual(await rowsFor(DEALER));
    });

    test('API1_TC_11 Returns 200 [] for a month with no data (January 2000)', async () => {
      const response = await api1({ dealer_code: DEALER, month: 1, year: 2000 });
      expect(response.status()).toBe(200);
      expect(await response.json()).toEqual([]);
    });
  });

  // ===========================================================================
  // DATA VALIDATION — API1_TC_12 to API1_TC_19 (response values against business rules)
  // ===========================================================================
  test.describe('Data Validation @validation', () => {
    test('API1_TC_12 Dates are YYYY-MM-DD and FromDate <= ToDate', async () => {
      for (const row of await rowsFor(DEALER)) {
        expect(row.FromDate).toMatch(/^\d{4}-\d{2}-\d{2}$/);
        expect(row.ToDate).toMatch(/^\d{4}-\d{2}-\d{2}$/);
        expect(row.FromDate <= row.ToDate).toBe(true);
      }
    });

    test('API1_TC_13 Amount fields are numbers (or null), never strings', async () => {
      for (const row of await rowsFor(DEALER)) {
        for (const key of AMOUNTS) expect(row[key] === null || typeof row[key] === 'number', key).toBe(true);
      }
    });

    test('API1_TC_14 Each component appears at most once per dealer (single agreement)', async () => {
      const names = (await rowsFor(DEALER)).map((r) => r.ComponentDescription);
      expect(new Set(names).size).toBe(names.length);
    });

    test('API1_TC_15 Every row is valid in the requested month (validity period overlaps it)', async () => {
      // A row belongs to the months its FromDate..ToDate overlaps, not to the GCS folder it came from
      // (the September folder also holds late SAP updates of August schemes).
      for (const row of await rowsFor(DEALER)) {
        expect(row.FromDate <= MONTH_END && row.ToDate >= `${MONTH_PREFIX}-01`, `${row.ComponentDescription} ${row.FromDate}..${row.ToDate}`).toBe(true);
      }
    });

    test('API1_TC_16 [sample data] Sample dealer has all 10 components', async () => {
      test.skip(!SAMPLE_DATA, 'needs scripts/make_sample_data.py data');
      const rows = await rowsFor('6100000001');
      expect(rows.map((r) => r.ComponentDescription).sort()).toEqual([...COMPONENTS].sort());
    });

    test('API1_TC_17 [sample data] Latest SAP update wins (day-2 LUXURY values replace, not add)', async () => {
      test.skip(!SAMPLE_DATA, 'needs scripts/make_sample_data.py data');
      const luxury = (await rowsFor('6100000001')).find((r) => r.ComponentDescription === 'LUXURY');
      expect(luxury).toMatchObject({ Qualification: 315.1, GsrB: 159292.8, ClearCut: 1592.93, ShortfallPct: 21.23, NextSlabRate: 1.5 });
    });

    test('API1_TC_18 [sample data] SAP trailing-minus numbers come back negative', async () => {
      test.skip(!SAMPLE_DATA, 'needs scripts/make_sample_data.py data');
      const slab1 = (await rowsFor(NEGATIVE_DEALER)).find((r) => r.ComponentDescription === 'EB SLAB 1');
      expect(slab1.Qualification).toBe(-882);
      expect(slab1.GsrB).toBe(-882);
    });

    test('API1_TC_19 [sample data] Qualification: GSR for EB slabs, EB FULL MONTH and Value; invoiced qty for Luxury/Premium', async () => {
      test.skip(!SAMPLE_DATA, 'needs scripts/make_sample_data.py data');
      const byName = Object.fromEntries((await rowsFor('6100000001')).map((r) => [r.ComponentDescription, r]));
      expect(byName['EB SLAB 1'].Qualification).toBe(byName['EB SLAB 1'].GsrB); // GSR (Q) == GSR (B) in sample
      expect(byName.LUXURY.Qualification).toBe(315.1); // invoiced quantity, not the GSR
      expect(byName.LUXURY.Qualification).not.toBe(byName.LUXURY.GsrB);
      // SAP leaves GSR (Q) empty on EB FULL MONTH; it takes its GSR from GSR (B), like the slabs.
      expect(byName['EB FULL MONTH'].Qualification).toBe(405232.86);
      expect(byName['EB FULL MONTH'].Qualification).toBe(byName['EB FULL MONTH'].GsrB);
    });
  });

  // ===========================================================================
  // MONTH AND YEAR — API1_TC_20 to API1_TC_25 (invalid month/year, expecting 422)
  // ===========================================================================
  test.describe('Month and year @negative', () => {
    const without = (key) => {
      const body = { dealer_code: DEALER, ...MONTH };
      delete body[key];
      return client().post(API1, { data: body });
    };

    test('API1_TC_20 Missing month returns 422', async () => {
      await expectValidationError(await without('month'));
    });

    test('API1_TC_21 Missing year returns 422', async () => {
      await expectValidationError(await without('year'));
    });

    test('API1_TC_22 Month outside 1-12 returns 422', async () => {
      for (const month of [0, 13, -1]) await expectValidationError(await api1({ dealer_code: DEALER, month }));
    });

    test('API1_TC_23 Year outside 2000-2100 returns 422', async () => {
      for (const year of [1999, 2101, 26]) await expectValidationError(await api1({ dealer_code: DEALER, year }));
    });

    test('API1_TC_24 Boolean month or year returns 422 (true is not 1)', async () => {
      await expectValidationError(await api1({ dealer_code: DEALER, month: true }));
      await expectValidationError(await api1({ dealer_code: DEALER, year: false }));
    });

    test('API1_TC_25 Non-integer month returns 422 (9.5, "Sept", "", [9], null)', async () => {
      for (const month of [9.5, 'Sept', '', [9], null]) await expectValidationError(await api1({ dealer_code: DEALER, month }));
    });
  });

  // ===========================================================================
  // EDGE, NEGATIVE, SECURITY — API1_TC_26 onwards (see helpers.js)
  // ===========================================================================
  registerRequestContract({ prefix: 'API1', path: API1, start: 26, baseBody: MONTH });

  // ===========================================================================
  // MONTH AND YEAR FORMAT — API1_TC_66 to API1_TC_68 (raw JSON, so 09 can be sent unquoted;
  // sent as a Buffer because Playwright re-encodes a string body that isn't valid JSON)
  // Month: 1-12 as one or two digits (9, 09, "9", "09"). Year: four digits, 2000-2100.
  // ===========================================================================
  test.describe('Month and year format', () => {
    const raw = (month, year) => client().post(API1, {
      headers: { 'Content-Type': 'application/json' },
      data: Buffer.from(`{"dealer_code": ${JSON.stringify(DEALER)}, "month": ${month}, "year": ${year}}`),
    });
    const padded = String(MONTH.month).padStart(2, '0');

    test('API1_TC_66 Month as one or two digits (9, 09, "9", "09") returns the same rows', async () => {
      const expected = await rowsFor(DEALER);
      for (const month of [MONTH.month, padded, `"${MONTH.month}"`, `"${padded}"`]) {
        const response = await raw(month, MONTH.year);
        expect(response.status(), `month ${month}`).toBe(200);
        expect(await response.json(), `month ${month}`).toEqual(expected);
      }
    });

    test('API1_TC_67 Month with a sign, decimal, exponent, spaces, 0 or 3 digits returns 422 @negative', async () => {
      for (const month of ['-9', '-09', '"-9"', '"+9"', '9.0', '"9.0"', '09.5', '1e1', '0', '00', '"0"', '009', '"009"', '"010"', '" 9 "',
        '010', '0010', '"0010"', '0012', '"012"', '13', '"13"']) {
        const response = await raw(month, MONTH.year);
        expect(response.status(), `month ${month}`).toBe(422);
        await expectValidationError(response);
      }
    });

    test('API1_TC_78 Every month 1-12 is accepted as 1-9, 01-09, 10, 11, 12 (number or string) @validation', async () => {
      for (let m = 1; m <= 12; m++) {
        const forms = m < 10 ? [`${m}`, `0${m}`, `"${m}"`, `"0${m}"`] : [`${m}`, `"${m}"`];
        for (const month of forms) {
          const response = await raw(month, MONTH.year);
          expect(response.status(), `month ${month}`).toBe(200);
          expect(Array.isArray(await response.json()), `month ${month}`).toBe(true);
        }
      }
    });

    test('API1_TC_79 Zero-padded month works whatever the Content-Type case (Application/JSON) @edge', async () => {
      const expected = await rowsFor(DEALER);
      for (const contentType of ['Application/JSON', 'APPLICATION/JSON; charset=utf-8']) {
        const response = await client().post(API1, {
          headers: { 'Content-Type': contentType },
          data: Buffer.from(`{"dealer_code": ${JSON.stringify(DEALER)}, "month": ${padded}, "year": ${MONTH.year}}`),
        });
        expect(response.status(), contentType).toBe(200);
        expect(await response.json(), contentType).toEqual(expected);
      }
    });

    test('API1_TC_68 Year that is not four digits, or has a sign, decimal or spaces returns 422 @negative', async () => {
      for (const year of ['02026', '"02026"', '26', '"26"', '-2026', '"+2026"', '2026.0', '"2026.0"', '2.026e3', '" 2026 "']) {
        const response = await raw(MONTH.month, year);
        expect(response.status(), `year ${year}`).toBe(422);
        await expectValidationError(response);
      }
    });
  });

  // ===========================================================================
  // RATE LIMIT — API1_TC_69_SEC. The server allows RATE_LIMIT_PER_SECOND requests per IP per second
  // (default 2,500). Runs only when RATE_LIMIT is set to the server's value; start a test server
  // with a low one, e.g. RATE_LIMIT_PER_SECOND=20, and run with RATE_LIMIT=20.
  // ===========================================================================
  test.describe('Rate limit @security', () => {
    const LIMIT = Number(process.env.RATE_LIMIT || 0);

    test('API1_TC_69_SEC More than the per-IP limit in one second gets 429 with Retry-After, then recovers', async () => {
      test.skip(!LIMIT, 'set RATE_LIMIT to the server\'s RATE_LIMIT_PER_SECOND');
      const responses = await Promise.all(Array.from({ length: LIMIT * 3 }, () => api1({ dealer_code: DEALER })));
      const statuses = responses.map((r) => r.status());
      expect(statuses.filter((s) => s >= 500)).toEqual([]);
      const refused = responses.filter((r) => r.status() === 429);
      expect(refused.length).toBeGreaterThan(0);
      for (const response of refused) {
        expect(response.headers()['retry-after']).toBe('1');
        expect(response.headers()['x-content-type-options']).toBe('nosniff');
        expect(await response.json()).toEqual({ detail: 'Too many requests' });
      }
      await new Promise((resolve) => setTimeout(resolve, 1100));
      expect((await api1({ dealer_code: DEALER })).status()).toBe(200);
    });
  });

  // ===========================================================================
  // BEARER TOKENS — API1_TC_70_SEC onwards (the request-contract cases API1_TC_43-47_SEC cover a
  // missing, garbage or tampered token, a token in the query string and X-API-Key). Tokens the
  // server never issued must get 401 (403 only for a missing scope), never 200 or 500.
  // TC_74-76 forge tokens with the server's key (JWT_SECRET; the .env.example key on localhost).
  // ===========================================================================
  test.describe('Bearer tokens @security', () => {
    test.beforeEach(() => test.skip(!AUTH_ENABLED, 'server does not require a token (AUTH_ENABLED != true)'));
    const body = { dealer_code: DEALER, ...MONTH };
    const callWith = async (playwright, authorization, check) =>
      withHeaders(playwright, { Authorization: authorization }, async (ctx) => check(await ctx.post(API1, { data: body })));
    const needsKey = () => test.skip(!JWT_SECRET, 'set JWT_SECRET to the server\'s key to forge tokens');

    // A key with non-ASCII characters used to crash the old X-API-Key comparison (500, no security headers).
    test('API1_TC_70_SEC Non-ASCII Bearer token returns 401 Invalid token, never 500', async ({ playwright }) => {
      for (const token of ['kéy', `${await getToken()}é`, 'ÿ'.repeat(14)]) {
        await callWith(playwright, `Bearer ${token}`, (response) => expectUnauthorized(response, 'Invalid token'));
      }
    });

    test('API1_TC_71_SEC Other schemes (Basic with the client credentials, Token, JWT) return 401', async ({ playwright }) => {
      const token = await getToken();
      const basic = Buffer.from(`${CLIENT_ID}:${CLIENT_SECRET}`).toString('base64');
      for (const authorization of [`Basic ${basic}`, `Token ${token}`, `JWT ${token}`, token]) {
        await callWith(playwright, authorization, (response) => expectUnauthorized(response, 'Not authenticated'));
      }
    });

    test('API1_TC_72_SEC Unsigned token (alg "none") returns 401', async ({ playwright }) => {
      for (const alg of ['none', 'None', 'NONE']) {
        const unsigned = signJwt(tokenClaims(), '', { alg, typ: 'JWT' }).replace(/\.[^.]*$/, '.'); // empty signature
        await callWith(playwright, `Bearer ${unsigned}`,
          (response) => expectUnauthorized(response, 'Invalid token'));
      }
    });

    test('API1_TC_73_SEC Token signed with another key or algorithm returns 401', async ({ playwright }) => {
      const claims = tokenClaims();
      const tokens = [signJwt(claims, 'an-attacker-key-that-is-at-least-32-chars'), signJwt(claims, '')];
      const rs = crypto.generateKeyPairSync('rsa', { modulusLength: 2048 }).privateKey;
      const unsigned = `${Buffer.from(JSON.stringify({ alg: 'RS256', typ: 'JWT' })).toString('base64url')}.${Buffer.from(JSON.stringify(claims)).toString('base64url')}`;
      tokens.push(`${unsigned}.${crypto.sign('sha256', Buffer.from(unsigned), rs).toString('base64url')}`);
      for (const token of tokens) {
        await callWith(playwright, `Bearer ${token}`, (response) => expectUnauthorized(response, 'Invalid token'));
      }
    });

    test('API1_TC_74_SEC Expired token returns 401 Token expired with error="invalid_token"', async ({ playwright }) => {
      needsKey();
      const now = Math.floor(Date.now() / 1000);
      const token = signJwt(tokenClaims({ iat: now - 7200, nbf: now - 7200, exp: now - 120 }), JWT_SECRET);
      await callWith(playwright, `Bearer ${token}`, async (response) => {
        await expectUnauthorized(response, 'Token expired');
        expect(response.headers()['www-authenticate']).toContain('error_description="Token expired"');
      });
    });

    test('API1_TC_75_SEC Token without the schemes:read scope returns 403 Insufficient scope', async ({ playwright }) => {
      needsKey();
      for (const scope of ['', 'schemes:write', 'admin', undefined]) {
        await callWith(playwright, `Bearer ${signJwt(tokenClaims({ scope }), JWT_SECRET)}`, async (response) => {
          expect(response.status(), String(scope)).toBe(403);
          expect(await response.json()).toEqual({ detail: 'Insufficient scope' });
          expect(response.headers()['www-authenticate']).toContain('error="insufficient_scope"');
        });
      }
    });

    test('API1_TC_76_SEC Correctly signed token with a wrong claim returns 401 Invalid token', async ({ playwright }) => {
      needsKey();
      const now = Math.floor(Date.now() / 1000);
      const cases = {
        'unknown client (revoked)': { sub: 'not-a-registered-client' },
        'wrong audience': { aud: 'another-api' },
        'wrong issuer': { iss: 'https://evil.example.com' },
        'not valid yet': { nbf: now + 600, iat: now + 600 },
        'no exp': { exp: undefined },
        'no jti': { jti: undefined },
        'no sub': { sub: undefined },
      };
      for (const [name, overrides] of Object.entries(cases)) {
        await callWith(playwright, `Bearer ${signJwt(tokenClaims(overrides), JWT_SECRET)}`, async (response) => {
          expect(response.status(), name).toBe(401);
          await expectUnauthorized(response, 'Invalid token');
        });
      }
    });

    test('API1_TC_77 Valid token works with any case of "Bearer" and is reusable', async ({ playwright }) => {
      const token = await getToken();
      for (const scheme of ['Bearer', 'bearer', 'BEARER']) {
        await callWith(playwright, `${scheme} ${token}`, async (response) => {
          expect(response.status(), scheme).toBe(200);
          expect(await response.json()).toBeInstanceOf(Array);
        });
      }
    });
  });
});
