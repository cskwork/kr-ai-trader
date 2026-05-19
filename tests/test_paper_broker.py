"""PaperBroker — 거래세, 멱등, 잔고/포지션 일관성."""

from __future__ import annotations

import pytest

from kr_ai_trader.broker.base import Order, OrderSide
from kr_ai_trader.broker.paper import PaperBroker


def _order(client_id: str, side: OrderSide, qty: int, ticker: str = "005930") -> Order:
    return Order(
        client_order_id=client_id,
        ticker=ticker,
        side=side,
        quantity=qty,
    )


async def test_buy_creates_position_and_debits_cash(paper_broker: PaperBroker) -> None:
    initial = await paper_broker.get_cash()
    result = await paper_broker.place_order(_order("c1", OrderSide.buy, 10))
    assert result.status == "filled"
    assert result.filled_quantity == 10
    cash = await paper_broker.get_cash()
    positions = await paper_broker.get_positions()
    assert cash == pytest.approx(initial - 10 * 70_000.0)
    assert len(positions) == 1
    assert positions[0].ticker == "005930"
    assert positions[0].quantity == 10


async def test_sell_applies_transaction_tax(paper_broker: PaperBroker) -> None:
    await paper_broker.place_order(_order("c2", OrderSide.buy, 10))
    cash_after_buy = await paper_broker.get_cash()
    result = await paper_broker.place_order(_order("c3", OrderSide.sell, 5))
    assert result.status == "filled"
    cash_after_sell = await paper_broker.get_cash()
    notional = 5 * 70_000.0
    tax = notional * 0.0018
    assert cash_after_sell == pytest.approx(cash_after_buy + notional - tax)


async def test_idempotent_order_returns_same_result(paper_broker: PaperBroker) -> None:
    first = await paper_broker.place_order(_order("dup", OrderSide.buy, 10))
    second = await paper_broker.place_order(_order("dup", OrderSide.buy, 10))
    assert first.broker_order_id == second.broker_order_id
    positions = await paper_broker.get_positions()
    # 두 번째 호출이 새 포지션을 추가하지 않아야 함
    assert positions[0].quantity == 10


async def test_insufficient_cash_rejected(paper_broker: PaperBroker) -> None:
    huge = await paper_broker.place_order(_order("c4", OrderSide.buy, 10_000))
    assert huge.status == "rejected"
    assert "insufficient cash" in (huge.rejected_reason or "")


async def test_sell_more_than_held_rejected(paper_broker: PaperBroker) -> None:
    await paper_broker.place_order(_order("c5", OrderSide.buy, 5))
    result = await paper_broker.place_order(_order("c6", OrderSide.sell, 10))
    assert result.status == "rejected"
    assert "insufficient position" in (result.rejected_reason or "")
