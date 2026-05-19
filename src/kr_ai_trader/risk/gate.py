"""결정론적 리스크 게이트.

LLM 제안 → 이 게이트 → 통과한 것만 주문. 모든 거부 사유는 explainable.

검사 항목:
1. HALT 파일 존재 여부
2. 티커 화이트리스트
3. 종목당 비중 한도 (MAX_POSITION_PCT)
4. 섹터 비중 한도는 sector_map 주입 시 활성화
5. 일일 손실 서킷브레이커 (halt / flatten)
6. 레버리지 = 0 (cash 부족 시 거부)
7. 사이즈 0 또는 음수 거부
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..broker.base import Order, OrderSide, Position
from ..config import Settings


class RiskRejection(RuntimeError):
    pass


@dataclass(frozen=True)
class RiskDecision:
    accepted: bool
    reasons: list[str] = field(default_factory=list)
    adjusted_quantity: int | None = None    # 사이즈 조정 시


class RiskGate:
    def __init__(
        self,
        *,
        settings: Settings,
        universe: frozenset[str],
        sector_map: dict[str, str] | None = None,
    ) -> None:
        self.s = settings
        self.universe = universe
        self.sector_map = sector_map or {}

    def evaluate(
        self,
        *,
        order: Order,
        cash: float,
        positions: list[Position],
        portfolio_equity: float,
        day_pnl_pct: float,
        last_price: float,
    ) -> RiskDecision:
        reasons: list[str] = []

        if self.s.halt_file.exists():
            reasons.append(f"HALT file present: {self.s.halt_file}")

        if order.ticker not in self.universe:
            reasons.append(f"ticker {order.ticker} not in universe (LLM hallucination guard)")

        if order.quantity <= 0:
            reasons.append(f"non-positive quantity: {order.quantity}")

        if day_pnl_pct <= -abs(self.s.daily_loss_flatten_pct):
            reasons.append(
                f"daily loss {day_pnl_pct:.2f}% breached flatten threshold "
                f"{-self.s.daily_loss_flatten_pct}%; only liquidation allowed"
            )
            if order.side == OrderSide.buy:
                reasons.append("buy blocked under flatten regime")
        elif day_pnl_pct <= -abs(self.s.daily_loss_halt_pct) and order.side == OrderSide.buy:
            reasons.append(
                f"daily loss {day_pnl_pct:.2f}% breached halt threshold "
                f"{-self.s.daily_loss_halt_pct}%; new buys halted"
            )

        notional = last_price * order.quantity

        if order.side == OrderSide.buy and self.s.leverage == 0 and notional > cash:
            reasons.append(f"leverage=0 and notional {notional:.0f} > cash {cash:.0f}")

        if portfolio_equity > 0 and order.side == OrderSide.buy:
            existing_value = sum(
                p.market_value for p in positions if p.ticker == order.ticker
            )
            new_value = existing_value + notional
            new_pct = new_value / portfolio_equity * 100
            if new_pct > self.s.max_position_pct:
                reasons.append(
                    f"position {order.ticker} would be {new_pct:.2f}% > "
                    f"limit {self.s.max_position_pct}%"
                )

            sector = self.sector_map.get(order.ticker)
            if sector:
                sector_value = sum(
                    p.market_value
                    for p in positions
                    if self.sector_map.get(p.ticker) == sector
                )
                sector_pct = (sector_value + notional) / portfolio_equity * 100
                if sector_pct > self.s.max_sector_pct:
                    reasons.append(
                        f"sector {sector} would be {sector_pct:.2f}% > "
                        f"limit {self.s.max_sector_pct}%"
                    )

        if order.side == OrderSide.sell:
            held = sum(p.quantity for p in positions if p.ticker == order.ticker)
            if held < order.quantity:
                reasons.append(f"sell qty {order.quantity} > held {held} (short-selling blocked)")

        return RiskDecision(accepted=not reasons, reasons=reasons)
