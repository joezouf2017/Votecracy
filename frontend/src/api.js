const BASE = '/api'

// Daily mode identifies the player with an httpOnly cookie the backend issues,
// so every daily request has to carry credentials — including cross-origin,
// once the frontend isn't served through the dev proxy.
const withCookies = { credentials: 'include' }

async function json(res, fallbackMessage) {
  if (!res.ok) {
    const err = new Error(fallbackMessage)
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

export async function fetchRandomQuestion() {
  return json(await fetch(`${BASE}/questions/random`), 'Failed to fetch question')
}

export async function submitVote(questionId, choice) {
  const res = await fetch(`${BASE}/questions/${questionId}/vote`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ choice }),
  })
  return json(res, 'Vote submission failed')
}

export async function fetchDailyQuestion() {
  return json(await fetch(`${BASE}/daily`, withCookies), "Failed to fetch today's question")
}

export async function submitDailyVote(choice) {
  const res = await fetch(`${BASE}/daily/vote`, {
    ...withCookies,
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ choice }),
  })
  return json(res, 'Vote submission failed')
}

export async function fetchDailyResults() {
  return json(await fetch(`${BASE}/daily/results`, withCookies), 'Failed to fetch results')
}
