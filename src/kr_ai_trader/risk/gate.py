"""결정론적 리스크 게이트.

LLM 제안 → 이 게이트 → 통과한 것만 주문. 모든 거부 사유는 explainable.

각 검사가 [cskwork/investment-agent-rules](https://github.com/cskwork/investment-agent-rules)
의 10대 계명 중 어느 것을 강제하는지 명시. RiskDecision.principles_applied 에 반환.

검사 → 계명 매핑:
- HALT 파일 → Behavioral Discipline (10), Capital Preservation (1)
- 티커 화이트리스트 → Circle of Competence (3)
- 사이즈 0/음수 → Process Over Outcome (9)
- 일일 손실 halt/flatten → Capital Preservation (1)
- 레버리지 0 → Capital Preservation (1)
- 종목 비중 한도 → Concentration vs Diversification (7), Position Sizing (6)
- 섹터 비중 한도 → Concentration vs Diversification (7)
- hard_stop_pct (제안 거리) → Position Sizing & Asymmetric R:R (6)
- 공매도 차단 → Capital Preservation (1)
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
    adjusted_quantity: int | None = None     # 사이즈 조정 시
    principles_applied: list[str] = field(default_factory=list)
    principles_violated: list[str] = field(default_factory=list)


# 계명 번호 → 한 줄 설명 (UI/저널/Decision 표시용)
PRINCIPLES: dict[int, str] = {
    1: "Capital Preservation First",
    2: "Margin of Safety",
    3: "Circle of Competence",
    4: "Second-Level Thinking",
    5: "Cycles and Reflexivity",
    6: "Position Sizing and Asymmetric R:R",
    7: "Concentration vs Diversification",
    8: "Long-term Compounding",
    9: "Process Over Outcome",
    10: "Behavioral Discipline",
}


def _label(n: int) -> str:
    return f"{n:02d} {PRINCIPLES[n]}"


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
        proposed_stop_loss_pct: float | None = None,
    ) -> RiskDecision:
        reasons: list[str] = []
        violated: set[int] = set()
        applied: set[int] = set()

        # 1. HALT — Behavioral Discipline + Capital Preservation
        applied.update({10, 1})
        if self.s.halt_file.exists():
            reasons.append(f"HALT file present: {self.s.halt_file}")
            violated.update({10, 1})

        # 3. Circle of Competence — 화이트리스트 외 차단
        applied.add(3)
        if order.ticker not in self.universe:
            reasons.append(f"ticker {order.ticker} not in universe (LLM hallucination guard)")
            violated.add(3)

        # 9. Process Over Outcome — 사이즈 0/음수
        applied.add(9)
        if order.quantity <= 0:
            reasons.append(f"non-positive quantity: {order.quantity}")
            violated.add(9)

        # 1. Capital Preservation — 일일 손실 서킷브레이커
        applied.add(1)
        if day_pnl_pct <= -abs(self.s.daily_loss_flatten_pct):
            reasons.append(
                f"daily loss {day_pnl_pct:.2f}% breached flatten threshold "
                f"{-self.s.daily_loss_flatten_pct}%; only liquidation allowed"
            )
            violated.add(1)
            if order.side == OrderSide.buy:
                reasons.append("buy blocked under flatten regime")
        elif day_pnl_pct <= -abs(self.s.daily_loss_halt_pct) and order.side == OrderSide.buy:
            reasons.append(
                f"daily loss {day_pnl_pct:.2f}% breached halt threshold "
                f"{-self.s.daily_loss_halt_pct}%; new buys halted"
            )
            violated.add(1)

        notional = last_price * order.quantity

        # 1. Capital Preservation — 레버리지 0
        applied.add(1)
        if order.side == OrderSide.buy and self.s.leverage == 0 and notional > cash:
            reasons.append(f"leverage=0 and notional {notional:.0f} > cash {cash:.0f}")
            violated.add(1)

        # 6 + 7. Concentration vs Diversification + Position Sizing
        if portfolio_equity > 0 and order.side == OrderSide.buy:
            applied.update({6, 7})
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
                violated.update({6, 7})

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
                    violated.add(7)

        # 6. Position Sizing & Asymmetric R:R — 손절 거리 vs hard_stop_pct
        if proposed_stop_loss_pct is not None and self.s.hard_stop_pct > 0:
            applied.add(6)
            if proposed_stop_loss_pct > self.s.hard_stop_pct:
                reasons.append(
                    f"proposed stop_loss {proposed_stop_loss_pct:.1f}% > "
                    f"hard_stop {self.s.hard_stop_pct}% (calendar 6)"
                )
                violated.add(6)

        # 1. Capital Preservation — 공매도 차단
        if order.side == OrderSide.sell:
            applied.add(1)
            held = sum(p.quantity for p in positions if p.ticker == order.ticker)
            if held < order.quantity:
                reasons.append(
                    f"sell qty {order.quantity} > held {held} (short-selling blocked)"
                )
                violated.add(1)

        return RiskDecision(
            accepted=not reasons,
            reasons=reasons,
            principles_applied=sorted(_label(n) for n in applied),
            principles_violated=sorted(_label(n) for n in violated),
        )
