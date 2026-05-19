// REST/WebSocket 클라이언트. 백엔드 주소는 .env 의 VITE_API_BASE 로 오버라이드 가능.

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://127.0.0.1:8765'
const WS_BASE = API_BASE.replace(/^http/, 'ws')

export interface AppSettings {
  llm_provider: string
  claude_code_model: string
  anthropic_model: string
  openai_model: string
  ollama_model: string
  universe: string
  max_position_pct: number
  max_sector_pct: number
  daily_loss_halt_pct: number
  daily_loss_flatten_pct: number
  leverage: number
  halt_file: string
  halt_active: boolean
  kis_live: boolean
  session: { is_business_day: boolean; is_regular_session: boolean; now_kst: string }
}

export interface Position {
  ticker: string
  quantity: number
  avg_price: number
  current_price: number
  market_value: number
  unrealized_pnl_pct: number
}

export interface PortfolioResponse {
  broker: string
  is_live: boolean
  cash: number
  positions: Position[]
  equity: number
}

export interface Features {
  ticker: string
  last_close: number
  pct_change_1d: number
  pct_change_5d: number
  pct_change_20d: number
  sma_5: number
  sma_20: number
  rsi_14: number
  volume: number
  as_of: string
}

export interface OhlcvRow {
  date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface OhlcvResponse {
  ticker: string
  count: number
  rows: OhlcvRow[]
}

export type CycleEvent =
  | { kind: 'settings_loaded'; ts: string; provider: string; model: string; universe_size: number; tickers: string[]; cash: number }
  | { kind: 'features_computed'; ts: string; ticker: string; features: Features }
  | { kind: 'features_failed'; ts: string; ticker: string; error: string }
  | { kind: 'ticker_skipped'; ts: string; ticker: string; reason: string }
  | { kind: 'moderator_started'; ts: string; ticker: string }
  | { kind: 'moderator_failed'; ts: string; ticker: string; error: string }
  | { kind: 'no_action'; ts: string; ticker: string }
  | { kind: 'proposal_built'; ts: string; ticker: string; side: string; conviction: number; size_pct: number; thesis: string; risks: string[]; stop_loss_pct: number | null }
  | { kind: 'risk_gate_decision'; ts: string; ticker: string; accepted: boolean; reasons: string[]; computed_qty: number; notional: number; equity: number }
  | { kind: 'order_placed'; ts: string; ticker: string; side: string; quantity: number; price: number; status: string; client_order_id: string; broker_order_id: string }
  | { kind: 'order_rejected'; ts: string; ticker: string; reasons: string[] }
  | { kind: 'cycle_done'; ts: string; final_cash: number; final_positions: Array<{ ticker: string; qty: number; avg: number }> }
  | { kind: 'error'; ts: string; message: string }

export async function getHealth() {
  const r = await fetch(`${API_BASE}/health`)
  return r.json()
}

export async function getSettings(): Promise<AppSettings> {
  const r = await fetch(`${API_BASE}/api/settings`)
  if (!r.ok) throw new Error(`settings ${r.status}`)
  return r.json()
}

export async function getPositions(): Promise<PortfolioResponse> {
  const r = await fetch(`${API_BASE}/api/positions`)
  if (!r.ok) throw new Error(`positions ${r.status}`)
  return r.json()
}

export async function getFeatures(ticker: string): Promise<Features> {
  const r = await fetch(`${API_BASE}/api/features/${ticker}`)
  if (!r.ok) throw new Error(`features ${r.status}`)
  return r.json()
}

export async function getOhlcv(ticker: string, days = 60): Promise<OhlcvResponse> {
  const r = await fetch(`${API_BASE}/api/ohlcv/${ticker}?days=${days}`)
  if (!r.ok) throw new Error(`ohlcv ${r.status}`)
  return r.json()
}

export async function getJournalToday(): Promise<{ date: string; markdown: string; exists: boolean }> {
  const r = await fetch(`${API_BASE}/api/journal`)
  if (!r.ok) throw new Error(`journal ${r.status}`)
  return r.json()
}

export async function getUniverse(): Promise<{ name: string; count: number; tickers: string[] }> {
  const r = await fetch(`${API_BASE}/api/universe`)
  if (!r.ok) throw new Error(`universe ${r.status}`)
  return r.json()
}

export function openCycleWS(tickers: string[], cash: number, onEvent: (e: CycleEvent) => void): WebSocket {
  const ws = new WebSocket(`${WS_BASE}/ws/cycle`)
  ws.onopen = () => {
    ws.send(JSON.stringify({ tickers, cash }))
  }
  ws.onmessage = (msg) => {
    try {
      onEvent(JSON.parse(msg.data) as CycleEvent)
    } catch {
      // ignore non-JSON
    }
  }
  return ws
}
