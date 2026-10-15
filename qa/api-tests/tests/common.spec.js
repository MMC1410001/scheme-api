// Checks that belong to the server, not to API 1: health, unknown routes, HTTPS.
const { test, expect, BASE_URL, newApi, useClient } = require('./helpers');

test.describe('Common', () => {
  const client = useClient();

  test('COMMON_TC_1 GET /health returns 200 {ok: true} @functional', async () => {
    const response = await client().get('/health');
    expect(response.status()).toBe(200);
    expect(await response.json()).toEqual({ ok: true });
  });

  test('COMMON_TC_2 Unknown route returns 404 @negative', async () => {
    expect((await client().post('/v1/schemes/unknown', { data: { dealer_code: '1' } })).status()).toBe(404);
  });

  test('COMMON_TC_3_SEC /health stays open without a key (liveness probe) @security', async ({ playwright }) => {
    const ctx = await newApi(playwright, {});
    expect((await ctx.get('/health')).status()).toBe(200);
    await ctx.dispose();
  });

  test('COMMON_TC_4_SEC Plain HTTP is redirected or refused (deployed environments only) @security', async ({ playwright }) => {
    test.skip(!BASE_URL.startsWith('https://'), 'local run over http; check on the deployed URL');
    const ctx = await playwright.request.newContext({ maxRedirects: 0 });
    let status = 0;
    try {
      status = (await ctx.get(`${BASE_URL.replace('https://', 'http://')}/health`)).status();
    } catch {
      status = -1; // connection refused counts as a pass
    }
    expect([-1, 301, 302, 307, 308]).toContain(status);
    await ctx.dispose();
  });
});
