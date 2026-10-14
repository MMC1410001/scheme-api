// API 2 IS ON HOLD (2026-10-15): this file is not run (testIgnore in playwright.config.js) and the
// endpoint is commented out in app/routes.py. Kept so the tests can be restored with the API.
// API 2 — POST /v1/schemes/report: the dealer's row from the latest generated report
// (newest month folder, then highest _v<N>). Keys are the report's two header rows joined and
// snake_cased, e.g. "Value Slab" + "GSR(Q) Value" -> "value_slab_gsr_q_value"; empty cells are null.
// Settings (API_BASE_URL, API_KEY, DEALER_CODE, SAMPLE_DATA, ...) are described in helpers.js.
const {
  test, expect, SAMPLE_DATA, DEALER, NEGATIVE_DEALER, UNKNOWN_DEALER, API2, useClient, registerRequestContract,
} = require('./helpers');

const FIRST_COLUMNS = ['zone', 'region', 'depot_code', 'customer', 'customer_name'];
const SNAKE_CASE = /^[a-z0-9]+(_[a-z0-9]+)*$/;

test.describe('API 2 - Dealer report', () => {
  const client = useClient();
  const api2 = (body) => client().post(API2, { data: body });
  const rowsFor = async (dealer) => (await api2({ dealer_code: dealer })).json();

  // ===========================================================================
  // FUNCTIONAL — API2_TC_1 to API2_TC_8 (valid requests, expecting 200)
  // ===========================================================================
  test.describe('Functional @functional', () => {
    test('API2_TC_1 Returns 200 and exactly one row for a dealer in the latest report', async () => {
      const response = await api2({ dealer_code: DEALER });
      expect(response.status()).toBe(200);
      const rows = await response.json();
      expect(Array.isArray(rows)).toBe(true);
      expect(rows).toHaveLength(1);
    });

    test('API2_TC_2 customer equals the requested dealer', async () => {
      const [row] = await rowsFor(DEALER);
      expect(row.customer).toBe(DEALER);
    });

    test('API2_TC_3 Columns start with zone, region, depot_code, customer, customer_name (file order)', async () => {
      const [row] = await rowsFor(DEALER);
      expect(Object.keys(row).slice(0, FIRST_COLUMNS.length)).toEqual(FIRST_COLUMNS);
    });

    test('API2_TC_4 Every key is snake_case', async () => {
      const [row] = await rowsFor(DEALER);
      for (const key of Object.keys(row)) expect(key).toMatch(SNAKE_CASE);
    });

    test('API2_TC_5 Accepts the dealer code as a JSON number', async () => {
      const response = await api2({ dealer_code: Number(DEALER) });
      expect(response.status()).toBe(200);
      expect(await response.json()).toEqual(await rowsFor(DEALER));
    });

    test('API2_TC_6 Ignores leading zeros in the dealer code', async () => {
      expect(await rowsFor(`000${DEALER}`)).toEqual(await rowsFor(DEALER));
    });

    test('API2_TC_7 Trims spaces around the dealer code', async () => {
      expect(await rowsFor(`  ${DEALER}  `)).toEqual(await rowsFor(DEALER));
    });

    test('API2_TC_8 Returns 200 [] for a dealer not in the report', async () => {
      const response = await api2({ dealer_code: UNKNOWN_DEALER });
      expect(response.status()).toBe(200);
      expect(await response.json()).toEqual([]);
    });
  });

  // ===========================================================================
  // DATA VALIDATION — API2_TC_9 to API2_TC_16 (response values against the report rules)
  // ===========================================================================
  test.describe('Data Validation @validation', () => {
    test('API2_TC_9 Values are numbers, strings or null (never objects or arrays)', async () => {
      const [row] = await rowsFor(DEALER);
      for (const [key, value] of Object.entries(row)) {
        expect(value === null || ['number', 'string'].includes(typeof value), key).toBe(true);
      }
    });

    test('API2_TC_10 Numbers carry no float32 noise (at most 7 significant digits, or 2 decimals)', async () => {
      const [row] = await rowsFor(DEALER);
      for (const [key, value] of Object.entries(row)) {
        if (typeof value !== 'number' || Number.isInteger(value)) continue;
        const clean = value === Number(value.toPrecision(7)) || value === Number(value.toFixed(2));
        expect(clean, `${key} = ${value}`).toBe(true);
      }
    });

    test('API2_TC_11 Every dealer gets the same columns in the same order', async () => {
      test.skip(!SAMPLE_DATA, 'needs a second known dealer (sample data)');
      const [a] = await rowsFor(DEALER);
      const [b] = await rowsFor(NEGATIVE_DEALER);
      expect(Object.keys(b)).toEqual(Object.keys(a));
    });

    test('API2_TC_12 [sample data] The highest version (v2) of the latest month is served, not v1', async () => {
      test.skip(!SAMPLE_DATA, 'needs scripts/make_sample_data.py data');
      const [row] = await rowsFor('6100000001');
      expect(row.value_slab_gsr_q_value).toBe(125000.5); // v1 has 120000
    });

    test('API2_TC_13 [sample data] float32 noise is removed (0.4000000059604645 -> 0.4)', async () => {
      test.skip(!SAMPLE_DATA, 'needs scripts/make_sample_data.py data');
      const [row] = await rowsFor('6100000001');
      expect(row.value_slab_value_cn_pct).toBe(0.4);
      expect(row.value_slab_short_fall_pct_value).toBe(66.86);
      expect(row.value_slab_short_fall_value).toBe(33431.36);
    });

    test('API2_TC_14 [sample data] Empty cells come back as null', async () => {
      test.skip(!SAMPLE_DATA, 'needs scripts/make_sample_data.py data');
      const [row] = await rowsFor('6100000001');
      expect(row).toHaveProperty('early_bird_clear_cut', null);
    });

    test('API2_TC_15 [sample data] Negative values are kept', async () => {
      test.skip(!SAMPLE_DATA, 'needs scripts/make_sample_data.py data');
      const [row] = await rowsFor(NEGATIVE_DEALER);
      expect(row.value_slab_gsr_q_value).toBe(-882);
    });

    test('API2_TC_16 [sample data] The group name keeps repeated column names apart', async () => {
      test.skip(!SAMPLE_DATA, 'needs scripts/make_sample_data.py data');
      const [row] = await rowsFor('6100000001');
      // "Clear Cut" appears under both Early Bird and Luxury in the file.
      expect(row).toHaveProperty('early_bird_clear_cut');
      expect(row).toHaveProperty('luxury_clear_cut', 1592.93);
    });
  });

  // ===========================================================================
  // EDGE, NEGATIVE, SECURITY — API2_TC_17 onwards (same rules as API 1; see helpers.js)
  // ===========================================================================
  registerRequestContract({ prefix: 'API2', path: API2, start: 17 });
});
