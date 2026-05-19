import { useEffect, useState } from 'react'
import type { AppSettings, PortfolioResponse } from '../api'
import { getPositions, getSettings } from '../api'

interface Sample {
  ts: string
  cash: number
  equity: number
  positions: number
  halt: boolean
  provider: string
}

export default function Observability() {
  const [series, setSeries] = useState<Sample[]>([])
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    let mounted = true
    const sample = async () => {
      try {
        const [s, p]: [AppSettings, PortfolioResponse] = await Promise.all([
          getSettings(),
          getPositions(),
        ])
        if (!mounted) return
        setSeries((prev) =>
          [
            ...prev,
            {
              ts: s.session.now_kst.slice(11, 19),
              cash: p.cash,
              equity: p.equity,
              positions: p.positions.length,
              halt: s.halt_active,
              provider: s.llm_provider,
            },
          ].slice(-60),
        )
        setErr(null)
      } catch (e: unknown) {
        if (mounted) setErr(e instanceof Error ? e.message : String(e))
      }
    }
    void sample()
    const t = setInterval(sample, 3000)
    return () => {
      mounted = false
      clearInterval(t)
    }
  }, [])

  const equityMax = Math.max(...series.map((s) => s.equity), 1)
  const equityMin = Math.min(...series.map((s) => s.equity), equityMax)
  const last = series.at(-1)

  return (
    <div>
      <div className="grid3">
        <div className="metric">
          <div className="label">Backend latency samples</div>
          <div className="value">{series.length}/60</div>
        </div>
        <div className="metric">
          <div className="label">LLM provider</div>
          <div className="value" style={{ fontSize: 16 }}>
            {last?.provider ?? '—'}
          </div>
        </div>
        <div className="metric">
          <div className="label">HALT switch</div>
          <div className={last?.halt ? 'value neg' : 'value pos'} style={{ fontSize: 16 }}>
            {last?.halt ? 'ON' : 'off'}
          </div>
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h3>Equity (last {series.length} samples, 3s interval)</h3>
        {series.length < 2 ? (
          <div className="empty">샘플링 중… 백엔드가 멈추면 멈춰요.</div>
        ) : (
          <Sparkline values={series.map((s) => s.equity)} min={equityMin} max={equityMax} />
        )}
        <div className="muted" style={{ marginTop: 8 }}>
          min ₩{Math.round(equityMin).toLocaleString('ko-KR')} · max ₩
          {Math.round(equityMax).toLocaleString('ko-KR')}
        </div>
      </div>

      <div className="card">
        <h3>Raw samples</h3>
        {err && <div className="empty" style={{ color: '#ff7b72' }}>{err}</div>}
        <table>
          <thead>
            <tr>
              <th>Time (KST)</th>
              <th>Cash</th>
              <th>Equity</th>
              <th>Positions</th>
              <th>HALT</th>
            </tr>
          </thead>
          <tbody>
            {series
              .slice()
              .reverse()
              .map((s, i) => (
                <tr key={i}>
                  <td>{s.ts}</td>
                  <td>₩{Math.round(s.cash).toLocaleString('ko-KR')}</td>
                  <td>₩{Math.round(s.equity).toLocaleString('ko-KR')}</td>
                  <td>{s.positions}</td>
                  <td style={{ color: s.halt ? '#ff7b72' : '#56d364' }}>{s.halt ? 'on' : 'off'}</td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function Sparkline({ values, min, max }: { values: number[]; min: number; max: number }) {
  const w = 600
  const h = 80
  const range = Math.max(max - min, 1)
  const pts = values
    .map((v, i) => {
      const x = (i / Math.max(values.length - 1, 1)) * w
      const y = h - ((v - min) / range) * h
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
  return (
    <svg viewBox={`0 0 ${w} ${h}`} style={{ width: '100%', height: 80 }}>
      <polyline fill="none" stroke="#58a6ff" strokeWidth="1.5" points={pts} />
    </svg>
  )
}
