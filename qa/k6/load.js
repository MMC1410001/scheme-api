// Load: expected busy-hour traffic. Ramps to TARGET_VUS (default 150, as in the dealer
// API dev tests), holds for HOLD (default 3m), ramps down.
//   k6 run load.js
//   TARGET_VUS=300 HOLD=10m k6 run load.js
import { thresholds, summaryTrendStats, summaryTo } from './config.js';
import { mixedTraffic } from './traffic.js';

const TARGET = parseInt(__ENV.TARGET_VUS || '150');

export const options = {
  scenarios: {
    load: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '30s', target: TARGET },
        { duration: __ENV.HOLD || '3m', target: TARGET },
        { duration: '15s', target: 0 },
      ],
      gracefulRampDown: '10s',
    },
  },
  thresholds,
  summaryTrendStats,
};

export default mixedTraffic;
export const handleSummary = summaryTo('load');
