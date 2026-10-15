// Spike: a sudden burst (e.g. The client batch job starting), then back to normal.
// Checks that the API survives and recovers without errors.
//   k6 run spike.js
//   SPIKE_VUS=1000 k6 run spike.js
import { thresholds, summaryTrendStats, summaryTo, authSetup } from './config.js';
import { userIteration } from './traffic.js';

const BASE = parseInt(__ENV.BASE_VUS || '20');
const SPIKE = parseInt(__ENV.SPIKE_VUS || '500');

export const options = {
  scenarios: {
    spike: {
      executor: 'ramping-vus',
      startVUs: BASE,
      stages: [
        { duration: '30s', target: BASE },
        { duration: '10s', target: SPIKE }, // burst
        { duration: '1m', target: SPIKE },
        { duration: '10s', target: BASE },  // recovery
        { duration: '30s', target: BASE },
        { duration: '5s', target: 0 },
      ],
      gracefulRampDown: '10s',
    },
  },
  thresholds,
  summaryTrendStats,
};

export default userIteration;
export const setup = authSetup; // one token for the whole run
export const handleSummary = summaryTo('spike');
