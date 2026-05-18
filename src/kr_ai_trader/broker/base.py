"""브로커 공통 인터페이스. PaperBroker / KIS 모두 동일 시그니처."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable


class BrokerError(RuntimeError):
    """주문/조회 실패."""


class OrderSide(StrEnum):
    buy = "buy"
    sell = "sell"


@dataclass(frozen=True)
class Quote:
    ticker: str
    price: float
    timestamp: datetime
    bid: float | None = None
    ask: float | None = None


@dataclass(frozen=True)
class Position:
    ticker: str
    quantity: int
    avg_price: float
    current_price: float

    @property
    def market_value(self) -> float:
        return self.quantity * self.current_price

    @property
    def unrealized_pnl_pct(self) -> float:
        if self.avg_price == 0:
            return 0.0
        return (self.current_price - self.avg_price) / self.avg_price * 100


@dataclass
class Order:
    client_order_id: str       # 멱등키. 동일 ID 재전송 시 같은 결과 보장
    ticker: str
    side: OrderSide
    quantity: int
    limit_price: float | None = None     # None 이면 시장가
    broker_order_id: str | None = None
    status: str = "pending"              # pending | filled | partial | rejected | cancelled
    filled_quantity: int = 0
    filled_avg_price: float = 0.0
    created_at: datetime = field(default_factory=datetime.utcnow)
    rejected_reason: str | None = None


@runtime_checkable
class Broker(Protocol):
    name: str
    is_live: bool

    async def get_cash(self) -> float: ...
    async def get_positions(self) -> list[Position]: ...
    async def get_quote(self, ticker: str) -> Quote: ...
    async def place_order(self, order: Order) -> Order: ...
    async def cancel_order(self, broker_order_id: str) -> bool: ...
