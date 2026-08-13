const CATEGORY_COLORS = {
  medical: '#4caf50',
  society: '#2196f3',
  economy: '#ff9800',
  technology: '#9c27b0',
}

export default function QuestionCard({ question, onVote, loading }) {
  const color = CATEGORY_COLORS[question.category] ?? '#607d8b'

  return (
    <div className="card">
      <div className="badges">
        <span className="badge" style={{ backgroundColor: color }}>
          {question.category}
        </span>
        <span className="badge badge-era">{question.era}</span>
      </div>
      <p className="question-prompt">{question.prompt}</p>
      <div className="vote-buttons">
        {question.options.map((option) => (
          <button
            key={option}
            className="vote-btn"
            onClick={() => onVote(option)}
            disabled={loading}
          >
            {option}
          </button>
        ))}
      </div>
    </div>
  )
}
