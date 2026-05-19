"""인메모리 페이퍼 브로커. 백테/유닛테스트/오프라인 데모용.

거래비용 모델 (Settings 기반):
- 매수: notional * commission_pct   (브로커 수수료)
- 매도: notional * commission_pct + notional * 거래세
  * 코스피 종목 → tax_kospi_sell_pct
  * 코스닥 종목 → tax_kosdaq_sell_pct
  * KRX 거래세는 시기·시장별로 변하므로 Settings 로 외부화.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from ..config import Settings, get_settings
from .base import Broker, BrokerError, Order, OrderSide, Position, Quote


# 코스닥 6자리 종목코드 prefix 추정 — 실사용 시 sector_map 으로 교체 권장.
# 코스피 종목 코드는 보통 0/1/2/3 으로 시작, 코스닥은 0xxxxx (다른 분포). 분리 불완전하므로
# market_overrides 로 명시 매핑 우선.
def _is_kosdaq(ticker: str, overrides: dict[str, str] | None) -> bool:
    if overrides and ticker in overrides:
        return overrides[ticker].lower() == "kosdaq"
    # 보수적으로 디폴트는 kospi 가정. 실데이터 운영 시 universe metadata 로 보강.
    return False


class PaperBroker(Broker):
    name = "paper"
    is_live = False

    def __init__(
        self,
        *,
        initial_cash: float = 10_000_000.0,
        settings: Settings | None = None,
        market_overrides: dict[str, str] | None = None,
    ) -> None:
        self._cash = initial_cash
        self._positions: dict[str, Position] = {}
        self._last_quotes: dict[str, Quote] = {}
        self._orders: dict[str, Order] = {}     # client_order_id -> Order (멱등)
        self._settings = settings or get_settings()
        self._market_overrides = market_overrides or {}

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

    def _sell_tax_pct(self, ticker: str) -> float:
        s = self._settings
        return s.tax_kosdaq_sell_pct if _is_kosdaq(ticker, self._market_overrides) else s.tax_kospi_sell_pct

    async def place_order(self, order: Order) -> Order:
        # 멱등: 동일 client_order_id 재전송 → 기존 결과 반환
        if order.client_order_id in self._orders:
            return self._orders[order.client_order_id]

        quote = await self.get_quote(order.ticker)
        fill_price = order.limit_price if order.limit_price is not None else quote.price
        notional = fill_price * order.quantity
        commission_pct = self._settings.commission_pct

        if order.side == OrderSide.buy:
            commission = notional * commission_pct
            total_cost = notional + commission
            if total_cost > self._cash:
                order.status = "rejected"
                order.rejected_reason = "insufficient cash (incl. commission)"
                self._orders[order.client_order_id] = order
                return order
            self._cash -= total_cost
            existing = self._positions.get(order.ticker)
            if existing:
                total_q = existing.quantity + order.quantity
                # 매수 수수료를 평단가에 반영 (실현/미실현 PnL 정합성)
                new_cost_basis = existing.avg_price * existing.quantity + notional + commission
                avg = new_cost_basis / total_q
                self._positions[order.ticker] = Position(
                    ticker=order.ticker, quantity=total_q, avg_price=avg, current_price=fill_price
                )
            else:
                avg = (notional + commission) / order.quantity
                self._positions[order.ticker] = Position(
                    ticker=order.ticker,
                    quantity=order.quantity,
                    avg_price=avg,
                    current_price=fill_price,
                )
        else:   # sell
            existing = self._positions.get(order.ticker)
            if not existing or existing.quantity < order.quantity:
                order.status = "rejected"
                order.rejected_reason = "insufficient position"
                self._orders[order.client_order_id] = order
                return order
            commission = notional * commission_pct
            tax = notional * self._sell_tax_pct(order.ticker)
            proceeds = notional - commission - tax
            self._cash += proceeds
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
