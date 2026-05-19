import { useEffect, useState } from 'react'
import './App.css'
import Dashboard from './views/Dashboard'
import CycleRunner from './views/CycleRunner'
import JournalView from './views/JournalView'
import Observability from './views/Observability'
import { getHealth } from './api'

type Tab = 'dashboard' | 'cycle' | 'journal' | 'observability'

const TABS: { id: Tab; label: string }[] = [
  { id: 'dashboard', label: 'Dashboard' },
  { id: 'cycle', label: 'Run cycle' },
  { id: 'journal', label: 'Journal' },
  { id: 'observability', label: 'Observability' },
]

interface Health {
  ok: boolean
  provider: string
  kis_live: boolean
  now_kst: string
}

function App() {
  const [tab, setTab] = useState<Tab>('dashboard')
  const [health, setHealth] = useState<Health | null>(null)
  const [healthErr, setHealthErr] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const tick = () => {
      getHealth()
        .then((h: Health) => {
          if (!cancelled) {
            setHealth(h)
            setHealthErr(null)
          }
        })
        .catch((e: unknown) => {
          if (!cancelled) setHealthErr(e instanceof Error ? e.message : String(e))
        })
    }
    tick()
    const t = setInterval(tick, 5000)
    return () => {
      cancelled = true
      clearInterval(t)
    }
  }, [])

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="dot" />
          kr-ai-trader
        </div>
        <nav className="tabs">
          {TABS.map((t) => (
            <button
              key={t.id}
              className={tab === t.id ? 'tab on' : 'tab'}
              onClick={() => setTab(t.id)}
              type="button"
            >
              {t.label}
            </button>
          ))}
        </nav>
        <div className="status">
          {healthErr ? (
            <span className="badge bad" title={healthErr}>
              backend down
            </span>
          ) : health ? (
            <>
              <span className="badge ok">backend</span>
              <span className="muted">{health.provider}</span>
              <span className={health.kis_live ? 'badge live' : 'badge paper'}>
                {health.kis_live ? 'LIVE' : 'PAPER'}
              </span>
            </>
          ) : (
            <span className="badge">connecting…</span>
          )}
        </div>
      </header>

      <main className="content">
        {tab === 'dashboard' && <Dashboard />}
        {tab === 'cycle' && <CycleRunner />}
        {tab === 'journal' && <JournalView />}
        {tab === 'observability' && <Observability />}
      </main>
    </div>
  )
}

export default App
