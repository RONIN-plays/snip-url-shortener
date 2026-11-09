// Chaos test traffic: steady load while chaos.sh kills Redis and Postgres.
//
// Traffic mix (a realistic hot/cold split):
//   80%  "head" links  - a few popular codes, almost always cached in Redis
//   20%  "tail" links  - 20,000 rarely-hit codes (seeded by chaos.sh); most requests
//                        are cache MISSES, so they truly need Postgres
//
// Results are bucketed into 5-second windows (b0, b1, ...) so we can see exactly
// when things broke and when they recovered. analyze_chaos.py turns this into a table.
import http from 'k6/http';
import { Counter, Trend } from 'k6/metrics';

const BASE = __ENV.BASE || 'http://api:8000';
const RATE = parseInt(__ENV.RATE || '800');
const DURATION_S = parseInt(__ENV.DURATION_S || '75');
const BUCKET_MS = 5000;
const NB = Math.ceil(DURATION_S / 5) + 1;
const CLASSES = ['302', '404', '429', '5xx', 'err'];   // err = no HTTP response (timeout/refused)

const responses = new Counter('responses');
const latency = new Trend('chaos_latency', true);

// k6 only exports per-tag sub-metrics that have a threshold, so declare a
// do-nothing (always-true) threshold for every bucket/class combination.
const thresholds = {};
for (let b = 0; b < NB; b++) {
  thresholds[`chaos_latency{b:${b}}`] = ['max>=0'];
  for (const c of CLASSES) thresholds[`responses{b:${b},c:${c}}`] = ['count>=0'];
}

export const options = {
  scenarios: {
    chaos: {
      executor: 'constant-arrival-rate',
      rate: RATE, timeUnit: '1s', duration: `${DURATION_S}s`,
      preAllocatedVUs: 200, maxVUs: 3000,
    },
  },
  thresholds,
  summaryTrendStats: ['med', 'p(95)', 'p(99)', 'max'],
};

export function setup() {
  const head = [];
  for (let i = 0; i < 200; i++) {
    const r = http.post(`${BASE}/api/links`, JSON.stringify({ url: `https://example.com/h/${i}` }),
      { headers: { 'Content-Type': 'application/json' } });
    if (r.status !== 201) throw new Error(`setup failed: ${r.status}`);
    head.push(r.json('code'));
  }
  const t0 = Date.now();
  console.log(`T0 ${t0}`);          // chaos.sh waits for this line, then starts its timeline
  return { head, t0 };
}

export default function (d) {
  const code = Math.random() < 0.8
    ? d.head[Math.floor(Math.random() * d.head.length)]
    : `tail-${1 + Math.floor(Math.random() * 20000)}`;
  const res = http.get(`${BASE}/${code}`, { redirects: 0, timeout: '5s' });
  const b = Math.min(NB - 1, Math.floor((Date.now() - d.t0) / BUCKET_MS));
  const c = res.status === 302 ? '302' : res.status === 404 ? '404' : res.status === 429 ? '429'
          : res.status >= 500 ? '5xx' : 'err';
  responses.add(1, { b: String(b), c });
  latency.add(res.timings.duration, { b: String(b) });
}

export function handleSummary(data) {
  const out = [];
  for (let b = 0; b < NB; b++) {
    const row = { bucket: b, t: b * 5 };
    for (const c of CLASSES) {
      const m = data.metrics[`responses{b:${b},c:${c}}`];
      row[c] = m ? m.values.count : 0;
    }
    const l = data.metrics[`chaos_latency{b:${b}}`];
    row.p50 = l ? l.values.med : null;
    row.p95 = l ? l.values['p(95)'] : null;
    row.p99 = l ? l.values['p(99)'] : null;
    row.max = l ? l.values.max : null;
    out.push(row);
  }
  return { [`/scripts/results/${__ENV.NAME || 'chaos'}.json`]: JSON.stringify(out) };
}
