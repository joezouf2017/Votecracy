const BASE = '/api'

export async function fetchRandomQuestion() {
  const res = await fetch(`${BASE}/questions/random`)
  if (!res.ok) throw new Error('Failed to fetch question')
  return res.json()
}

export async function submitVote(questionId, choice) {
  const res = await fetch(`${BASE}/questions/${questionId}/vote`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ choice }),
  })
  if (!res.ok) throw new Error('Vote submission failed')
  return res.json()
}
