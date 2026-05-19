"""RiskGate — 결정론 검사. 모든 거부 사유 explainable 한지."""

from __future__ import annotations

from kr_ai_trader.broker.base import Order, OrderSide, Position
from kr_ai_trader.risk.gate import RiskGate


def _order(ticker: str, side: OrderSide, qty: int) -> Order:
    return Order(
        client_order_id=f"t-{ticker}-{side.value}-{qty}",
        ticker=ticker,
        side=side,
        quantity=qty,
    )


def test_unknown_ticker_rejected(risk_gate: RiskGate) -> None:
    decision = risk_gate.evaluate(
        order=_order("999999", OrderSide.buy, 1),
        cash=10_000_000.0,
        positions=[],
        portfolio_equity=10_000_000.0,
        day_pnl_pct=0.0,
        last_price=10_000.0,
    )
    assert not decision.accepted
    assert any("not in universe" in r for r in decision.reasons)


def test_non_positive_quantity_rejected(risk_gate: RiskGate) -> None:
    decision = risk_gate.evaluate(
        order=_order("005930", OrderSide.buy, 0),
        cash=10_000_000.0,
        positions=[],
        portfolio_equity=10_000_000.0,
        day_pnl_pct=0.0,
        last_price=70_000.0,
    )
    assert not decision.accepted
    assert any("non-positive" in r for r in decision.reasons)


def test_max_position_pct_rejected(risk_gate: RiskGate) -> None:
    # 70_000 * 100 = 7_000_000 → 70% of 10M, way over 3%
    decision = risk_gate.evaluate(
        order=_order("005930", OrderSide.buy, 100),
        cash=10_000_000.0,
        positions=[],
        portfolio_equity=10_000_000.0,
        day_pnl_pct=0.0,
        last_price=70_000.0,
    )
    assert not decision.accepted
    assert any(">" in r and "limit" in r for r in decision.reasons)


def test_leverage_zero_insufficient_cash_rejected(risk_gate: RiskGate) -> None:
    decision = risk_gate.evaluate(
        order=_order("005930", OrderSide.buy, 100),
        cash=1_000_000.0,
        positions=[],
        portfolio_equity=1_000_000.0,
        day_pnl_pct=0.0,
        last_price=70_000.0,
    )
    assert not decision.accepted
    assert any("leverage=0" in r for r in decision.reasons)


def test_daily_loss_halt_blocks_buy(risk_gate: RiskGate) -> None:
    decision = risk_gate.evaluate(
        order=_order("005930", OrderSide.buy, 1),
        cash=10_000_000.0,
        positions=[],
        portfolio_equity=10_000_000.0,
        day_pnl_pct=-2.5,  # halt threshold 2%
        last_price=70_000.0,
    )
    assert not decision.accepted
    assert any("halt threshold" in r for r in decision.reasons)


def test_daily_loss_flatten_blocks_buy_allows_sell(risk_gate: RiskGate) -> None:
    common = dict(
        cash=10_000_000.0,
        positions=[Position(ticker="005930", quantity=10, avg_price=70_000.0, current_price=70_000.0)],
        portfolio_equity=10_000_000.0,
        day_pnl_pct=-4.5,  # flatten threshold 4%
        last_price=70_000.0,
    )
    buy = risk_gate.evaluate(order=_order("005930", OrderSide.buy, 1), **common)
    sell = risk_gate.evaluate(order=_order("005930", OrderSide.sell, 1), **common)

    assert not buy.accepted
    assert any("flatten" in r for r in buy.reasons)
    # 매도는 hold/flatten regime 메시지는 추가되지만 buy block 사유는 없어야 함
    assert all("buy blocked" not in r for r in sell.reasons)


def test_short_selling_blocked(risk_gate: RiskGate) -> None:
    decision = risk_gate.evaluate(
        order=_order("005930", OrderSide.sell, 5),
        cash=10_000_000.0,
        positions=[],
        portfolio_equity=10_000_000.0,
        day_pnl_pct=0.0,
        last_price=70_000.0,
    )
    assert not decision.accepted
    assert any("short-selling blocked" in r for r in decision.reasons)


def test_halt_file_blocks_everything(risk_gate: RiskGate) -> None:
    risk_gate.s.halt_file.write_text("halted")
    try:
        decision = risk_gate.evaluate(
            order=_order("005930", OrderSide.buy, 1),
            cash=10_000_000.0,
            positions=[],
            portfolio_equity=10_000_000.0,
            day_pnl_pct=0.0,
            last_price=70_000.0,
        )
        assert not decision.accepted
        assert any("HALT" in r for r in decision.reasons)
    finally:
        risk_gate.s.halt_file.unlink(missing_ok=True)


def test_clean_buy_accepted(risk_gate: RiskGate) -> None:
    decision = risk_gate.evaluate(
        # ~2.1% of 10M, under 3% limit
        order=_order("005930", OrderSide.buy, 3),
        cash=10_000_000.0,
        positions=[],
        portfolio_equity=10_000_000.0,
        day_pnl_pct=0.0,
        last_price=70_000.0,
    )
    assert decision.accepted, decision.reasons
