"""풀사이클 데모 — 시드 보유 종목 → LLM 매도 결정 → 실주문(페이퍼)까지 한 흐름.

이미 1주씩 보유한 상태에서 시작 → 같은 종목 매도 → RiskGate 통과 → PaperBroker 체결 →
Journal 에 ORDER FILLED 기록 (rejection 이 아닌) 까지 검증한다.

usage:
    PYTHONPATH=src python -m scripts.demo_buy_then_sell
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import structlog

from kr_ai_trader.agents.moderator import Moderator
from kr_ai_trader.broker.base import Order, OrderSide, Quote
from kr_ai_trader.broker.paper import PaperBroker
from kr_ai_trader.config import get_settings
from kr_ai_trader.data.prices import compute_features, summary_to_dict
from kr_ai_trader.data.universe import load_universe
from kr_ai_trader.execution.executor import Executor
from kr_ai_trader.journal.recorder import JournalRecorder
from kr_ai_trader.llm.factory import get_llm
from kr_ai_trader.risk.gate import RiskGate

log = structlog.get_logger("demo")
TICKER = "000660"  # RSI 79 → 매도 신호 강함
SEED_QTY = 3


async def main() -> int:
    settings = get_settings()
    universe = load_universe(settings.universe, settings.universe_file)
    llm = get_llm(settings)

    broker = PaperBroker(initial_cash=10_000_000.0)
    journal = JournalRecorder()
    gate = RiskGate(settings=settings, universe=universe)
    executor = Executor(broker=broker, risk_gate=gate, journal=journal, strategy_name="demo")
    moderator = Moderator(llm=llm)

    features = compute_features(TICKER)
    quote = Quote(
        ticker=TICKER, price=features.last_close, timestamp=datetime.now(timezone.utc)
    )
    broker.set_quote(quote)

    seed = await broker.place_order(
        Order(client_order_id=f"seed-{TICKER}", ticker=TICKER, side=OrderSide.buy, quantity=SEED_QTY)
    )
    log.info("seed.filled", ticker=TICKER, qty=SEED_QTY, price=seed.filled_avg_price)

    market_context = json.dumps(summary_to_dict(features), ensure_ascii=False, indent=2)
    proposal = await moderator.decide(ticker=TICKER, market_context=market_context)
    if proposal is None:
        log.warning("moderator.no_action", ticker=TICKER)
        return 1

    order = await executor.execute(proposal)
    if order is None:
        log.warning("executor.rejected")
        return 2

    cash = await broker.get_cash()
    positions = await broker.get_positions()
    result = {
        "ticker": TICKER,
        "seed_qty": SEED_QTY,
        "proposal": {
            "side": proposal.side,
            "conviction": proposal.conviction,
            "size_pct": proposal.size_pct,
            "thesis": proposal.thesis,
        },
        "order": {
            "status": order.status,
            "qty": order.filled_quantity,
            "price": order.filled_avg_price,
            "client_order_id": order.client_order_id,
            "broker_order_id": order.broker_order_id,
        },
        "final_cash": cash,
        "final_positions": [
            {"ticker": p.ticker, "qty": p.quantity, "avg": p.avg_price} for p in positions
        ],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
