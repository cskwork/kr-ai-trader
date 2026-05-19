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
          <div className="label">수집 샘플</div>
          <div className="value">{series.length}<span style={{ fontSize: 14, fontWeight: 500, color: 'var(--text-secondary)' }}>/60</span></div>
        </div>
        <div className="metric">
          <div className="label">LLM 공급자</div>
          <div className="value" style={{ fontSize: 18 }}>
            {last?.provider ?? '—'}
          </div>
        </div>
        <div className="metric">
          <div className="label">서킷브레이커</div>
          <div className={last?.halt ? 'value neg' : 'value pos'} style={{ fontSize: 18 }}>
            {last?.halt ? '발동' : '정상'}
          </div>
        </div>
      </div>

      <div className="card" style={{ marginTop: 2 }}>
        <h3>자산 추이 — 최근 {series.length}샘플 (3초 간격)</h3>
        {series.length < 2 ? (
          <div className="empty">샘플 수집 중… 백엔드가 응답해야 그래프가 표시돼요.</div>
        ) : (
          <Sparkline values={series.map((s) => s.equity)} min={equityMin} max={equityMax} />
        )}
        <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 8 }}>
          <span className="muted">최저 ₩{Math.round(equityMin).toLocaleString('ko-KR')}</span>
          <span className="muted">최고 ₩{Math.round(equityMax).toLocaleString('ko-KR')}</span>
        </div>
      </div>

      <div className="card">
        <h3>원시 샘플</h3>
        {err && <div className="empty" style={{ color: 'var(--red)', fontStyle: 'normal' }}>{err}</div>}
        <table>
          <thead>
            <tr>
              <th>시각 (KST)</th>
              <th>현금</th>
              <th>총 자산</th>
              <th>보유 종목</th>
              <th>서킷</th>
            </tr>
          </thead>
          <tbody>
            {series
              .slice()
              .reverse()
              .map((s, i) => (
                <tr key={i}>
                  <td style={{ color: 'var(--text-secondary)' }}>{s.ts}</td>
                  <td>₩{Math.round(s.cash).toLocaleString('ko-KR')}</td>
                  <td style={{ fontWeight: 600 }}>₩{Math.round(s.equity).toLocaleString('ko-KR')}</td>
                  <td>{s.positions}</td>
                  <td style={{ color: s.halt ? 'var(--red)' : 'var(--green)', fontWeight: 600 }}>
                    {s.halt ? '발동' : '정상'}
                  </td>
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
  const h = 90
  const range = Math.max(max - min, 1)

  const pts = values.map((v, i) => {
    const x = (i / Math.max(values.length - 1, 1)) * w
    const y = h - ((v - min) / range) * (h - 8) - 4
    return { x, y }
  })

  const polylinePoints = pts.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ')

  // Build fill path: line + bottom border
  const fillPath =
    pts.length > 0
      ? `M${pts[0].x.toFixed(1)},${h} ` +
        pts.map((p) => `L${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ') +
        ` L${pts[pts.length - 1].x.toFixed(1)},${h} Z`
      : ''

  const gradId = 'sparkGrad'

  return (
    <svg viewBox={`0 0 ${w} ${h}`} style={{ width: '100%', height: 90 }} preserveAspectRatio="none">
      <defs>
        <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#3182F6" stopOpacity="0.25" />
          <stop offset="100%" stopColor="#3182F6" stopOpacity="0.02" />
        </linearGradient>
      </defs>
      {fillPath && <path d={fillPath} fill={`url(#${gradId})`} />}
      <polyline fill="none" stroke="#3182F6" strokeWidth="2" points={polylinePoints} />
      {pts.length > 0 && (
        <circle
          cx={pts[pts.length - 1].x}
          cy={pts[pts.length - 1].y}
          r="4"
          fill="#3182F6"
          stroke="#1C1C22"
          strokeWidth="2"
        />
      )}
    </svg>
  )
}
