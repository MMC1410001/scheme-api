// API-only suite: no browsers are launched.
const { defineConfig } = require('@playwright/test');

module.exports = defineConfig({
  testDir: './tests',
  // API 2 is on hold (2026-10-15): its spec is not run. Remove this line (and restore the endpoint) to run it.
  testIgnore: ['**/api2_report.spec.js'],
  timeout: 30_000,
  fullyParallel: true,
  reporter: [['list'], ['html', { open: 'never' }], ['json', { outputFile: 'test-results/results.json' }]],
  use: {
    baseURL: process.env.API_BASE_URL || 'http://localhost:8000',
  },
});
