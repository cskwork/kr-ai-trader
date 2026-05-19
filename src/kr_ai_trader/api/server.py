"""kr-ai-trader 백엔드 API.

엔드포인트:
- GET  /health                       — 헬스체크
- GET  /api/settings                 — 활성 LLM/유니버스/리스크 파라미터
- GET  /api/universe                 — 허용 티커
- GET  /api/positions                — 페이퍼 브로커 잔고/포지션
- GET  /api/features/{ticker}        — pykrx 피처 (RSI/SMA/모멘텀)
- GET  /api/journal                  — 오늘 저널 마크다운
- WS   /ws/cycle                     — 사이클 실행 + 단계별 이벤트 stream

CORS: Tauri dev (http://localhost:1420) + 정적 dist 허용.
"""

from __future__ import annotations

import contextlib
import json
import re
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from ..agents.moderator import Moderator
from ..broker.base import Order, OrderSide, Quote
from ..broker.paper import PaperBroker
from ..config import get_settings
from ..data.calendar import market_session
from ..data.prices import compute_features, summary_to_dict
from ..data.universe import load_universe
from ..execution.executor import Executor
from ..journal.recorder import JournalRecorder
from ..llm.factory import get_llm
from ..risk.gate import RiskGate

log = structlog.get_logger("api")

app = FastAPI(title="kr-ai-trader API", version="0.2.0")

# KRX 종목코드는 6자리 숫자. 경계에서 강제하여 path traversal / pykrx rate-limit 남용 차단.
_TICKER_RE = re.compile(r"^\d{6}$")
# /ws/cycle 가 받는 cash 의 합리적 범위 (단위: 원).
_MIN_CASH = 1_000.0           # 1천원
_MAX_CASH = 10_000_000_000.0  # 100억원

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:1420",
        "http://127.0.0.1:1420",
        "tauri://localhost",
        "https://tauri.localhost",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Accept"],
)


def _validate_ticker(ticker: str) -> str:
    if not _TICKER_RE.fullmatch(ticker):
        raise HTTPException(status_code=400, detail=f"invalid ticker: {ticker!r}")
    return ticker


# 모듈 전역 페이퍼 브로커는 /api/positions 표시용. /ws/cycle 은 세션마다 새 브로커를 생성하므로
# 둘은 의도적으로 분리. 향후 단일 세션 모델로 통합 시 두 경로 모두 정리.
_paper_broker: PaperBroker | None = None
_journal: JournalRecorder | None = None


def _broker() -> PaperBroker:
    global _paper_broker
    if _paper_broker is None:
        _paper_broker = PaperBroker(initial_cash=10_000_000.0, settings=get_settings())
    return _paper_broker


def _journal_recorder() -> JournalRecorder:
    global _journal
    if _journal is None:
        _journal = JournalRecorder()
    return _journal


@app.get("/health")
async def health() -> dict[str, Any]:
    s = get_settings()
    return {
        "ok": True,
        "provider": s.llm_provider.value,
        "kis_live": s.kis_live,
        "now_kst": market_session().now_kst.isoformat(),
    }


@app.get("/api/settings")
async def get_app_settings() -> dict[str, Any]:
    s = get_settings()
    return {
        "llm_provider": s.llm_provider.value,
        "claude_code_model": s.claude_code_model,
        "anthropic_model": s.anthropic_model,
        "openai_model": s.openai_model,
        "ollama_model": s.ollama_model,
        "universe": s.universe,
        "max_position_pct": s.max_position_pct,
        "max_sector_pct": s.max_sector_pct,
        "daily_loss_halt_pct": s.daily_loss_halt_pct,
        "daily_loss_flatten_pct": s.daily_loss_flatten_pct,
        "leverage": s.leverage,
        "halt_file": str(s.halt_file),
        "halt_active": s.halt_file.exists(),
        "kis_live": s.kis_live,
        "session": {
            "is_business_day": market_session().is_business_day,
            "is_regular_session": market_session().is_regular_session,
            "now_kst": market_session().now_kst.isoformat(),
        },
    }


@app.get("/api/universe")
async def universe_list() -> dict[str, Any]:
    s = get_settings()
    tickers = sorted(load_universe(s.universe, s.universe_file))
    return {"name": s.universe, "count": len(tickers), "tickers": tickers[:50]}


@app.get("/api/positions")
async def positions() -> dict[str, Any]:
    broker = _broker()
    cash = await broker.get_cash()
    pos = await broker.get_positions()
    return {
        "broker": broker.name,
        "is_live": broker.is_live,
        "cash": cash,
        "positions": [
            {
                "ticker": p.ticker,
                "quantity": p.quantity,
                "avg_price": p.avg_price,
                "current_price": p.current_price,
                "market_value": p.market_value,
                "unrealized_pnl_pct": p.unrealized_pnl_pct,
            }
            for p in pos
        ],
        "equity": cash + sum(p.market_value for p in pos),
    }


@app.get("/api/ohlcv/{ticker}")
async def ohlcv(ticker: str, days: int = 60) -> dict[str, Any]:
    """과거 N영업일 OHLCV 차트용 데이터."""
    from datetime import timedelta

    from ..data.calendar import previous_business_day
    from ..data.prices import get_ohlcv

    ticker = _validate_ticker(ticker)
    days = max(1, min(days, 365))
    end = previous_business_day()
    start = end - timedelta(days=days * 2)  # 휴일 보정 buffer
    df = get_ohlcv(ticker, start, end)
    if df.empty:
        raise HTTPException(status_code=404, detail=f"no ohlcv for {ticker}")
    df = df.tail(days)
    rows = []
    for idx, row in df.iterrows():
        rows.append(
            {
                "date": idx.strftime("%Y-%m-%d"),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": int(row["volume"]),
            }
        )
    return {"ticker": ticker, "count": len(rows), "rows": rows}


@app.get("/api/features/{ticker}")
async def features(ticker: str) -> dict[str, Any]:
    ticker = _validate_ticker(ticker)
    try:
        s = compute_features(ticker)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"features unavailable for {ticker}: {e}") from e
    return summary_to_dict(s)


@app.get("/api/journal")
async def journal_today() -> dict[str, Any]:
    journal = _journal_recorder()
    path = journal.path_for()                 # KST 기준 today
    today = path.stem
    if not path.exists():
        return {"date": today, "markdown": "", "exists": False}
    return {"date": today, "markdown": path.read_text(encoding="utf-8"), "exists": True}


async def _send_event(ws: WebSocket, kind: str, **payload: Any) -> None:
    """단일 사이클 이벤트 전송. 클라이언트는 kind 로 분기 렌더링."""
    msg = {"kind": kind, "ts": datetime.now(timezone.utc).isoformat(), **payload}
    await ws.send_text(json.dumps(msg, ensure_ascii=False, default=str))


@app.websocket("/ws/cycle")
async def ws_cycle(ws: WebSocket) -> None:
    """클라이언트가 보내는 {tickers: [...], cash: ...} 1회 메시지로 사이클 실행.

    이벤트:
    - settings_loaded
    - features_computed (per ticker)
    - moderator_started (per ticker)
    - bull_response / bear_response / risk_officer_response
    - proposal_built / no_action
    - risk_gate_decision
    - order_placed / order_rejected
    - cycle_done
    """
    await ws.accept()
    try:
        raw = await ws.receive_text()
        req = json.loads(raw)
        raw_tickers = req.get("tickers") or ["005930"]
        # 클라이언트가 보낸 ticker 를 6자리 숫자로 미리 필터.
        tickers: list[str] = [t for t in raw_tickers if isinstance(t, str) and _TICKER_RE.fullmatch(t)]
        if not tickers:
            await _send_event(ws, "error", message="no valid 6-digit tickers in request")
            await ws.close()
            return
        try:
            initial_cash = float(req.get("cash") or 10_000_000.0)
        except (TypeError, ValueError):
            initial_cash = 10_000_000.0
        initial_cash = max(_MIN_CASH, min(initial_cash, _MAX_CASH))

        settings = get_settings()
        universe = load_universe(settings.universe, settings.universe_file)
        llm = get_llm(settings)
        await _send_event(
            ws,
            "settings_loaded",
            provider=llm.name,
            model=llm.model,
            universe_size=len(universe),
            tickers=tickers,
            cash=initial_cash,
        )

        broker = PaperBroker(initial_cash=initial_cash, settings=settings)
        risk_gate = RiskGate(settings=settings, universe=universe)
        journal = _journal_recorder()
        executor = Executor(
            broker=broker, risk_gate=risk_gate, journal=journal, strategy_name="ui"
        )
        moderator = Moderator(llm=llm)

        for ticker in tickers:
            if ticker not in universe:
                await _send_event(ws, "ticker_skipped", ticker=ticker, reason="not in universe")
                continue
            try:
                feat = compute_features(ticker)
            except Exception as e:
                await _send_event(ws, "features_failed", ticker=ticker, error=str(e))
                continue

            await _send_event(
                ws,
                "features_computed",
                ticker=ticker,
                features=summary_to_dict(feat),
            )

            broker.set_quote(
                Quote(ticker=ticker, price=feat.last_close, timestamp=datetime.now(timezone.utc))
            )

            await _send_event(ws, "moderator_started", ticker=ticker)
            try:
                proposal = await moderator.decide(
                    ticker=ticker,
                    market_context=json.dumps(summary_to_dict(feat), ensure_ascii=False, indent=2),
                )
            except Exception as e:
                await _send_event(ws, "moderator_failed", ticker=ticker, error=str(e))
                continue

            if proposal is None:
                await _send_event(ws, "no_action", ticker=ticker)
                continue

            await _send_event(
                ws,
                "proposal_built",
                ticker=ticker,
                side=proposal.side,
                conviction=proposal.conviction,
                size_pct=proposal.size_pct,
                thesis=proposal.thesis,
                risks=proposal.risks,
                stop_loss_pct=proposal.stop_loss_pct,
            )

            cash = await broker.get_cash()
            positions_ = await broker.get_positions()
            quote = await broker.get_quote(ticker)
            equity = cash + sum(p.market_value for p in positions_)
            target_notional = equity * (proposal.size_pct / 100.0)
            qty = int(target_notional // quote.price)
            side_ = OrderSide.buy if proposal.side == "buy" else OrderSide.sell
            if side_ == OrderSide.sell:
                held = sum(p.quantity for p in positions_ if p.ticker == ticker)
                qty = max(0, min(qty if qty > 0 else held, held))
            tentative = Order(
                client_order_id=Executor.make_idempotent_id("ui", ticker, side_.value, qty),
                ticker=ticker,
                side=side_,
                quantity=qty,
            )
            decision = risk_gate.evaluate(
                order=tentative,
                cash=cash,
                positions=positions_,
                portfolio_equity=equity,
                day_pnl_pct=0.0,
                last_price=quote.price,
                proposed_stop_loss_pct=proposal.stop_loss_pct,
            )
            await _send_event(
                ws,
                "risk_gate_decision",
                ticker=ticker,
                accepted=decision.accepted,
                reasons=decision.reasons,
                principles_applied=decision.principles_applied,
                principles_violated=decision.principles_violated,
                computed_qty=qty,
                notional=qty * quote.price,
                equity=equity,
            )

            order = await executor.execute(proposal)
            if order is None:
                await _send_event(ws, "order_rejected", ticker=ticker, reasons=decision.reasons)
            else:
                await _send_event(
                    ws,
                    "order_placed",
                    ticker=ticker,
                    side=order.side.value,
                    quantity=order.filled_quantity,
                    price=order.filled_avg_price,
                    status=order.status,
                    client_order_id=order.client_order_id,
                    broker_order_id=order.broker_order_id,
                )

        cash = await broker.get_cash()
        positions_ = await broker.get_positions()
        await _send_event(
            ws,
            "cycle_done",
            final_cash=cash,
            final_positions=[
                {"ticker": p.ticker, "qty": p.quantity, "avg": p.avg_price}
                for p in positions_
            ],
        )
    except WebSocketDisconnect:
        log.info("cycle.ws.disconnected")
    except Exception as e:
        log.exception("cycle.ws.error")
        with contextlib.suppress(Exception):
            await _send_event(ws, "error", message=str(e))
        with contextlib.suppress(Exception):
            await ws.close()


def main() -> None:
    import uvicorn

    uvicorn.run("kr_ai_trader.api.server:app", host="127.0.0.1", port=8765, reload=False)


if __name__ == "__main__":
    main()
