"""제안 → 리스크 게이트 → 주문 실행 → 저널 기록."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

import structlog

from ..agents.moderator import TradeProposal
from ..broker.base import Broker, Order, OrderSide
from ..journal.recorder import JournalRecorder
from ..risk.gate import RiskGate

log = structlog.get_logger(__name__)


class Executor:
    def __init__(
        self,
        *,
        broker: Broker,
        risk_gate: RiskGate,
        journal: JournalRecorder,
        strategy_name: str = "default",
    ) -> None:
        self.broker = broker
        self.risk_gate = risk_gate
        self.journal = journal
        self.strategy_name = strategy_name

    @staticmethod
    def _make_idempotent_id(strategy: str, ticker: str) -> str:
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        suffix = uuid.uuid4().hex[:8]
        return f"{strategy}-{today}-{ticker}-{suffix}"

    async def execute(
        self,
        proposal: TradeProposal,
        *,
        day_pnl_pct: float = 0.0,
    ) -> Order | None:
        side: Literal[OrderSide.buy, OrderSide.sell]
        side = OrderSide.buy if proposal.side == "buy" else OrderSide.sell

        cash = await self.broker.get_cash()
        positions = await self.broker.get_positions()
        quote = await self.broker.get_quote(proposal.ticker)
        equity = cash + sum(p.market_value for p in positions)

        target_notional = equity * (proposal.size_pct / 100.0)
        qty = max(1, int(target_notional // quote.price))

        order = Order(
            client_order_id=self._make_idempotent_id(self.strategy_name, proposal.ticker),
            ticker=proposal.ticker,
            side=side,
            quantity=qty,
            limit_price=None,
        )

        decision = self.risk_gate.evaluate(
            order=order,
            cash=cash,
            positions=positions,
            portfolio_equity=equity,
            day_pnl_pct=day_pnl_pct,
            last_price=quote.price,
            proposed_stop_loss_pct=proposal.stop_loss_pct,
        )

        if not decision.accepted:
            log.warning(
                "risk_gate.rejected",
                ticker=proposal.ticker,
                reasons=decision.reasons,
            )
            await self.journal.record_rejection(proposal=proposal, reasons=decision.reasons)
            return None

        result = await self.broker.place_order(order)
        await self.journal.record_order(proposal=proposal, order=result)
        log.info(
            "order.submitted",
            ticker=proposal.ticker,
            side=side.value,
            qty=qty,
            broker=self.broker.name,
            live=self.broker.is_live,
            status=result.status,
        )
        return result
