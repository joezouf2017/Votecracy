/**
 * The two properties of the API layer that a frontend rewrite has to preserve.
 *
 * Both fail quietly if broken — no exception, no console error — so they get
 * tests rather than a comment.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

import {
  fetchDailyQuestion,
  fetchDailyResults,
  fetchRandomQuestion,
  submitDailyVote,
  submitVote,
} from './api'

function jsonResponse(body, { ok = true, status = 200 } = {}) {
  return { ok, status, json: async () => body }
}

beforeEach(() => {
  global.fetch = vi.fn().mockResolvedValue(jsonResponse({}))
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('credentials', () => {
  // The voter cookie is httpOnly, so this is the only thing that makes daily
  // mode able to recognise a returning player. Drop it and everything still
  // returns 200 — every visit just looks like a new voter.
  const calls = [
    ['fetchRandomQuestion', () => fetchRandomQuestion()],
    ['submitVote', () => submitVote('q1', 'Support')],
    ['fetchDailyQuestion', () => fetchDailyQuestion()],
    ['submitDailyVote', () => submitDailyVote('Support')],
    ['fetchDailyResults', () => fetchDailyResults()],
  ]

  it.each(calls)('%s sends credentials', async (_name, call) => {
    await call()
    const [, options] = global.fetch.mock.calls[0]
    expect(options.credentials).toBe('include')
  })
})

describe('error shape', () => {
  // Callers branch on these. A thrown Error without `.status` collapses 409,
  // 403 and 503 into one generic failure and takes the UX with it.
  it.each([409, 403, 503, 400, 404])(
    'exposes status %i on the thrown error',
    async (status) => {
      global.fetch.mockResolvedValue(
        jsonResponse({ detail: 'nope' }, { ok: false, status })
      )

      await expect(fetchDailyResults()).rejects.toMatchObject({
        status,
        detail: 'nope',
      })
    }
  )

  it('still throws with a status when the error body is not JSON', async () => {
    global.fetch.mockResolvedValue({
      ok: false,
      status: 502,
      json: async () => {
        throw new SyntaxError('Unexpected token < in JSON')
      },
    })

    await expect(fetchDailyResults()).rejects.toMatchObject({ status: 502 })
  })
})

describe('requests', () => {
  it('posts the choice as JSON', async () => {
    await submitDailyVote('Oppose')
    const [url, options] = global.fetch.mock.calls[0]

    expect(url).toBe('/api/daily/vote')
    expect(options.method).toBe('POST')
    expect(JSON.parse(options.body)).toEqual({ choice: 'Oppose' })
  })
})
