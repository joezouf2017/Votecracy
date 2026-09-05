import { useState, useEffect, useCallback } from 'react'
import { fetchDailyQuestion, submitDailyVote, fetchDailyResults } from '../api'
import QuestionCard from '../components/QuestionCard'
import RevealCard from '../components/RevealCard'
import TallyPanel from '../components/TallyPanel'

export default function DailyPage() {
  const [phase, setPhase] = useState('loading') // loading | voting | revealed | error
  const [question, setQuestion] = useState(null)
  const [results, setResults] = useState(null)
  const [error, setError] = useState(null)

  const load = useCallback(async () => {
    setPhase('loading')
    setError(null)
    try {
      const q = await fetchDailyQuestion()
      setQuestion(q)
      // The backend remembers this player by cookie, so a returning visitor
      // goes straight back to their reveal instead of being asked to vote again.
      if (q.already_voted) {
        setResults(await fetchDailyResults())
        setPhase('revealed')
      } else {
        setPhase('voting')
      }
    } catch {
      setError('Could not load today’s question. Is the backend running?')
      setPhase('error')
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const handleVote = async (choice) => {
    setPhase('loading')
    try {
      setResults(await submitDailyVote(choice))
      setPhase('revealed')
    } catch (err) {
      // 409 means this player already voted — from another tab, or a
      // double-submit. Their vote stands; just show them the reveal.
      if (err.status === 409) {
        try {
          setResults(await fetchDailyResults())
          setPhase('revealed')
          return
        } catch {
          // Fall through to the generic retry path below.
        }
      }
      setError(err.detail || 'Vote failed. Please try again.')
      setPhase('voting')
    }
  }

  return (
    <>
      {phase === 'loading' && <div className="status">Loading...</div>}
      {phase === 'error' && <div className="status error">{error}</div>}
      {phase === 'voting' && question && (
        <>
          {error && <div className="status error">{error}</div>}
          <p className="day-label">Today&apos;s question &mdash; {question.day}</p>
          <QuestionCard question={question} onVote={handleVote} loading={false} />
        </>
      )}
      {phase === 'revealed' && results && (
        <RevealCard reveal={results}>
          <TallyPanel
            tally={results.tally}
            totalVotes={results.total_votes}
            available={results.tally_available}
            availableAt={results.tally_available_at}
          />
        </RevealCard>
      )}
    </>
  )
}
