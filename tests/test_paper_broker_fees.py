"""PaperBroker 수수료/세금 회귀 테스트.

리뷰 결과: 기존 PaperBroker 가 매수 수수료를 무시했음. Settings 기반 commission_pct 가
양방향에 적용되는지, 매도 거래세가 함께 차감되는지 확인.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kr_ai_trader.broker.base import Order, OrderSide, Quote
from kr_ai_trader.broker.paper import PaperBroker
from kr_ai_trader.config import Settings


@pytest.fixture
def fee_settings() -> Settings:
    return Settings(
        commission_pct=0.0005,         # 0.05%
        tax_kospi_sell_pct=0.0018,
        tax_kosdaq_sell_pct=0.0023,
        kis_live=False,
    )


def _quote(ticker: str = "005930", price: float = 70_000.0) -> Quote:
    return Quote(ticker=ticker, price=price, timestamp=datetime.now(timezone.utc))


async def test_buy_charges_commission(fee_settings: Settings) -> None:
    broker = PaperBroker(initial_cash=10_000_000.0, settings=fee_settings)
    broker.set_quote(_quote())
    notional = 10 * 70_000.0
    commission = notional * fee_settings.commission_pct
    await broker.place_order(
        Order(client_order_id="b1", ticker="005930", side=OrderSide.buy, quantity=10)
    )
    cash = await broker.get_cash()
    assert cash == pytest.approx(10_000_000.0 - notional - commission)


async def test_sell_charges_commission_plus_tax(fee_settings: Settings) -> None:
    broker = PaperBroker(initial_cash=10_000_000.0, settings=fee_settings)
    broker.set_quote(_quote())
    await broker.place_order(
        Order(client_order_id="b1", ticker="005930", side=OrderSide.buy, quantity=10)
    )
    cash_after_buy = await broker.get_cash()
    notional = 5 * 70_000.0
    await broker.place_order(
        Order(client_order_id="s1", ticker="005930", side=OrderSide.sell, quantity=5)
    )
    cash_after_sell = await broker.get_cash()
    expected_proceeds = notional - notional * fee_settings.commission_pct - notional * 0.0018
    assert cash_after_sell == pytest.approx(cash_after_buy + expected_proceeds)


async def test_kosdaq_uses_different_tax_rate(fee_settings: Settings) -> None:
    broker = PaperBroker(
        initial_cash=10_000_000.0,
        settings=fee_settings,
        market_overrides={"000660": "kosdaq"},
    )
    broker.set_quote(_quote("000660", 150_000.0))
    await broker.place_order(
        Order(client_order_id="b", ticker="000660", side=OrderSide.buy, quantity=10)
    )
    cash_after_buy = await broker.get_cash()
    await broker.place_order(
        Order(client_order_id="s", ticker="000660", side=OrderSide.sell, quantity=5)
    )
    notional = 5 * 150_000.0
    expected_proceeds = notional - notional * fee_settings.commission_pct - notional * 0.0023
    assert (await broker.get_cash()) == pytest.approx(cash_after_buy + expected_proceeds)


async def test_buy_rejects_when_cash_below_total_cost(fee_settings: Settings) -> None:
    # cash 정확히 notional 만큼 — commission 때문에 부족해야 함.
    notional = 10 * 70_000.0
    broker = PaperBroker(initial_cash=notional, settings=fee_settings)
    broker.set_quote(_quote())
    result = await broker.place_order(
        Order(client_order_id="b", ticker="005930", side=OrderSide.buy, quantity=10)
    )
    assert result.status == "rejected"
    assert "commission" in (result.rejected_reason or "")
