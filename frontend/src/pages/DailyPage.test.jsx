/**
 * Daily mode's client behaviour, centred on the status codes.
 *
 * The backend distinguishes 409 (you already voted), 403 (you have not) and
 * 503 (the vote was refused, try again). A page that renders "something went
 * wrong" for all three is a regression that no backend test would catch, so it
 * gets caught here.
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, beforeEach } from 'vitest'

import DailyPage from './DailyPage'
import { fetchDailyQuestion, fetchDailyResults, submitDailyVote } from '../api'

vi.mock('../api', () => ({
  fetchDailyQuestion: vi.fn(),
  fetchDailyResults: vi.fn(),
  submitDailyVote: vi.fn(),
}))

const QUESTION = {
  id: 'us-medicare-1965',
  category: 'medical',
  era: 'historical',
  prompt: 'It is 1965. Do you vote for Medicare?',
  options: ['Support', 'Oppose'],
  day: '2026-03-14',
  already_voted: false,
}

const RESULTS = {
  question_id: QUESTION.id,
  day: QUESTION.day,
  your_choice: 'Support',
  actual_vote: 'Passed 307-116 in the House',
  outcome: 'Medicare enrolled 19 million Americans in its first year.',
  source: 'Social Security Amendments of 1965',
  tally_available: false,
  tally_available_at: '2026-03-15T00:00:00Z',
  tally: null,
  total_votes: null,
}

function httpError(status, detail) {
  const err = new Error(`failed with ${status}`)
  err.status = status
  err.detail = detail
  return err
}

beforeEach(() => {
  vi.clearAllMocks()
  fetchDailyQuestion.mockResolvedValue(QUESTION)
  fetchDailyResults.mockResolvedValue(RESULTS)
  submitDailyVote.mockResolvedValue(RESULTS)
})

describe('before voting', () => {
  it('shows the question and no part of the outcome', async () => {
    render(<DailyPage />)

    expect(await screen.findByText(QUESTION.prompt)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Support' })).toBeInTheDocument()

    // Rule #1, mirrored on the client. The server would not send these anyway,
    // but a page that renders a stale reveal from a previous question would
    // leak one all the same.
    expect(screen.queryByText(/History Reveals/i)).not.toBeInTheDocument()
    expect(screen.queryByText(RESULTS.outcome)).not.toBeInTheDocument()
    expect(screen.queryByText(RESULTS.actual_vote)).not.toBeInTheDocument()
  })

  it('does not ask for results it is not entitled to', async () => {
    render(<DailyPage />)
    await screen.findByText(QUESTION.prompt)

    expect(fetchDailyResults).not.toHaveBeenCalled()
  })
})

describe('voting', () => {
  it('shows the reveal after a successful vote', async () => {
    render(<DailyPage />)
    await userEvent.click(await screen.findByRole('button', { name: 'Support' }))

    expect(await screen.findByText(RESULTS.outcome)).toBeInTheDocument()
    expect(screen.getByText(RESULTS.actual_vote)).toBeInTheDocument()
  })

  it('hides the community split while the day is still open', async () => {
    render(<DailyPage />)
    await userEvent.click(await screen.findByRole('button', { name: 'Support' }))
    await screen.findByText(RESULTS.outcome)

    expect(screen.getByText(/Unlocks when/i)).toBeInTheDocument()
    expect(screen.queryByText(/%/)).not.toBeInTheDocument()
  })

  it('shows the split once the day has closed', async () => {
    submitDailyVote.mockResolvedValue({
      ...RESULTS,
      tally_available: true,
      tally: { Support: 3, Oppose: 1 },
      total_votes: 4,
    })

    render(<DailyPage />)
    await userEvent.click(await screen.findByRole('button', { name: 'Support' }))

    expect(await screen.findByText(/75% \(3\)/)).toBeInTheDocument()
    expect(screen.getByText(/25% \(1\)/)).toBeInTheDocument()
    expect(screen.getByText('4 votes')).toBeInTheDocument()
  })
})

describe('status codes', () => {
  it('409 shows the reveal rather than an error', async () => {
    // Another tab already cast this player's vote. Their vote stands — the
    // only correct response is to show them what they voted for.
    submitDailyVote.mockRejectedValue(httpError(409, "You've already voted on today's question."))

    render(<DailyPage />)
    await userEvent.click(await screen.findByRole('button', { name: 'Support' }))

    expect(await screen.findByText(RESULTS.outcome)).toBeInTheDocument()
    expect(fetchDailyResults).toHaveBeenCalled()
    expect(screen.queryByText(/already voted/i)).not.toBeInTheDocument()
  })

  it('503 keeps the player on the vote screen with a retryable message', async () => {
    submitDailyVote.mockRejectedValue(
      httpError(503, 'Voting is temporarily unavailable. Please try again in a moment.'),
    )

    render(<DailyPage />)
    await userEvent.click(await screen.findByRole('button', { name: 'Support' }))

    expect(await screen.findByText(/temporarily unavailable/i)).toBeInTheDocument()
    // Still votable — a refused vote is not a dead end.
    expect(screen.getByRole('button', { name: 'Support' })).toBeInTheDocument()
    expect(screen.queryByText(RESULTS.outcome)).not.toBeInTheDocument()
  })

  it('a returning player goes straight to their reveal', async () => {
    // The cookie is what makes this possible. If credentials stop being sent,
    // already_voted comes back false forever and this test fails.
    fetchDailyQuestion.mockResolvedValue({ ...QUESTION, already_voted: true })

    render(<DailyPage />)

    expect(await screen.findByText(RESULTS.outcome)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Support' })).not.toBeInTheDocument()
  })
})

describe('when the backend is unreachable', () => {
  it('says so instead of rendering an empty page', async () => {
    fetchDailyQuestion.mockRejectedValue(new TypeError('Failed to fetch'))

    render(<DailyPage />)

    await waitFor(() => expect(screen.getByText(/Could not load/i)).toBeInTheDocument())
  })
})
