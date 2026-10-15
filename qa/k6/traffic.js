// One "user" iteration: API 1 (current-month) for a dealer from the pool.
import http from 'k6/http';
import { sleep } from 'k6';
import { API1, MONTH, params, pickDealer, checkApi1 } from './config.js';

// One request, no think time (used by rate.js, which sets the request rate itself).
export function oneRequest() {
  checkApi1(http.post(API1, JSON.stringify({ dealer_code: pickDealer(), ...MONTH }), params('current-month')));
}

export function userIteration() {
  oneRequest();
  sleep(Math.random() * 0.5 + 0.25); // 0.25-0.75 s think time
}
