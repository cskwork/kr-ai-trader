import { useMemo, useRef, useState } from 'react'
import type { CycleEvent } from '../api'
import { openCycleWS } from '../api'

const SUGGEST = [
  { code: '005930', name: '삼성전자' },
  { code: '000660', name: 'SK하이닉스' },
  { code: '035420', name: 'NAVER' },
  { code: '207940', name: '삼성바이오로직스' },
  { code: '035720', name: '카카오' },
]

type StageKey = 'features' | 'llm' | 'gate' | 'order'
interface StageState {
  done: boolean
  active: boolean
  detail?: string
  ok?: boolean
}

const STAGE_LABEL: Record<StageKey, string> = {
  features: '1. 가격 피처',
  llm: '2. LLM 토론',
  gate: '3. 리스크 게이트',
  order: '4. 주문 실행',
}

export default function CycleRunner() {
  const [tickersInput, setTickersInput] = useState(
    SUGGEST.slice(0, 1)
      .map((s) => s.code)
      .join(','),
  )
  const [cash, setCash] = useState(10_000_000)
  const [events, setEvents] = useState<CycleEvent[]>([])
  const [running, setRunning] = useState(false)
  const [doneAt, setDoneAt] = useState<string | null>(null)
  const [showHelp, setShowHelp] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)

  const stages = useMemo<Record<StageKey, StageState>>(() => {
    const s: Record<StageKey, StageState> = {
      features: { done: false, active: false },
      llm: { done: false, active: false },
      gate: { done: false, active: false },
      order: { done: false, active: false },
    }
    for (const e of events) {
      switch (e.kind) {
        case 'features_computed':
          s.features = { done: true, active: false, ok: true, detail: `RSI ${e.features.rsi_14.toFixed(1)}` }
          s.llm.active = true
          break
        case 'features_failed':
          s.features = { done: true, active: false, ok: false, detail: e.error.slice(0, 40) }
          break
        case 'moderator_started':
          s.llm.active = true
          break
        case 'no_action':
          s.llm = { done: true, active: false, ok: false, detail: 'no_action' }
          break
        case 'moderator_failed':
          s.llm = { done: true, active: false, ok: false, detail: e.error.slice(0, 40) }
          break
        case 'proposal_built':
          s.llm = { done: true, active: false, ok: true, detail: `${e.side.toUpperCase()} ${(e.conviction * 100).toFixed(0)}%` }
          s.gate.active = true
          break
        case 'risk_gate_decision':
          s.gate = { done: true, active: false, ok: e.accepted, detail: e.accepted ? `qty ${e.computed_qty}` : 'rejected' }
          if (e.accepted) s.order.active = true
          break
        case 'order_placed':
          s.order = { done: true, active: false, ok: true, detail: `${e.side} ${e.quantity}` }
          break
        case 'order_rejected':
          s.order = { done: true, active: false, ok: false, detail: 'rejected' }
          break
      }
    }
    return s
  }, [events])

  function start() {
    setEvents([])
    setDoneAt(null)
    setRunning(true)
    const tickers = tickersInput
      .split(/[,\s]+/)
      .map((t) => t.trim())
      .filter(Boolean)
    const ws = openCycleWS(tickers, cash, (e) => {
      setEvents((prev) => [...prev, e])
      if (e.kind === 'cycle_done' || e.kind === 'error') {
        setRunning(false)
        setDoneAt(e.ts)
      }
    })
    ws.onclose = () => setRunning(false)
    ws.onerror = () => setRunning(false)
    wsRef.current = ws
  }

  function stop() {
    wsRef.current?.close()
    setRunning(false)
  }

  return (
    <div>
      <div className="card">
        <div className="row" style={{ marginBottom: 12 }}>
          <h3 style={{ margin: 0 }}>새 사이클</h3>
          <div className="spacer" />
          <button
            type="button"
            onClick={() => setShowHelp((v) => !v)}
            style={{
              background: 'transparent',
              border: '1px solid #30363d',
              color: '#79c0ff',
              padding: '4px 10px',
              borderRadius: 999,
              fontSize: 12,
              cursor: 'pointer',
            }}
          >
            {showHelp ? '가이드 숨기기' : '사용법 가이드'}
          </button>
        </div>

        {showHelp && <HelpPanel />}

        <div className="kv" style={{ gridTemplateColumns: '120px 1fr', marginBottom: 12 }}>
          <div className="k">티커</div>
          <div className="row" style={{ flexWrap: 'wrap', gap: 6 }}>
            <input
              value={tickersInput}
              onChange={(e) => setTickersInput(e.target.value)}
              placeholder="005930,000660"
              disabled={running}
              style={{ flex: '1 1 240px', minWidth: 200 }}
            />
            <div className="row" style={{ flexWrap: 'wrap', gap: 4 }}>
              {SUGGEST.map((s) => (
                <button
                  key={s.code}
                  type="button"
                  disabled={running}
                  onClick={() => setTickersInput(s.code)}
                  style={{
                    background: '#0d1117',
                    border: '1px solid #30363d',
                    color: '#c9d1d9',
                    padding: '2px 8px',
                    borderRadius: 999,
                    fontSize: 11,
                    cursor: 'pointer',
                  }}
                >
                  {s.code} {s.name}
                </button>
              ))}
            </div>
          </div>
          <div className="k">시작 현금</div>
          <input
            type="number"
            value={cash}
            onChange={(e) => setCash(Number(e.target.value))}
            disabled={running}
            style={{ maxWidth: 240 }}
          />
        </div>
        <div className="row">
          <button className="primary" onClick={start} disabled={running} type="button">
            {running ? '실행 중…' : '사이클 실행 ▶'}
          </button>
          {running && (
            <button
              type="button"
              className="primary"
              style={{ background: '#3a0a13', borderColor: '#f85149' }}
              onClick={stop}
            >
              취소
            </button>
          )}
          <div className="spacer" />
          {doneAt && <span className="muted">완료 {doneAt.slice(11, 19)} UTC</span>}
        </div>

        {events.length > 0 && (
          <div style={{ marginTop: 14 }}>
            <Stepper stages={stages} />
          </div>
        )}
      </div>

      <div className="event-list">
        {events.length === 0 && !running && (
          <div className="empty">
            티커를 입력하고 <b>사이클 실행</b> 을 누르면 단계별 의사결정이 여기에 표시됩니다.
          </div>
        )}
        {running && events.length === 0 && (
          <div className="empty">백엔드 연결 중… (Moderator 가 LLM 3회 호출 → 종목당 30–60s)</div>
        )}
        {events.map((e, i) => (
          <EventCard key={i} e={e} />
        ))}
      </div>
    </div>
  )
}

function Stepper({ stages }: { stages: Record<StageKey, StageState> }) {
  const keys: StageKey[] = ['features', 'llm', 'gate', 'order']
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8 }}>
      {keys.map((k, i) => {
        const s = stages[k]
        const bg = s.ok === true ? '#033a16' : s.ok === false ? '#3a0a13' : s.active ? '#0c2d6b' : '#161b22'
        const border = s.ok === true ? '#238636' : s.ok === false ? '#f85149' : s.active ? '#1f6feb' : '#30363d'
        const color = s.ok === true ? '#56d364' : s.ok === false ? '#ff7b72' : s.active ? '#79c0ff' : '#8b949e'
        return (
          <div
            key={k}
            style={{
              background: bg,
              border: `1px solid ${border}`,
              borderRadius: 8,
              padding: '8px 10px',
              fontSize: 12,
              color,
            }}
          >
            <div style={{ fontWeight: 600, marginBottom: 2 }}>
              {STAGE_LABEL[k]} {s.active && !s.done ? '…' : s.done ? (s.ok ? '✓' : '✗') : ''}
            </div>
            <div style={{ fontSize: 11, color: '#8b949e' }}>{s.detail ?? '대기'}</div>
            {i < keys.length - 1 && <div style={{ position: 'relative' }} />}
          </div>
        )
      })}
    </div>
  )
}

function HelpPanel() {
  return (
    <div
      style={{
        background: '#0d1117',
        border: '1px solid #30363d',
        borderRadius: 8,
        padding: 14,
        marginBottom: 12,
        fontSize: 13,
        lineHeight: 1.6,
      }}
    >
      <div style={{ marginBottom: 8 }}>
        <b style={{ color: '#79c0ff' }}>한 사이클이란?</b> 종목 하나에 대해 LLM 이 3-에이전트 (Bull / Bear / RiskOfficer) 토론으로 매매 제안을 만들고, 결정론
        리스크 게이트가 통과시키면 페이퍼 브로커가 모의 체결하는 한 번의 흐름.
      </div>
      <ol style={{ margin: '8px 0 0', paddingLeft: 20, color: '#c9d1d9' }}>
        <li>
          <b>가격 피처</b> — pykrx 로 최근 60영업일 OHLCV → RSI14, SMA5/20, 1·5·20일 모멘텀 산출
        </li>
        <li>
          <b>LLM 토론</b> — claude-haiku-4-5 가 Bull / Bear / RiskOfficer 역할로 3회 호출, JSON Schema 검증된 매매 제안 (thesis + risks) 반환. 합의되지 않으면
          <code> no_action</code>
        </li>
        <li>
          <b>리스크 게이트</b> — 화이트리스트 / 포지션 한도 / 일일 손실 서킷브레이커 / 공매도 차단 / 레버리지 0 검사. 거부 사유는 모두 기록
        </li>
        <li>
          <b>주문 실행</b> — 멱등 client_order_id 로 PaperBroker 가 체결. 거래세 0.18% 반영
        </li>
      </ol>
      <div style={{ marginTop: 8, color: '#8b949e', fontSize: 12 }}>
        결과는 <code>journal/YYYY-MM-DD.md</code> 에 영구 기록 (Journal 탭) — thesis, risks, 거부 사유까지 보존되어 사후 분석 가능.
      </div>
    </div>
  )
}

function EventCard({ e }: { e: CycleEvent }) {
  const kindClass =
    e.kind === 'proposal_built'
      ? 'proposal'
      : e.kind === 'order_placed'
      ? 'order'
      : e.kind === 'order_rejected' || e.kind === 'no_action' || e.kind === 'moderator_failed'
      ? 'reject'
      : e.kind === 'risk_gate_decision'
      ? 'risk'
      : ''
  const ts = e.ts.slice(11, 19)
  return (
    <div className="event">
      <div className="head">
        <span className={`kind ${kindClass}`}>{e.kind.replace(/_/g, ' ')}</span>
        <span className="ts">{ts}</span>
      </div>
      <div className="body">
        <Renderer e={e} />
      </div>
    </div>
  )
}

function Renderer({ e }: { e: CycleEvent }) {
  switch (e.kind) {
    case 'settings_loaded':
      return (
        <div>
          provider <b>{e.provider}</b> · model <b>{e.model}</b> · universe <b>{e.universe_size}</b> · tickers{' '}
          <b>{e.tickers.join(', ')}</b> · cash <b>₩{e.cash.toLocaleString('ko-KR')}</b>
        </div>
      )
    case 'features_computed':
      return (
        <div>
          <b>{e.ticker}</b> · close ₩{e.features.last_close.toLocaleString('ko-KR')} · 1d{' '}
          <Pct v={e.features.pct_change_1d} /> · 5d <Pct v={e.features.pct_change_5d} /> · 20d{' '}
          <Pct v={e.features.pct_change_20d} /> · RSI14 <b>{e.features.rsi_14.toFixed(2)}</b>
        </div>
      )
    case 'features_failed':
      return (
        <div>
          <b>{e.ticker}</b> features failed: {e.error}
        </div>
      )
    case 'ticker_skipped':
      return (
        <div>
          <b>{e.ticker}</b> skipped — {e.reason}
        </div>
      )
    case 'moderator_started':
      return (
        <div>
          <b>{e.ticker}</b> — Bull + Bear + RiskOfficer 호출 중…
        </div>
      )
    case 'moderator_failed':
      return (
        <div>
          <b>{e.ticker}</b> moderator failed: {e.error}
        </div>
      )
    case 'no_action':
      return (
        <div>
          <b>{e.ticker}</b> — Risk Officer reject 또는 hold (포지션 변경 없음)
        </div>
      )
    case 'proposal_built':
      return (
        <div>
          <div>
            <b>{e.ticker}</b> · side <b style={{ color: e.side === 'buy' ? '#56d364' : '#ff7b72' }}>{e.side}</b> ·
            conviction <b>{(e.conviction * 100).toFixed(0)}%</b> · size <b>{e.size_pct.toFixed(2)}%</b>
            {e.stop_loss_pct ? (
              <>
                {' '}
                · stop_loss <b>{e.stop_loss_pct}%</b>
              </>
            ) : null}
          </div>
          <div className="thesis">{e.thesis}</div>
          {e.risks.length > 0 && (
            <ul className="risks">
              {e.risks.map((r: string, i: number) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          )}
        </div>
      )
    case 'risk_gate_decision':
      return (
        <div>
          <div>
            <b>{e.ticker}</b> · qty <b>{e.computed_qty}</b> · notional ₩
            {Math.round(e.notional).toLocaleString('ko-KR')} · equity ₩
            {Math.round(e.equity).toLocaleString('ko-KR')} ·{' '}
            <b style={{ color: e.accepted ? '#56d364' : '#ff7b72' }}>{e.accepted ? 'ACCEPTED' : 'REJECTED'}</b>
          </div>
          {e.reasons.length > 0 && (
            <ul className="reasons">
              {e.reasons.map((r: string, i: number) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          )}
        </div>
      )
    case 'order_placed':
      return (
        <div>
          <b>{e.ticker}</b> · {e.side.toUpperCase()} <b>{e.quantity}</b> @ ₩
          {Math.round(e.price).toLocaleString('ko-KR')} · status <b>{e.status}</b>
          <div className="muted" style={{ marginTop: 4 }}>
            client_id <code>{e.client_order_id}</code> · broker_id <code>{e.broker_order_id}</code>
          </div>
        </div>
      )
    case 'order_rejected':
      return (
        <div>
          <b>{e.ticker}</b> — order rejected
          {e.reasons.length > 0 && (
            <ul className="reasons">
              {e.reasons.map((r: string, i: number) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          )}
        </div>
      )
    case 'cycle_done':
      return (
        <div>
          최종 cash ₩{Math.round(e.final_cash).toLocaleString('ko-KR')} · positions{' '}
          {e.final_positions.length === 0 ? '없음' : e.final_positions.map((p) => `${p.ticker}×${p.qty}`).join(', ')}
        </div>
      )
    case 'error':
      return <div style={{ color: '#ff7b72' }}>{e.message}</div>
  }
}

function Pct({ v }: { v: number }) {
  const cls = v > 0 ? '#56d364' : v < 0 ? '#ff7b72' : '#c9d1d9'
  const sign = v > 0 ? '+' : ''
  return (
    <b style={{ color: cls }}>
      {sign}
      {v.toFixed(2)}%
    </b>
  )
}
