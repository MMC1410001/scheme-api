// Smoke: 1 VU for 30 s. Run before any heavier test; it must pass cleanly.
//   k6 run smoke.js
import http from 'k6/http';
import { check } from 'k6';
import { BASE_URL, thresholds, summaryTrendStats, summaryTo } from './config.js';
import { mixedTraffic } from './traffic.js';

export const options = {
  vus: 1,
  duration: '30s',
  thresholds,
  summaryTrendStats,
};

export function setup() {
  const res = http.get(`${BASE_URL}/health`);
  if (!check(res, { 'health is 200': (r) => r.status === 200 })) {
    throw new Error(`API not healthy at ${BASE_URL} (status ${res.status})`);
  }
}

export default mixedTraffic;
export const handleSummary = summaryTo('smoke');
