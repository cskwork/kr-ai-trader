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
        <h3>백엔드 연결 불가</h3>
        <div style={{ color: 'var(--red)', fontSize: 13 }}>{err}</div>
        <div className="muted" style={{ marginTop: 8 }}>
          터미널에서 <code>make api</code> 를 먼저 실행하세요.
        </div>
      </div>
    )
  }

  return (
    <div>
      <div className="grid3">
        <Metric label="총 자산" value={portfolio ? formatKrw(portfolio.equity) : '—'} />
        <Metric label="가용 현금" value={portfolio ? formatKrw(portfolio.cash) : '—'} />
        <Metric label="보유 종목" value={portfolio?.positions.length.toString() ?? '—'} />
        <Metric label="유니버스" value={universe ? `${universe.count}종목` : '—'} />
        <Metric
          label="거래 모드"
          value={settings?.kis_live ? '실전' : '모의'}
          tone={settings?.kis_live ? 'neg' : 'pos'}
        />
        <Metric
          label="서킷브레이커"
          value={settings?.halt_active ? '발동' : '정상'}
          tone={settings?.halt_active ? 'neg' : 'pos'}
        />
      </div>

      <div className="card" style={{ marginTop: 2 }}>
        <div className="row" style={{ marginBottom: 10 }}>
          <h3 style={{ margin: 0 }}>종가 차트 — 60영업일</h3>
          <div className="spacer" />
          <select value={chartTicker} onChange={(e) => setChartTicker(e.target.value)}>
            {POPULAR.map((p) => (
              <option key={p.code} value={p.code}>
                {p.name} ({p.code})
              </option>
            ))}
          </select>
        </div>
        {chartErr ? (
          <div className="empty" style={{ color: 'var(--red)' }}>{chartErr}</div>
        ) : !ohlcv ? (
          <div className="empty">차트 불러오는 중…</div>
        ) : (
          <>
            <div className="row" style={{ marginBottom: 8 }}>
              <span className="muted">최근 {ohlcv.count}일</span>
              <span className="spacer" />
              <span style={{ fontSize: 13 }}>
                종가{' '}
                <b style={{ fontSize: 15, letterSpacing: '-0.3px' }}>{formatKrw(latest?.close ?? 0)}</b>
                <span
                  style={{
                    marginLeft: 8,
                    fontWeight: 700,
                    color: drift >= 0 ? 'var(--green)' : 'var(--red)',
                    fontSize: 13,
                  }}
                >
                  {drift >= 0 ? '+' : ''}
                  {drift.toFixed(2)}%
                </span>
              </span>
            </div>
            <ResponsiveContainer width="100%" height={220}>
              <ComposedChart data={chartData} margin={{ top: 6, right: 10, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                <XAxis
                  dataKey="date"
                  stroke="var(--text-tertiary)"
                  fontSize={11}
                  interval={Math.max(1, Math.floor(chartData.length / 8))}
                  tick={{ fill: 'var(--text-secondary)' }}
                />
                <YAxis
                  yAxisId="price"
                  stroke="var(--text-tertiary)"
                  fontSize={11}
                  domain={['auto', 'auto']}
                  tickFormatter={(v) => `${(v / 1000).toFixed(0)}k`}
                  tick={{ fill: 'var(--text-secondary)' }}
                />
                <YAxis
                  yAxisId="vol"
                  orientation="right"
                  stroke="transparent"
                  fontSize={10}
                  tickFormatter={(v) => `${(v / 1_000_000).toFixed(0)}M`}
                  tick={{ fill: 'var(--text-tertiary)' }}
                />
                <Tooltip
                  contentStyle={{
                    background: 'var(--bg-raised)',
                    border: '1px solid var(--border-muted)',
                    borderRadius: 10,
                    fontSize: 12,
                    boxShadow: 'var(--shadow-raise)',
                  }}
                  labelStyle={{ color: 'var(--text-secondary)' }}
                  itemStyle={{ color: 'var(--text-primary)' }}
                />
                <Bar yAxisId="vol" dataKey="volume" fill="rgba(255,255,255,0.05)" radius={[2, 2, 0, 0]} />
                <Line
                  yAxisId="price"
                  type="monotoneX"
                  dataKey="close"
                  stroke="var(--blue)"
                  strokeWidth={2.5}
                  dot={false}
                  activeDot={{ r: 4, fill: 'var(--blue)', stroke: 'var(--bg-surface)', strokeWidth: 2 }}
                />
              </ComposedChart>
            </ResponsiveContainer>
          </>
        )}
      </div>

      <div className="grid2">
        <div className="card">
          <h3>설정</h3>
          {settings ? (
            <div className="kv">
              <div className="k">LLM 공급자</div>
              <div className="v">{settings.llm_provider}</div>
              <div className="k">모델</div>
              <div className="v">{settings.claude_code_model}</div>
              <div className="k">유니버스</div>
              <div className="v">{settings.universe}</div>
              <div className="k">최대 포지션</div>
              <div className="v">{settings.max_position_pct}%</div>
              <div className="k">최대 섹터</div>
              <div className="v">{settings.max_sector_pct}%</div>
              <div className="k">손실 차단</div>
              <div className="v">-{settings.daily_loss_halt_pct}% (신규 매수 차단)</div>
              <div className="k">전량 청산</div>
              <div className="v">-{settings.daily_loss_flatten_pct}%</div>
              <div className="k">레버리지</div>
              <div className="v">{settings.leverage} (신용/미수 금지)</div>
              <div className="k">장 상태</div>
              <div className="v">
                {settings.session.is_regular_session
                  ? '정규장 (09:00–15:30 KST)'
                  : settings.session.is_business_day
                  ? '장외 시간 (영업일)'
                  : '휴장'}
              </div>
              <div className="k">현재 KST</div>
              <div className="v">{settings.session.now_kst.replace('T', ' ').slice(0, 19)}</div>
            </div>
          ) : (
            <div className="empty">불러오는 중…</div>
          )}
        </div>

        <div className="card">
          <h3>보유 종목</h3>
          {portfolio && portfolio.positions.length > 0 ? (
            <table>
              <thead>
                <tr>
                  <th>종목</th>
                  <th>수량</th>
                  <th>평균가</th>
                  <th>현재가</th>
                  <th>수익률</th>
                </tr>
              </thead>
              <tbody>
                {portfolio.positions.map((p) => (
                  <tr key={p.ticker}>
                    <td style={{ fontWeight: 600 }}>{p.ticker}</td>
                    <td>{p.quantity}</td>
                    <td>{formatKrw(p.avg_price)}</td>
                    <td>{formatKrw(p.current_price)}</td>
                    <td
                      style={{
                        color: p.unrealized_pnl_pct >= 0 ? 'var(--green)' : 'var(--red)',
                        fontWeight: 700,
                      }}
                    >
                      {p.unrealized_pnl_pct >= 0 ? '+' : ''}
                      {p.unrealized_pnl_pct.toFixed(2)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="empty">
              보유 종목 없음 — 분석 실행 탭에서 사이클을 돌리면 LLM 이 매매 제안을 만들어요.
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
