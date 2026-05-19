import { useEffect, useMemo, useState } from 'react'
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { AppSettings, OhlcvResponse, PortfolioResponse } from '../api'
import { getOhlcv, getPositions, getSettings, getUniverse } from '../api'

const POPULAR = [
  { code: '005930', name: '삼성전자' },
  { code: '000660', name: 'SK하이닉스' },
  { code: '207940', name: '삼성바이오로직스' },
  { code: '373220', name: 'LG에너지솔루션' },
  { code: '035420', name: 'NAVER' },
  { code: '035720', name: '카카오' },
]

interface UniverseInfo {
  name: string
  count: number
  tickers: string[]
}

export default function Dashboard() {
  const [settings, setSettings] = useState<AppSettings | null>(null)
  const [portfolio, setPortfolio] = useState<PortfolioResponse | null>(null)
  const [universe, setUniverse] = useState<UniverseInfo | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [chartTicker, setChartTicker] = useState<string>('005930')
  const [ohlcv, setOhlcv] = useState<OhlcvResponse | null>(null)
  const [chartErr, setChartErr] = useState<string | null>(null)

  useEffect(() => {
    let mounted = true
    const load = async () => {
      try {
        const [s, p, u] = await Promise.all([getSettings(), getPositions(), getUniverse()])
        if (!mounted) return
        setSettings(s)
        setPortfolio(p)
        setUniverse(u)
        setErr(null)
      } catch (e: unknown) {
        if (mounted) setErr(e instanceof Error ? e.message : String(e))
      }
    }
    void load()
    const t = setInterval(load, 5000)
    return () => {
      mounted = false
      clearInterval(t)
    }
  }, [])

  useEffect(() => {
    let mounted = true
    setOhlcv(null)
    setChartErr(null)
    getOhlcv(chartTicker, 60)
      .then((d) => mounted && setOhlcv(d))
      .catch((e: unknown) => mounted && setChartErr(e instanceof Error ? e.message : String(e)))
    return () => {
      mounted = false
    }
  }, [chartTicker])

  const chartData = useMemo(() => {
    if (!ohlcv) return []
    return ohlcv.rows.map((r) => ({
      date: r.date.slice(5),
      close: r.close,
      volume: r.volume,
    }))
  }, [ohlcv])

  const latest = ohlcv?.rows.at(-1)
  const baseClose = ohlcv?.rows[0]?.close ?? 0
  const drift = latest && baseClose ? ((latest.close - baseClose) / baseClose) * 100 : 0

  if (err) {
    return (
      <div className="card">
        <h3>Backend unreachable</h3>
        <div>{err}</div>
        <div className="muted" style={{ marginTop: 8 }}>
          터미널에서 <code>make api</code> 를 먼저 실행하세요.
        </div>
      </div>
    )
  }

  return (
    <div>
      <div className="grid3">
        <Metric label="Equity" value={portfolio ? formatKrw(portfolio.equity) : '—'} />
        <Metric label="Cash" value={portfolio ? formatKrw(portfolio.cash) : '—'} />
        <Metric label="Positions" value={portfolio?.positions.length.toString() ?? '—'} />
        <Metric label="Universe" value={universe ? universe.count.toString() : '—'} />
        <Metric
          label="Mode"
          value={settings?.kis_live ? 'LIVE' : 'PAPER'}
          tone={settings?.kis_live ? 'neg' : 'pos'}
        />
        <Metric
          label="HALT"
          value={settings?.halt_active ? 'ACTIVE' : 'clear'}
          tone={settings?.halt_active ? 'neg' : 'pos'}
        />
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <div className="row" style={{ marginBottom: 8 }}>
          <h3 style={{ margin: 0 }}>Price chart — 종가 60영업일</h3>
          <div className="spacer" />
          <select value={chartTicker} onChange={(e) => setChartTicker(e.target.value)}>
            {POPULAR.map((p) => (
              <option key={p.code} value={p.code}>
                {p.code} {p.name}
              </option>
            ))}
          </select>
        </div>
        {chartErr ? (
          <div className="empty" style={{ color: '#ff7b72' }}>{chartErr}</div>
        ) : !ohlcv ? (
          <div className="empty">차트 로딩…</div>
        ) : (
          <>
            <div className="row" style={{ marginBottom: 6 }}>
              <span className="muted">최근 {ohlcv.count}일</span>
              <span className="spacer" />
              <span>
                최근 종가 <b>{formatKrw(latest?.close ?? 0)}</b> · 60일 누적{' '}
                <b style={{ color: drift >= 0 ? '#56d364' : '#ff7b72' }}>
                  {drift >= 0 ? '+' : ''}
                  {drift.toFixed(2)}%
                </b>
              </span>
            </div>
            <ResponsiveContainer width="100%" height={220}>
              <ComposedChart data={chartData} margin={{ top: 6, right: 10, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#21262d" />
                <XAxis dataKey="date" stroke="#8b949e" fontSize={11} interval={Math.max(1, Math.floor(chartData.length / 8))} />
                <YAxis yAxisId="price" stroke="#8b949e" fontSize={11} domain={['auto', 'auto']} tickFormatter={(v) => `${(v / 1000).toFixed(0)}k`} />
                <YAxis yAxisId="vol" orientation="right" stroke="#30363d" fontSize={10} tickFormatter={(v) => `${(v / 1_000_000).toFixed(0)}M`} />
                <Tooltip
                  contentStyle={{ background: '#0d1117', border: '1px solid #30363d', borderRadius: 6, fontSize: 12 }}
                  labelStyle={{ color: '#8b949e' }}
                />
                <Bar yAxisId="vol" dataKey="volume" fill="#21262d" />
                <Line yAxisId="price" type="monotone" dataKey="close" stroke="#58a6ff" strokeWidth={2} dot={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </>
        )}
      </div>

      <div className="grid2">
        <div className="card">
          <h3>Settings</h3>
          {settings ? (
            <div className="kv">
              <div className="k">LLM provider</div>
              <div className="v">{settings.llm_provider}</div>
              <div className="k">claude_code_model</div>
              <div className="v">{settings.claude_code_model}</div>
              <div className="k">universe</div>
              <div className="v">{settings.universe}</div>
              <div className="k">max_position_pct</div>
              <div className="v">{settings.max_position_pct}%</div>
              <div className="k">max_sector_pct</div>
              <div className="v">{settings.max_sector_pct}%</div>
              <div className="k">daily_loss_halt_pct</div>
              <div className="v">-{settings.daily_loss_halt_pct}% (신규 매수 차단)</div>
              <div className="k">daily_loss_flatten_pct</div>
              <div className="v">-{settings.daily_loss_flatten_pct}% (전량 청산)</div>
              <div className="k">leverage</div>
              <div className="v">{settings.leverage} (=신용/미수 금지)</div>
              <div className="k">session</div>
              <div className="v">
                {settings.session.is_regular_session
                  ? '정규장 (09:00–15:30 KST)'
                  : settings.session.is_business_day
                  ? '장외 시간 (영업일)'
                  : '휴장'}
              </div>
              <div className="k">now KST</div>
              <div className="v">{settings.session.now_kst.replace('T', ' ').slice(0, 19)}</div>
            </div>
          ) : (
            <div className="empty">loading…</div>
          )}
        </div>

        <div className="card">
          <h3>Positions</h3>
          {portfolio && portfolio.positions.length > 0 ? (
            <table>
              <thead>
                <tr>
                  <th>Ticker</th>
                  <th>Qty</th>
                  <th>Avg</th>
                  <th>Last</th>
                  <th>P&L%</th>
                </tr>
              </thead>
              <tbody>
                {portfolio.positions.map((p) => (
                  <tr key={p.ticker}>
                    <td>{p.ticker}</td>
                    <td>{p.quantity}</td>
                    <td>{formatKrw(p.avg_price)}</td>
                    <td>{formatKrw(p.current_price)}</td>
                    <td style={{ color: p.unrealized_pnl_pct >= 0 ? '#56d364' : '#ff7b72' }}>
                      {p.unrealized_pnl_pct.toFixed(2)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="empty">
              보유 종목 없음 — Run cycle 탭에서 사이클을 실행하면 LLM 이 매매 제안을 만듭니다.
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: 'pos' | 'neg' }) {
  const cls = tone ? `value ${tone}` : 'value'
  return (
    <div className="metric">
      <div className="label">{label}</div>
      <div className={cls}>{value}</div>
    </div>
  )
}

function formatKrw(n: number): string {
  return `₩${Math.round(n).toLocaleString('ko-KR')}`
}
