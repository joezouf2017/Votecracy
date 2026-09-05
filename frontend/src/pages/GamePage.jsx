import { useState, useEffect, useCallback } from 'react'
import { fetchRandomQuestion, submitVote } from '../api'
import QuestionCard from '../components/QuestionCard'
import RevealCard from '../components/RevealCard'

export default function GamePage() {
  const [phase, setPhase] = useState('loading') // loading | voting | revealed | error
  const [question, setQuestion] = useState(null)
  const [reveal, setReveal] = useState(null)
  const [error, setError] = useState(null)

  const loadQuestion = useCallback(async () => {
    setPhase('loading')
    setError(null)
    try {
      const q = await fetchRandomQuestion()
      setQuestion(q)
      setPhase('voting')
    } catch {
      setError('Could not load a question. Is the backend running?')
      setPhase('error')
    }
  }, [])

  useEffect(() => {
    loadQuestion()
  }, [loadQuestion])

  const handleVote = async (choice) => {
    setPhase('loading')
    try {
      const data = await submitVote(question.id, choice)
      setReveal(data)
      setPhase('revealed')
    } catch {
      setError('Vote failed. Please try again.')
      setPhase('voting')
    }
  }

  return (
    <>
      {phase === 'loading' && <div className="status">Loading...</div>}
      {phase === 'error' && <div className="status error">{error}</div>}
      {phase === 'voting' && question && (
        <QuestionCard question={question} onVote={handleVote} loading={false} />
      )}
      {phase === 'revealed' && reveal && (
        <RevealCard reveal={reveal} onPlayAgain={loadQuestion} />
      )}
    </>
  )
}
