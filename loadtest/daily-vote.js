/**
 * Phase 2 gate: does the vote endpoint stay correct under concurrency?
 *
 * Every iteration is a *distinct* anonymous voter (a fresh 32-hex cookie), so
 * the expected result is simple and checkable: the final Redis tally and the
 * Postgres row count must both equal the number of 200 responses, exactly.
 * Any mismatch is a lost or double-counted vote — i.e. a race condition.
 *
 * Run via loadtest/run.sh, which resets state first and verifies the counts
 * afterwards. Running this script alone only produces latency numbers.
 */
import http from 'k6/http'
import { check } from 'k6'
import { Counter } from 'k6/metrics'

const accepted = new Counter('votes_accepted')
const rejected_duplicate = new Counter('votes_rejected_duplicate')

// Default is a ramp. Set VUS to pin a single concurrency level instead —
// that's how the per-level p50/p95/p99 table in docs/metrics is produced,
// since a ramp's aggregate percentiles blend all the levels together.
const FIXED_VUS = __ENV.VUS ? parseInt(__ENV.VUS, 10) : 0

export const options = FIXED_VUS
  ? {
      vus: FIXED_VUS,
      duration: __ENV.DURATION || '30s',
      summaryTrendStats: ['avg', 'min', 'med', 'p(95)', 'p(99)', 'max'],
    }
  : {
      stages: [
        { duration: '20s', target: 50 },
        { duration: '20s', target: 200 },
        { duration: '30s', target: 500 },
        { duration: '10s', target: 0 },
      ],
      summaryTrendStats: ['avg', 'min', 'med', 'p(95)', 'p(99)', 'max'],
    }

const BASE = __ENV.BASE_URL || 'http://backend:8000'
const HEX = '0123456789abcdef'

function voterId() {
  let s = ''
  for (let i = 0; i < 32; i++) s += HEX[Math.floor(Math.random() * 16)]
  return s
}

export default function () {
  const choice = Math.random() < 0.5 ? 'Support' : 'Oppose'
  const res = http.post(`${BASE}/api/daily/vote`, JSON.stringify({ choice }), {
    headers: {
      'Content-Type': 'application/json',
      Cookie: `votecracy_voter=${voterId()}`,
    },
  })

  if (res.status === 200) accepted.add(1)
  else if (res.status === 409) rejected_duplicate.add(1)

  check(res, { 'vote accepted (200)': (r) => r.status === 200 })
}
