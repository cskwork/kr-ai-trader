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
  features: '피처 수집',
  llm: 'LLM 토론',
  gate: '리스크 심사',
  order: '주문 실행',
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
        <div className="row" style={{ marginBottom: 14 }}>
          <h3 style={{ margin: 0 }}>새 분석 사이클</h3>
          <div className="spacer" />
          <button
            type="button"
            onClick={() => setShowHelp((v) => !v)}
            style={{
              background: 'transparent',
              border: '1px solid var(--border-muted)',
              color: 'var(--blue)',
              padding: '5px 12px',
              borderRadius: 'var(--r-pill)',
              fontSize: 12,
              fontWeight: 600,
              fontFamily: 'var(--font-sans)',
              cursor: 'pointer',
              transition: 'background 0.15s',
            }}
          >
            {showHelp ? '가이드 닫기' : '사용법 보기'}
          </button>
        </div>

        {showHelp && <HelpPanel />}

        <div className="kv" style={{ gridTemplateColumns: '100px 1fr', marginBottom: 14 }}>
          <div className="k" style={{ paddingTop: 8 }}>종목 코드</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <input
              value={tickersInput}
              onChange={(e) => setTickersInput(e.target.value)}
              placeholder="005930,000660"
              disabled={running}
              style={{ maxWidth: 320 }}
            />
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
              {SUGGEST.map((s) => (
                <button
                  key={s.code}
                  type="button"
                  disabled={running}
                  onClick={() => setTickersInput(s.code)}
                  style={{
                    background: tickersInput === s.code ? 'var(--blue-dim)' : 'var(--bg-raised)',
                    border: `1px solid ${tickersInput === s.code ? 'var(--blue)' : 'var(--border-muted)'}`,
                    color: tickersInput === s.code ? 'var(--blue)' : 'var(--text-secondary)',
                    padding: '3px 10px',
                    borderRadius: 'var(--r-pill)',
                    fontSize: 11,
                    fontWeight: 600,
                    fontFamily: 'var(--font-sans)',
                    cursor: 'pointer',
                    transition: 'all 0.15s',
                  }}
                >
                  {s.name}
                </button>
              ))}
            </div>
          </div>
          <div className="k" style={{ paddingTop: 8 }}>시작 현금</div>
          <input
            type="number"
            value={cash}
            onChange={(e) => setCash(Number(e.target.value))}
            disabled={running}
            style={{ maxWidth: 200 }}
          />
        </div>
        <div className="row">
          <button className="primary" onClick={start} disabled={running} type="button">
            {running ? '분석 중…' : '지금 분석하기'}
          </button>
          {running && (
            <button
              type="button"
              style={{
                background: 'var(--red-dim)',
                border: '1px solid var(--red)',
                color: 'var(--red)',
                padding: '9px 20px',
                borderRadius: 'var(--r-sm)',
                fontWeight: 600,
                fontFamily: 'var(--font-sans)',
                fontSize: 14,
                cursor: 'pointer',
              }}
              onClick={stop}
            >
              중단
            </button>
          )}
          <div className="spacer" />
          {doneAt && (
            <span className="muted">
              완료 {doneAt.slice(11, 19)} UTC
            </span>
          )}
        </div>

        {events.length > 0 && (
          <div style={{ marginTop: 16 }}>
            <Stepper stages={stages} />
          </div>
        )}
      </div>

      <div className="event-list">
        {events.length === 0 && !running && (
          <div className="empty">
            종목을 선택하고 <b>지금 분석하기</b>를 누르면 단계별 AI 의사결정이 여기에 표시돼요.
          </div>
        )}
        {running && events.length === 0 && (
          <div className="empty">
            백엔드 연결 중… (LLM 3회 호출 → 종목당 30–60초 소요)
          </div>
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
  const doneCount = keys.filter((k) => stages[k].done).length
  const activeIdx = keys.findIndex((k) => stages[k].active && !stages[k].done)
  const progressPct =
    doneCount === keys.length
      ? 100
      : activeIdx >= 0
      ? ((activeIdx + 0.5) / keys.length) * 100
      : (doneCount / keys.length) * 100

  return (
    <div>
      {/* Progress bar */}
      <div
        style={{
          height: 4,
          background: 'var(--bg-raised)',
          borderRadius: 'var(--r-pill)',
          overflow: 'hidden',
          marginBottom: 12,
        }}
      >
        <div
          style={{
            height: '100%',
            width: `${progressPct}%`,
            background: doneCount === keys.length && keys.every((k) => stages[k].ok !== false)
              ? 'var(--green)'
              : keys.some((k) => stages[k].ok === false)
              ? 'var(--red)'
              : 'var(--blue)',
            borderRadius: 'var(--r-pill)',
            transition: 'width 0.4s ease',
          }}
        />
      </div>

      {/* Step indicators */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8 }}>
        {keys.map((k) => {
          const s = stages[k]
          const statusColor =
            s.ok === true
              ? 'var(--green)'
              : s.ok === false
              ? 'var(--red)'
              : s.active
              ? 'var(--blue)'
              : 'var(--text-tertiary)'
          const bg =
            s.ok === true
              ? 'var(--green-dim)'
              : s.ok === false
              ? 'var(--red-dim)'
              : s.active
              ? 'var(--blue-dim)'
              : 'var(--bg-raised)'

          return (
            <div
              key={k}
              style={{
                background: bg,
                border: `1px solid ${statusColor === 'var(--text-tertiary)' ? 'var(--border-subtle)' : statusColor}`,
                borderRadius: 'var(--r-sm)',
                padding: '10px 12px',
                fontSize: 12,
                transition: 'all 0.25s ease',
              }}
            >
              <div
                style={{
                  fontWeight: 700,
                  marginBottom: 3,
                  color: statusColor,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 5,
                  fontSize: 11,
                  textTransform: 'uppercase',
                  letterSpacing: '0.4px',
                }}
              >
                {STAGE_LABEL[k]}
                {s.active && !s.done && (
                  <span style={{ animation: 'fadeInOut 1.2s ease-in-out infinite' }}>…</span>
                )}
                {s.done && (
                  <span style={{ fontSize: 12 }}>{s.ok ? '✓' : '✗'}</span>
                )}
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                {s.detail ?? '대기 중'}
              </div>
            </div>
          )
        })}
      </div>

      <style>{`
        @keyframes fadeInOut {
          0%, 100% { opacity: 0.4; }
          50% { opacity: 1; }
        }
      `}</style>
    </div>
  )
}

function HelpPanel() {
  return (
    <div
      style={{
        background: 'var(--bg-raised)',
        border: '1px solid var(--border-subtle)',
        borderRadius: 'var(--r-md)',
        padding: '16px 20px',
        marginBottom: 14,
        fontSize: 13,
        lineHeight: 1.65,
      }}
    >
      <div style={{ marginBottom: 10, color: 'var(--text-primary)' }}>
        <b style={{ color: 'var(--blue)' }}>사이클이란?</b>{' '}
        종목 하나에 대해 AI가 Bull / Bear / RiskOfficer 3-에이전트 토론으로 매매 제안을 만들고,
        리스크 게이트를 통과하면 모의 체결하는 한 번의 흐름이에요.
      </div>
      <ol style={{ margin: '10px 0 0', paddingLeft: 20, color: 'var(--text-secondary)' }}>
        <li style={{ marginBottom: 5 }}>
          <b style={{ color: 'var(--text-primary)' }}>피처 수집</b>{' '}
          — pykrx로 60영업일 OHLCV → RSI14, SMA5/20, 1·5·20일 모멘텀 계산
        </li>
        <li style={{ marginBottom: 5 }}>
          <b style={{ color: 'var(--text-primary)' }}>LLM 토론</b>{' '}
          — claude-haiku-4-5가 3회 호출, JSON Schema 검증된 매매 제안(thesis + risks) 반환.
          합의 실패 시 <code>no_action</code>
        </li>
        <li style={{ marginBottom: 5 }}>
          <b style={{ color: 'var(--text-primary)' }}>리스크 심사</b>{' '}
          — 화이트리스트 / 포지션 한도 / 일일 손실 서킷브레이커 / 공매도 차단. 거부 사유 전부 기록
        </li>
        <li>
          <b style={{ color: 'var(--text-primary)' }}>주문 실행</b>{' '}
          — 멱등 client_order_id로 PaperBroker 체결. 거래세 0.18% 반영
        </li>
      </ol>
      <div style={{ marginTop: 10, color: 'var(--text-secondary)', fontSize: 12 }}>
        결과는 <code>journal/YYYY-MM-DD.md</code>에 영구 기록 — 저널 탭에서 사후 분석 가능해요.
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
            <b>{e.ticker}</b> · 방향{' '}
            <b style={{ color: e.side === 'buy' ? 'var(--green)' : 'var(--red)' }}>
              {e.side === 'buy' ? '매수' : '매도'}
            </b>{' '}
            · 확신도 <b>{(e.conviction * 100).toFixed(0)}%</b> · 비중 <b>{e.size_pct.toFixed(2)}%</b>
            {e.stop_loss_pct ? (
              <>
                {' '}
                · 손절선 <b>{e.stop_loss_pct}%</b>
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
            <b style={{ color: e.accepted ? 'var(--green)' : 'var(--red)' }}>
              {e.accepted ? '심사 통과' : '심사 거부'}
            </b>
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
      return <div style={{ color: 'var(--red)' }}>{e.message}</div>
  }
}

function Pct({ v }: { v: number }) {
  const color = v > 0 ? 'var(--green)' : v < 0 ? 'var(--red)' : 'var(--text-secondary)'
  const sign = v > 0 ? '+' : ''
  return (
    <b style={{ color }}>
      {sign}
      {v.toFixed(2)}%
    </b>
  )
}
