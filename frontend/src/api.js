/**
 * The entire contract between this frontend and the backend.
 *
 * Two things here are load-bearing enough that a rewritten frontend has to
 * carry them over, because getting either wrong fails quietly:
 *
 * 1. `credentials: 'include'` on every request. Daily mode identifies a player
 *    by an httpOnly cookie the backend issues. Omit this and nothing errors —
 *    you get 200s and a sensible-looking UI, but the cookie never travels, so
 *    every page load looks like a brand-new voter and anyone who already voted
 *    is asked to vote again. It is applied to all requests rather than only the
 *    daily ones so there is one rule to follow instead of two.
 *
 * 2. The status codes carry meaning. Treating every non-200 as a generic error
 *    is a UX regression, not just a cosmetic one:
 *
 *      409  already voted (another tab, a double submit) -> show their reveal
 *      403  asked for results without voting             -> send them to vote
 *      503  Redis is down, the vote was refused          -> retryable, not fatal
 *      400  choice is not one of the options
 *
 * Errors thrown here carry `.status` and `.detail` so callers can act on the
 * distinction without re-parsing responses.
 */

const BASE = '/api'

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, { credentials: 'include', ...options })

  if (!res.ok) {
    const err = new Error(`${options.method ?? 'GET'} ${path} failed with ${res.status}`)
    err.status = res.status
    try {
      err.detail = (await res.json()).detail
    } catch {
      // Non-JSON error body — the status is enough to act on.
    }
    throw err
  }

  return res.json()
}

function post(path, body) {
  return request(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function fetchRandomQuestion() {
  return request('/questions/random')
}

export function submitVote(questionId, choice) {
  return post(`/questions/${questionId}/vote`, { choice })
}

export function fetchDailyQuestion() {
  return request('/daily')
}

export function submitDailyVote(choice) {
  return post('/daily/vote', { choice })
}

export function fetchDailyResults() {
  return request('/daily/results')
}
