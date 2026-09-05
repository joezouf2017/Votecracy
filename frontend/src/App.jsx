import { useState } from 'react'
import GamePage from './pages/GamePage'
import DailyPage from './pages/DailyPage'

const MODES = [
  { id: 'quick', label: 'Quick Play' },
  { id: 'daily', label: 'Daily Vote' },
]

export default function App() {
  const [mode, setMode] = useState('quick')

  return (
    <div className="page">
      <header className="app-header">
        <h1>Votecracy</h1>
        <p className="tagline">Vote first. Then see what history decided.</p>
        <nav className="mode-switch">
          {MODES.map((m) => (
            <button
              key={m.id}
              className={`mode-btn${mode === m.id ? ' mode-btn-active' : ''}`}
              onClick={() => setMode(m.id)}
            >
              {m.label}
            </button>
          ))}
        </nav>
      </header>
      <main className="main-content">
        {mode === 'quick' ? <GamePage /> : <DailyPage />}
      </main>
    </div>
  )
}
