"""한국투자증권 KIS Open API 어댑터.

`python-kis` (https://github.com/Soju06/python-kis) 위에 우리 Broker 프로토콜을 씌움.
설치: `pip install python-kis` (pyproject 에 이미 포함).
`KIS_LIVE=0` (디폴트) 이면 모의투자, `KIS_LIVE=1` 이면 실계좌. 명시적 플래그 강제.
"""

from __future__ import annotations

from datetime import datetime, timezone
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
                timestamp=datetime.now(timezone.utc),
                bid=float(getattr(q, "bid", 0)) or None,
                ask=float(getattr(q, "ask", 0)) or None,
            )
        except Exception as e:
            raise BrokerError(f"KIS get_quote({ticker}) failed: {e}") from e

    @staticmethod
    def _scrub(msg: str) -> str:
        """upstream 에러 메시지에서 자격증명 흔적 제거 + 길이 클립."""
        for needle in ("appkey", "appsecret", "Authorization", "secretkey"):
            msg = msg.replace(needle, "***")
        return msg[:200]

    async def place_order(self, order: Order) -> Order:
        """KIS 주문 제출.

        - 결정론적 broker_order_id 가 응답에서 오면 사용, 없으면 client_order_id 기반 대체키.
        - **transport/auth 오류는 BrokerError 로 전파** (이전: rejected 로 가려져 운영자 모름).
        - broker 가 명시적 rejection 결과를 주면 그것만 `rejected` 처리.
        """
        try:
            stock = self._client.stock(order.ticker)
        except Exception as e:
            raise BrokerError(f"KIS stock lookup failed: {self._scrub(str(e))}") from e

        kwargs: dict[str, float | int] = {"qty": order.quantity}
        if order.limit_price is not None:
            kwargs["price"] = order.limit_price

        try:
            result = stock.buy(**kwargs) if order.side == OrderSide.buy else stock.sell(**kwargs)
        except Exception as e:
            # 네트워크/타임아웃/인증 오류 → 호출부 (Executor) 에서 알람·정지 결정.
            raise BrokerError(f"KIS place_order transport error: {self._scrub(str(e))}") from e

        # python-kis 응답에 명시적 실패 플래그가 있으면 rejected 로 표기.
        explicit_reject = getattr(result, "rejected", False) or getattr(result, "error", None)
        if explicit_reject:
            order.status = "rejected"
            order.rejected_reason = self._scrub(str(explicit_reject))
            return order

        order.broker_order_id = str(getattr(result, "order_no", None) or f"kis-{order.client_order_id}")
        order.status = "filled" if getattr(result, "filled", True) else "pending"
        order.filled_quantity = int(getattr(result, "filled_qty", order.quantity))
        order.filled_avg_price = float(getattr(result, "avg_price", order.limit_price or 0.0))
        order.created_at = datetime.now(timezone.utc)
        return order

    async def cancel_order(self, broker_order_id: str) -> bool:
        try:
            self._client.account().cancel(order_no=broker_order_id)
            return True
        except Exception as e:
            raise BrokerError(f"KIS cancel_order failed: {self._scrub(str(e))}") from e
