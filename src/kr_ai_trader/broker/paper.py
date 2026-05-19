"""인메모리 페이퍼 브로커. 백테/유닛테스트/오프라인 데모용."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from .base import Broker, BrokerError, Order, OrderSide, Position, Quote


class PaperBroker(Broker):
    name = "paper"
    is_live = False

    def __init__(self, *, initial_cash: float = 10_000_000.0) -> None:
        self._cash = initial_cash
        self._positions: dict[str, Position] = {}
        self._last_quotes: dict[str, Quote] = {}
        self._orders: dict[str, Order] = {}     # client_order_id -> Order (멱등)

    def set_quote(self, quote: Quote) -> None:
        """테스트/백테에서 직접 가격 주입."""
        self._last_quotes[quote.ticker] = quote
        if quote.ticker in self._positions:
            pos = self._positions[quote.ticker]
            self._positions[quote.ticker] = Position(
                ticker=pos.ticker,
                quantity=pos.quantity,
                avg_price=pos.avg_price,
                current_price=quote.price,
            )

    async def get_cash(self) -> float:
        return self._cash

    async def get_positions(self) -> list[Position]:
        return list(self._positions.values())

    async def get_quote(self, ticker: str) -> Quote:
        q = self._last_quotes.get(ticker)
        if q is None:
            raise BrokerError(f"no quote for {ticker}; call set_quote() first in paper mode")
        return q

    async def place_order(self, order: Order) -> Order:
        # 멱등: 동일 client_order_id 재전송 → 기존 결과 반환
        if order.client_order_id in self._orders:
            return self._orders[order.client_order_id]

        quote = await self.get_quote(order.ticker)
        fill_price = order.limit_price if order.limit_price is not None else quote.price
        notional = fill_price * order.quantity

        if order.side == OrderSide.buy:
            if notional > self._cash:
                order.status = "rejected"
                order.rejected_reason = "insufficient cash"
                self._orders[order.client_order_id] = order
                return order
            self._cash -= notional
            existing = self._positions.get(order.ticker)
            if existing:
                total_q = existing.quantity + order.quantity
                avg = (existing.avg_price * existing.quantity + notional) / total_q
                self._positions[order.ticker] = Position(
                    ticker=order.ticker, quantity=total_q, avg_price=avg, current_price=fill_price
                )
            else:
                self._positions[order.ticker] = Position(
                    ticker=order.ticker,
                    quantity=order.quantity,
                    avg_price=fill_price,
                    current_price=fill_price,
                )
        else:   # sell
            existing = self._positions.get(order.ticker)
            if not existing or existing.quantity < order.quantity:
                order.status = "rejected"
                order.rejected_reason = "insufficient position"
                self._orders[order.client_order_id] = order
                return order
            # 거래세 0.18% (코스피/코스닥 동일 가정)
            tax = notional * 0.0018
            self._cash += notional - tax
            remaining = existing.quantity - order.quantity
            if remaining == 0:
                del self._positions[order.ticker]
            else:
                self._positions[order.ticker] = Position(
                    ticker=order.ticker,
                    quantity=remaining,
                    avg_price=existing.avg_price,
                    current_price=fill_price,
                )

        order.broker_order_id = f"paper-{uuid.uuid4().hex[:12]}"
        order.status = "filled"
        order.filled_quantity = order.quantity
        order.filled_avg_price = fill_price
        order.created_at = datetime.now(timezone.utc)
        self._orders[order.client_order_id] = order
        return order

    async def cancel_order(self, broker_order_id: str) -> bool:
        # 페이퍼는 즉시 체결되므로 취소 불가
        return False
