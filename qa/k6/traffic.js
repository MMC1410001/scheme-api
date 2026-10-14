// One "user" iteration. Which API is called is set with -e API=...:
//   api1   (default) only API 1 (current-month)
//   api2, both: API 2 is on hold (2026-10-15), so these are refused until it is restored
import http from 'k6/http';
import { sleep } from 'k6';
import { API1, API2, MONTH, params, pickDealer, checkApi1, checkApi2 } from './config.js';

const TARGET = (__ENV.API || 'api1').toLowerCase();
if (TARGET === 'api2' || TARGET === 'both') throw new Error('API 2 is on hold: run with -e API=api1 (the default)');
const API1_SHARE = { api1: 1 /* , api2: 0, both: 0.8 */ }[TARGET];
if (API1_SHARE === undefined) throw new Error(`API must be api1 (got "${__ENV.API}")`);

// One request, no think time (used by rate.js, which sets the request rate itself).
export function oneRequest() {
  const dealer = pickDealer();
  if (Math.random() < API1_SHARE) {
    checkApi1(http.post(API1, JSON.stringify({ dealer_code: dealer, ...MONTH }), params('current-month')));
  } else {
    checkApi2(http.post(API2, JSON.stringify({ dealer_code: dealer }), params('report')));
  }
}

export function mixedTraffic() {
  oneRequest();
  sleep(Math.random() * 0.5 + 0.25); // 0.25-0.75 s think time
}
