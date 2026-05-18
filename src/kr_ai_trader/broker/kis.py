"""한국투자증권 KIS Open API 어댑터.

`python-kis` (https://github.com/Soju06/python-kis) 위에 우리 Broker 프로토콜을 씌움.
설치: `pip install python-kis` (pyproject 에 이미 포함).
`KIS_LIVE=0` (디폴트) 이면 모의투자, `KIS_LIVE=1` 이면 실계좌. 명시적 플래그 강제.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from .base import Broker, BrokerError, Order, OrderSide, Position, Quote

if TYPE_CHECKING:
    # 런타임에는 lazy import (python-kis 미설치 환경에서도 import 단계 통과)
    pass


class KISBroker(Broker):
    name = "kis"

    def __init__(
        self,
        *,
        app_key: str,
        app_secret: str,
        account_number: str,
        is_live: bool = False,
    ) -> None:
        if not (app_key and app_secret and account_number):
            raise BrokerError("KIS credentials missing (app_key/app_secret/account_number)")
        try:
            from pykis import PyKis  # type: ignore[import-not-found]
        except ImportError as e:
            raise BrokerError("python-kis not installed: pip install python-kis") from e

        self.is_live = is_live
        acct_main, acct_product = account_number.split("-") if "-" in account_number else (account_number, "01")
        self._client = PyKis(
            id="kr-ai-trader",
            account=(acct_main, acct_product),
            appkey=app_key,
            secretkey=app_secret,
            virtual=not is_live,        # virtual=True → 모의투자
            keep_token=True,
        )

    async def get_cash(self) -> float:
        # python-kis API: account().balance()
        try:
            balance = self._client.account().balance()
            return float(balance.deposit_amount)
        except Exception as e:
            raise BrokerError(f"KIS get_cash failed: {e}") from e

    async def get_positions(self) -> list[Position]:
        try:
            balance = self._client.account().balance()
            out: list[Position] = []
            for stock in balance.stocks:
                out.append(
                    Position(
                        ticker=str(stock.symbol),
                        quantity=int(stock.qty),
                        avg_price=float(stock.price),
                        current_price=float(stock.current_price),
                    )
                )
            return out
        except Exception as e:
            raise BrokerError(f"KIS get_positions failed: {e}") from e

    async def get_quote(self, ticker: str) -> Quote:
        try:
            stock = self._client.stock(ticker)
            q = stock.quote()
            return Quote(
                ticker=ticker,
                price=float(q.price),
                timestamp=datetime.utcnow(),
                bid=float(getattr(q, "bid", 0)) or None,
                ask=float(getattr(q, "ask", 0)) or None,
            )
        except Exception as e:
            raise BrokerError(f"KIS get_quote({ticker}) failed: {e}") from e

    async def place_order(self, order: Order) -> Order:
        try:
            stock = self._client.stock(order.ticker)
            kwargs = {"qty": order.quantity}
            if order.limit_price is not None:
                kwargs["price"] = order.limit_price
            if order.side == OrderSide.buy:
                result = stock.buy(**kwargs)
            else:
                result = stock.sell(**kwargs)
            order.broker_order_id = str(getattr(result, "order_no", uuid.uuid4().hex[:12]))
            order.status = "filled" if getattr(result, "filled", True) else "pending"
            order.filled_quantity = int(getattr(result, "filled_qty", order.quantity))
            order.filled_avg_price = float(getattr(result, "avg_price", order.limit_price or 0.0))
            order.created_at = datetime.utcnow()
            return order
        except Exception as e:
            order.status = "rejected"
            order.rejected_reason = str(e)
            return order

    async def cancel_order(self, broker_order_id: str) -> bool:
        try:
            self._client.account().cancel(order_no=broker_order_id)
            return True
        except Exception:
            return False
