// Redirect load test.
//   docker compose --profile load run --rm k6 run -e RATE=1000 -e DURATION=30s \
//       --summary-export /scripts/results/cache-on.json /scripts/redirect.js
//
// "constant-arrival-rate" = open-model load: k6 starts RATE new requests every
// second no matter how slowly the server answers. (A fixed number of looping
// users would slow down when the server slows down and hide the problem -
// "coordinated omission".)
import http from 'k6/http';
import { check } from 'k6';

const BASE = __ENV.BASE || 'http://api:8000';
const RATE = parseInt(__ENV.RATE || '1000');

export const options = {
  scenarios: {
    redirects: {
      executor: 'constant-arrival-rate',
      rate: RATE,
      timeUnit: '1s',
      duration: __ENV.DURATION || '30s',
      preAllocatedVUs: 100,
      maxVUs: 2000,
    },
  },
  summaryTrendStats: ['avg', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

// Runs once before the test: create a pool of links to redirect to.
export function setup() {
  const codes = [];
  for (let i = 0; i < 500; i++) {
    const r = http.post(`${BASE}/api/links`,
      JSON.stringify({ url: `https://example.com/page/${i}` }),
      { headers: { 'Content-Type': 'application/json' } });
    if (r.status !== 201) throw new Error(`setup failed: ${r.status} ${r.body}`);
    codes.push(r.json('code'));
  }
  return codes;
}

export default function (codes) {
  const code = codes[Math.floor(Math.random() * codes.length)];
  // redirects: 0 -> measure OUR response (the 302), don't follow it to example.com
  const r = http.get(`${BASE}/${code}`, { redirects: 0 });
  check(r, { 'is 302': (x) => x.status === 302 });
}
