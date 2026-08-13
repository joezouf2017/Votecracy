export default function RevealCard({ reveal, onPlayAgain }) {
  return (
    <div className="card reveal-card">
      <div className="reveal-header">
        <span className="reveal-label">History Reveals</span>
      </div>
      <div className="your-choice">
        You voted: <strong>{reveal.your_choice}</strong>
      </div>
      <div className="reveal-section">
        <span className="stat-label">What actually happened</span>
        <p>{reveal.actual_vote}</p>
      </div>
      <div className="reveal-section">
        <span className="stat-label">The outcome</span>
        <p>{reveal.outcome}</p>
      </div>
      <div className="source">Source: {reveal.source}</div>
      <button className="play-again-btn" onClick={onPlayAgain}>
        Next Question
      </button>
    </div>
  )
}
