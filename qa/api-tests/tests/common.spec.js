// Checks that belong to the server, not to API 1: health, unknown routes, HTTPS, and the OAuth2
// token endpoint POST /v1/auth/token (COMMON_TC_5 onwards; RFC 6749 client credentials).
const {
  test, expect, AUTH_ENABLED, BASE_URL, CLIENT_ID, CLIENT_SECRET, API1, TOKEN_PATH, SCOPE, MONTH, DEALER,
  newApi, withHeaders, getToken, useClient,
} = require('./helpers');

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

  test('COMMON_TC_3_SEC /health stays open without a token (liveness probe) @security', async ({ playwright }) => {
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

  // =========================================================================
  // TOKEN ENDPOINT — COMMON_TC_5 onwards. About 20 token requests here (plus one per worker): the endpoint allows
  // TOKEN_RATE_LIMIT_PER_MINUTE (default 30) per IP, so wait a minute before another full run.
  // =========================================================================
  test.describe('Token endpoint (OAuth2 client credentials)', () => {
    test.beforeEach(() => test.skip(!AUTH_ENABLED, 'server does not require a token (AUTH_ENABLED != true)'));
    const form = (params) => new URLSearchParams(params).toString();
    const FORM = { 'Content-Type': 'application/x-www-form-urlencoded' };
    const GOOD = { grant_type: 'client_credentials', client_id: CLIENT_ID, client_secret: CLIENT_SECRET };
    const basic = (id = CLIENT_ID, secret = CLIENT_SECRET) =>
      `Basic ${Buffer.from(`${encodeURIComponent(id)}:${encodeURIComponent(secret)}`).toString('base64')}`;
    // Every token request goes out with no Authorization unless the case sets one.
    const tokenCall = async (playwright, options, check) => withHeaders(playwright, {}, async (ctx) => check(await ctx.post(TOKEN_PATH, options)));

    async function expectToken(response) {
      expect(response.status(), await response.text()).toBe(200);
      const body = await response.json();
      expect(Object.keys(body).sort()).toEqual(['access_token', 'expires_in', 'scope', 'token_type']);
      expect(body.token_type).toBe('Bearer');
      expect(body.scope).toBe(SCOPE);
      expect(body.expires_in).toBe(Number(process.env.JWT_TTL_SECONDS || 3600)); // the server's JWT_TTL_SECONDS
      expect(body.access_token).toMatch(/^[\w-]+\.[\w-]+\.[\w-]+$/);
      const claims = JSON.parse(Buffer.from(body.access_token.split('.')[1], 'base64url').toString());
      expect(claims).toMatchObject({ iss: 'scheme-api', aud: 'scheme-api', sub: CLIENT_ID, scope: SCOPE });
      expect(claims.exp - claims.iat).toBe(body.expires_in);
      expect(JSON.stringify(claims)).not.toContain(CLIENT_SECRET);
      return body.access_token;
    }

    // RFC 6749 section 5.2 error: status, {error, error_description}, never a stack trace or the secret.
    async function expectOAuthError(response, statusCode, error) {
      const text = await response.text();
      expect(response.status(), text).toBe(statusCode);
      const body = JSON.parse(text);
      expect(Object.keys(body).sort()).toEqual(['error', 'error_description']);
      expect(body.error).toBe(error);
      expect(response.headers()['cache-control']).toContain('no-store');
      if (statusCode === 401) expect(response.headers()['www-authenticate']).toMatch(/^Basic realm="scheme-api"/);
      for (const leak of ['Traceback', '.py"', CLIENT_SECRET, 'wrong-secret-qa']) if (leak) expect(text).not.toContain(leak);
    }

    test('COMMON_TC_5 Form-encoded client credentials return a Bearer token that works on API 1 @functional', async ({ playwright }) => {
      await tokenCall(playwright, { data: form({ ...GOOD, scope: SCOPE }), headers: FORM }, async (response) => {
        const token = await expectToken(response);
        await withHeaders(playwright, { Authorization: `Bearer ${token}` }, async (ctx) =>
          expect((await ctx.post(API1, { data: { dealer_code: DEALER, ...MONTH } })).status()).toBe(200));
      });
    });

    test('COMMON_TC_6 JSON body with client credentials returns a token (scope optional) @functional', async ({ playwright }) => {
      await tokenCall(playwright, { data: GOOD }, expectToken);
    });

    test('COMMON_TC_7 HTTP Basic client authentication returns a token @functional', async ({ playwright }) => {
      await tokenCall(playwright, { data: form({ grant_type: 'client_credentials' }), headers: { ...FORM, Authorization: basic() } }, expectToken);
    });

    test('COMMON_TC_8_SEC Wrong client_secret returns 401 invalid_client with a Basic challenge @security', async ({ playwright }) => {
      await tokenCall(playwright, { data: form({ ...GOOD, client_secret: 'wrong-secret-qa' }), headers: FORM },
        (response) => expectOAuthError(response, 401, 'invalid_client'));
    });

    test('COMMON_TC_9_SEC Unknown client_id returns 401 invalid_client (same as a wrong secret) @security', async ({ playwright }) => {
      await tokenCall(playwright, { data: form({ ...GOOD, client_id: 'unknown-client-qa' }), headers: FORM }, async (response) => {
        await expectOAuthError(response, 401, 'invalid_client');
        expect((await response.json()).error_description).toBe('Client authentication failed');
      });
    });

    test('COMMON_TC_10_SEC Missing grant_type returns 400 invalid_request @security', async ({ playwright }) => {
      const { grant_type, ...rest } = GOOD;
      await tokenCall(playwright, { data: form(rest), headers: FORM }, (response) => expectOAuthError(response, 400, 'invalid_request'));
    });

    test('COMMON_TC_11_SEC Other grant types (password, authorization_code) return 400 unsupported_grant_type @security', async ({ playwright }) => {
      await tokenCall(playwright, { data: form({ ...GOOD, grant_type: 'password', username: 'a', password: 'b' }), headers: FORM },
        (response) => expectOAuthError(response, 400, 'unsupported_grant_type'));
    });

    test('COMMON_TC_12_SEC Scope other than schemes:read returns 400 invalid_scope @security', async ({ playwright }) => {
      await tokenCall(playwright, { data: form({ ...GOOD, scope: `${SCOPE} admin` }), headers: FORM },
        (response) => expectOAuthError(response, 400, 'invalid_scope'));
    });

    test('COMMON_TC_13_SEC Credentials in both Basic and the body return 400 invalid_request @security', async ({ playwright }) => {
      await tokenCall(playwright, { data: form(GOOD), headers: { ...FORM, Authorization: basic() } },
        (response) => expectOAuthError(response, 400, 'invalid_request'));
    });

    test('COMMON_TC_14_SEC GET on the token endpoint returns 405 (credentials never in a URL) @security', async ({ playwright }) => {
      await withHeaders(playwright, {}, async (ctx) =>
        expect((await ctx.get(`${TOKEN_PATH}?${form(GOOD)}`)).status()).toBe(405));
    });

    test('COMMON_TC_15_SEC Token responses (success and error) are not cached: Cache-Control no-store, Pragma no-cache @security', async ({ playwright }) => {
      for (const data of [form(GOOD), form({ ...GOOD, client_secret: 'wrong-secret-qa' })]) {
        await tokenCall(playwright, { data, headers: FORM }, async (response) => {
          expect(response.headers()['cache-control']).toContain('no-store');
          expect(response.headers()['pragma']).toBe('no-cache');
          expect(response.headers()['x-content-type-options']).toBe('nosniff');
        });
      }
    });

    test('COMMON_TC_16_SEC Unsupported content type or unparseable body returns 400 invalid_request @security', async ({ playwright }) => {
      const cases = [
        { data: form(GOOD), headers: { 'Content-Type': 'text/plain' } },
        { data: Buffer.from('{"grant_type": '), headers: { 'Content-Type': 'application/json' } },
        { data: Buffer.from('["client_credentials"]'), headers: { 'Content-Type': 'application/json' } },
        { data: Buffer.from(JSON.stringify({ ...GOOD, client_secret: 123 })), headers: { 'Content-Type': 'application/json' } },
      ];
      for (const options of cases) {
        await tokenCall(playwright, options, (response) => expectOAuthError(response, 400, 'invalid_request'));
      }
    });

    test('COMMON_TC_17_SEC A parameter sent twice returns 400 invalid_request (no parameter pollution) @security', async ({ playwright }) => {
      await tokenCall(playwright, { data: `${form(GOOD)}&client_id=unknown-client-qa`, headers: FORM },
        (response) => expectOAuthError(response, 400, 'invalid_request'));
    });

    test('COMMON_TC_18_SEC Missing client_secret, empty secret or malformed Basic returns 401 invalid_client @security', async ({ playwright }) => {
      const cases = [
        { data: form({ grant_type: 'client_credentials', client_id: CLIENT_ID }), headers: FORM },
        { data: form({ ...GOOD, client_secret: '' }), headers: FORM },
        { data: form({ grant_type: 'client_credentials' }), headers: { ...FORM, Authorization: 'Basic not-base64!' } },
      ];
      for (const options of cases) {
        await tokenCall(playwright, options, (response) => expectOAuthError(response, 401, 'invalid_client'));
      }
    });

    // Uses up the token limit for this IP for up to a minute, so it runs only when asked:
    //   TOKEN_RATE_LIMIT=30 AUTH_ENABLED=true npx playwright test -g COMMON_TC_19
    test('COMMON_TC_19_SEC More than TOKEN_RATE_LIMIT_PER_MINUTE token requests get 429 with Retry-After @security', async ({ playwright }) => {
      const LIMIT = Number(process.env.TOKEN_RATE_LIMIT || 0);
      test.skip(!LIMIT, 'set TOKEN_RATE_LIMIT to the server\'s TOKEN_RATE_LIMIT_PER_MINUTE and run this case on its own');
      await getToken(); // this worker's token, fetched before the limit is used up
      await withHeaders(playwright, {}, async (ctx) => {
        const bad = form({ ...GOOD, client_secret: 'wrong-secret-qa' }); // failed attempts count too
        const responses = await Promise.all(Array.from({ length: LIMIT + 5 }, () => ctx.post(TOKEN_PATH, { data: bad, headers: FORM })));
        const statuses = responses.map((r) => r.status());
        expect(statuses.filter((s) => s >= 500)).toEqual([]);
        const refused = responses.filter((r) => r.status() === 429);
        expect(refused.length).toBeGreaterThanOrEqual(5);
        for (const response of refused) {
          const retryAfter = Number(response.headers()['retry-after']);
          expect(retryAfter).toBeGreaterThanOrEqual(1);
          expect(retryAfter).toBeLessThanOrEqual(60);
          expect(await response.json()).toEqual({ detail: 'Too many requests' });
        }
        // The data endpoint has its own limit: an existing token keeps working.
        const api = await newApi(playwright);
        try {
          expect((await api.post(API1, { data: { dealer_code: DEALER, ...MONTH } })).status()).toBe(200);
        } finally {
          await api.dispose();
        }
      });
    });
  });
});
