"""제안 → 리스크 게이트 → 주문 실행 → 저널 기록."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo

import structlog

from ..agents.moderator import TradeProposal
from ..broker.base import Broker, Order, OrderSide
from ..journal.recorder import JournalRecorder
from ..risk.gate import RiskGate

log = structlog.get_logger(__name__)

KST = ZoneInfo("Asia/Seoul")


def _qty_bucket(qty: int) -> int:
    """수량을 코스피 board lot(1주) 기준 그대로 사용. 추후 변경 시 여기만 수정."""
    return qty


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
    def make_idempotent_id(
        strategy: str, ticker: str, side: str, qty: int, *, when: datetime | None = None
    ) -> str:
        """결정론적 client_order_id.

        동일 (strategy, ticker, KST trading date, side, qty_bucket) 재전송 시 같은 키를 생성하여
        PaperBroker/KIS 양쪽에서 dedup 가능. uuid/wall-clock 미포함.
        """
        moment = (when or datetime.now(timezone.utc)).astimezone(KST)
        date_s = moment.strftime("%Y%m%d")
        payload = f"{strategy}|{ticker}|{date_s}|{side}|{_qty_bucket(qty)}"
        digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]
        return f"{strategy}-{date_s}-{ticker}-{side}-{digest}"

    async def execute(
        self,
        proposal: TradeProposal,
        *,
        day_pnl_pct: float = 0.0,
    ) -> Order | None:
        side: Literal[OrderSide.buy, OrderSide.sell]
        side = OrderSide.buy if proposal.side == "buy" else OrderSide.sell

        # LLM 환각 가드: universe 외 티커는 broker.get_quote 호출 전에 차단.
        if proposal.ticker not in self.risk_gate.universe:
            reasons = [f"ticker {proposal.ticker} not in universe (pre-quote guard)"]
            log.warning("risk_gate.pre_quote_rejected", ticker=proposal.ticker, reasons=reasons)
            await self.journal.record_rejection(proposal=proposal, reasons=reasons)
            return None

        cash = await self.broker.get_cash()
        positions = await self.broker.get_positions()
        quote = await self.broker.get_quote(proposal.ticker)
        equity = cash + sum(p.market_value for p in positions)

        target_notional = equity * (proposal.size_pct / 100.0)
        qty = int(target_notional // quote.price)
        # 매수: 1주 미만 노출은 건너뜀. 매도: 보유 수량을 초과하지 않도록 clamp.
        if side == OrderSide.buy:
            if qty < 1:
                reasons = [
                    f"target_notional {target_notional:.0f} < price {quote.price:.0f}; "
                    "skipped (below 1 share)"
                ]
                log.info("executor.skipped", ticker=proposal.ticker, reasons=reasons)
                await self.journal.record_rejection(proposal=proposal, reasons=reasons)
                return None
        else:
            held = sum(p.quantity for p in positions if p.ticker == proposal.ticker)
            qty = max(0, min(qty if qty > 0 else held, held))
            if qty == 0:
                reasons = [f"no position to sell for {proposal.ticker}"]
                await self.journal.record_rejection(proposal=proposal, reasons=reasons)
                return None

        order = Order(
            client_order_id=self.make_idempotent_id(
                self.strategy_name, proposal.ticker, side.value, qty
            ),
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
