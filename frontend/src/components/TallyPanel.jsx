/**
 * The community split on a daily question.
 *
 * Deliberately absent while the vote is still open: showing a running count
 * mid-day would nudge later voters toward the leading option, which is exactly
 * the bias the game is trying to let players discover in themselves.
 */
export default function TallyPanel({ tally, totalVotes, available, availableAt }) {
  if (!available) {
    const unlocks = new Date(availableAt)
    return (
      <div className="tally tally-locked">
        <span className="stat-label">How everyone else voted</span>
        <p>
          Unlocks when today&apos;s vote closes —{' '}
          {unlocks.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })}
        </p>
      </div>
    )
  }

  const entries = Object.entries(tally ?? {})
  const total = totalVotes || entries.reduce((sum, [, n]) => sum + n, 0)

  return (
    <div className="tally">
      <span className="stat-label">How everyone else voted</span>
      {entries.map(([option, count]) => {
        const pct = total ? Math.round((count / total) * 100) : 0
        return (
          <div className="tally-row" key={option}>
            <div className="tally-row-head">
              <span>{option}</span>
              <span>
                {pct}% ({count})
              </span>
            </div>
            <div className="tally-bar">
              <div className="tally-fill" style={{ width: `${pct}%` }} />
            </div>
          </div>
        )
      })}
      <p className="tally-total">{total} votes</p>
    </div>
  )
}
