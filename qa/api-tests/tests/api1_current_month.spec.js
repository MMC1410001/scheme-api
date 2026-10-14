// API 1 — POST /v1/schemes/current-month: the dealer's rows for the 8 template components in the
// requested month. Body: {"dealer_code": ..., "month": 1-12, "year": 2000-2100}.
// Settings (API_BASE_URL, API_KEY, DEALER_CODE, SAMPLE_DATA, MONTH, YEAR, ...) are described in helpers.js.
const {
  test, expect, SAMPLE_DATA, DEALER, NEGATIVE_DEALER, UNKNOWN_DEALER, API1, MONTH, useClient,
  expectValidationError, registerRequestContract,
} = require('./helpers');

const FIELDS = ['Customer', 'FromDate', 'ToDate', 'ComponentDescription', 'Qualification', 'GsrB',
  'RebateRate', 'ClearCut', 'ShortfallAmount', 'ShortfallPct'];
const AMOUNTS = ['Qualification', 'GsrB', 'RebateRate', 'ClearCut', 'ShortfallAmount', 'ShortfallPct'];
const COMPONENTS = ['EB SLAB 1', 'EB SLAB 2', 'EB SLAB 3', 'VALUE', 'LUXURY', 'PREMIUM 01', 'PREMIUM 02', 'EB FULL MONTH'];
const MONTH_PREFIX = `${MONTH.year}-${String(MONTH.month).padStart(2, '0')}`;

test.describe('API 1 - Monthly schemes', () => {
  const client = useClient();
  // Every call sends the test month unless the body overrides it.
  const api1 = (body) => client().post(API1, { data: { ...MONTH, ...body } });
  const rowsFor = async (dealer) => (await api1({ dealer_code: dealer })).json();

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

    test('API1_TC_2 Rows have exactly the 10 template fields', async () => {
      for (const row of await rowsFor(DEALER)) expect(Object.keys(row).sort()).toEqual([...FIELDS].sort());
    });

    test('API1_TC_3 Only the 8 template components are returned', async () => {
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

    test('API1_TC_15 All rows belong to the requested month', async () => {
      for (const row of await rowsFor(DEALER)) {
        expect(row.ToDate.startsWith(MONTH_PREFIX) || row.FromDate.startsWith(MONTH_PREFIX)).toBe(true);
      }
    });

    test('API1_TC_16 [sample data] Sample dealer has all 8 components', async () => {
      test.skip(!SAMPLE_DATA, 'needs scripts/make_sample_data.py data');
      const rows = await rowsFor('6100000001');
      expect(rows.map((r) => r.ComponentDescription).sort()).toEqual([...COMPONENTS].sort());
    });

    test('API1_TC_17 [sample data] Latest SAP update wins (day-2 LUXURY values replace, not add)', async () => {
      test.skip(!SAMPLE_DATA, 'needs scripts/make_sample_data.py data');
      const luxury = (await rowsFor('6100000001')).find((r) => r.ComponentDescription === 'LUXURY');
      expect(luxury).toMatchObject({ Qualification: 315.1, GsrB: 159292.8, ClearCut: 1592.93, ShortfallPct: 21.23 });
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
  // EDGE, NEGATIVE, SECURITY — API1_TC_26 onwards (same rules as API 2; see helpers.js)
  // ===========================================================================
  registerRequestContract({ prefix: 'API1', path: API1, start: 26, baseBody: MONTH });
});
