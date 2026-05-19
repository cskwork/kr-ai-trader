"""페이퍼 트레이딩 1 사이클 — 실시장 데이터 → LLM → 페이퍼 브로커 → 저널.

사이클:
1. pykrx 로 유니버스 상위 N 종목 OHLCV/피처 로드
2. 각 종목당 Bull + Bear + RiskOfficer 모더레이션 → TradeProposal
3. RiskGate 검사 + PaperBroker 주문 + 저널 기록

usage:
    python -m scripts.run_paper            # 한 사이클 실행 후 종료
    python -m scripts.run_paper --cycles 3 # 3 사이클 (sleep 60s 사이)
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone

import structlog

from kr_ai_trader.agents.moderator import Moderator
from kr_ai_trader.broker.base import Quote
from kr_ai_trader.broker.paper import PaperBroker
from kr_ai_trader.config import get_settings
from kr_ai_trader.data.prices import compute_features, summary_to_dict
from kr_ai_trader.data.universe import load_universe
from kr_ai_trader.execution.executor import Executor
from kr_ai_trader.journal.recorder import JournalRecorder
from kr_ai_trader.llm.factory import get_llm
from kr_ai_trader.risk.gate import RiskGate

log = structlog.get_logger("run_paper")

# 풀체크용 기본 후보 — pykrx 인덱스 미인증 시 fallback
DEFAULT_PICKS = [
    "005930",  # 삼성전자
    "000660",  # SK하이닉스
    "035420",  # NAVER
]


async def run_one_cycle(*, top_n: int, initial_cash: float) -> dict:
    settings = get_settings()
    universe = load_universe(settings.universe, settings.universe_file)
    llm = get_llm(settings)

    broker = PaperBroker(initial_cash=initial_cash)
    risk_gate = RiskGate(settings=settings, universe=universe)
    journal = JournalRecorder()
    executor = Executor(
        broker=broker,
        risk_gate=risk_gate,
        journal=journal,
        strategy_name="smoke",
    )
    moderator = Moderator(llm=llm)

    picks = [t for t in DEFAULT_PICKS if t in universe][:top_n]
    if not picks:
        picks = list(universe)[:top_n]

    log.info("cycle.start", provider=llm.name, picks=picks, cash=initial_cash)

    cycle_summary: dict = {
        "provider": llm.name,
        "model": llm.model,
        "picks": picks,
        "decisions": [],
        "orders": [],
        "rejections": [],
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    for ticker in picks:
        try:
            features = compute_features(ticker)
        except Exception as exc:
            log.warning("features.failed", ticker=ticker, error=str(exc))
            continue

        broker.set_quote(
            Quote(
                ticker=ticker,
                price=features.last_close,
                timestamp=datetime.now(timezone.utc),
            )
        )

        market_context = json.dumps(summary_to_dict(features), ensure_ascii=False, indent=2)
        proposal = await moderator.decide(ticker=ticker, market_context=market_context)

        if proposal is None:
            cycle_summary["decisions"].append(
                {"ticker": ticker, "result": "no_action", "features": summary_to_dict(features)}
            )
            log.info("moderator.no_action", ticker=ticker)
            continue

        cycle_summary["decisions"].append(
            {
                "ticker": ticker,
                "result": "proposal",
                "side": proposal.side,
                "conviction": proposal.conviction,
                "size_pct": proposal.size_pct,
                "thesis": proposal.thesis,
                "features": summary_to_dict(features),
            }
        )

        order = await executor.execute(proposal)
        if order is None:
            cycle_summary["rejections"].append({"ticker": ticker, "reason": "risk_gate"})
        else:
            cycle_summary["orders"].append(
                {
                    "ticker": ticker,
                    "side": order.side.value,
                    "qty": order.filled_quantity,
                    "price": order.filled_avg_price,
                    "status": order.status,
                    "client_order_id": order.client_order_id,
                }
            )

    cycle_summary["ended_at"] = datetime.now(timezone.utc).isoformat()
    cycle_summary["final_cash"] = await broker.get_cash()
    cycle_summary["final_positions"] = [
        {"ticker": p.ticker, "qty": p.quantity, "avg": p.avg_price}
        for p in await broker.get_positions()
    ]
    log.info("cycle.done", summary=cycle_summary)
    return cycle_summary


async def main() -> int:
    parser = argparse.ArgumentParser(description="Paper trading single cycle")
    parser.add_argument("--top-n", type=int, default=1, help="번 사이클에서 분석할 종목 수")
    parser.add_argument("--cycles", type=int, default=1, help="반복 사이클 횟수")
    parser.add_argument("--sleep", type=int, default=60, help="사이클 사이 대기 (초)")
    parser.add_argument("--cash", type=float, default=10_000_000.0, help="초기 페이퍼 현금")
    parser.add_argument("--json-out", type=str, default=None, help="결과 JSON 저장 경로")
    args = parser.parse_args()

    summaries = []
    for i in range(args.cycles):
        summary = await run_one_cycle(top_n=args.top_n, initial_cash=args.cash)
        summaries.append(summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
        if i + 1 < args.cycles:
            await asyncio.sleep(args.sleep)

    if args.json_out:
        from pathlib import Path

        Path(args.json_out).write_text(
            json.dumps(summaries, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
