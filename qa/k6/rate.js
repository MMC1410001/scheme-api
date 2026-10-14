// Rate: a fixed number of requests per second, whatever the response time, like the dealer app
// loading data: up to 1 lakh requests a minute = ~1,700/s. Compare with REDIS_URL set
// and unset on the server (responses carry X-Cache: HIT/MISS when the cache is on).
//   k6 run rate.js                        # 1,700 requests/s for 2 minutes
//   RATE=600 DURATION=5m k6 run rate.js
//   DEALER_COUNT=500 k6 run rate.js       # fewer distinct dealers = more repeats (higher hit rate)
import { thresholds, summaryTrendStats, summaryTo } from './config.js';
import { oneRequest } from './traffic.js';

const RATE = parseInt(__ENV.RATE || '1700');

export const options = {
  scenarios: {
    rate: {
      executor: 'constant-arrival-rate',
      rate: RATE,
      timeUnit: '1s',
      duration: __ENV.DURATION || '2m',
      preAllocatedVUs: Math.min(RATE, 400),
      maxVUs: parseInt(__ENV.MAX_VUS || '2000'),
    },
  },
  thresholds: { ...thresholds, dropped_iterations: ['count<' + Math.ceil(RATE * 0.01 * 120)] }, // < 1% not sent
  summaryTrendStats,
};

export default oneRequest;
export const handleSummary = summaryTo('rate');
