// Stress: step up beyond normal load to find where latency or errors break down.
// Thresholds are reported but do not abort the run, so you see the whole curve.
//   k6 run stress.js
//   MAX_VUS=1000 k6 run stress.js
import { thresholds, summaryTrendStats, summaryTo, authSetup } from './config.js';
import { userIteration } from './traffic.js';

const MAX = parseInt(__ENV.MAX_VUS || '600');

export const options = {
  scenarios: {
    stress: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '30s', target: Math.round(MAX * 0.25) },
        { duration: '1m', target: Math.round(MAX * 0.25) },
        { duration: '30s', target: Math.round(MAX * 0.5) },
        { duration: '1m', target: Math.round(MAX * 0.5) },
        { duration: '30s', target: MAX },
        { duration: '1m', target: MAX },
        { duration: '30s', target: 0 },
      ],
      gracefulRampDown: '10s',
    },
  },
  thresholds,
  summaryTrendStats,
};

export default userIteration;
export const setup = authSetup; // one token for the whole run
export const handleSummary = summaryTo('stress');
