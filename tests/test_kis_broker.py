"""KISBroker 어댑터 테스트 — 가짜 `pykis` 모듈 주입. 실제 네트워크/SDK 호출 없음.

`python-kis` 는 OPTIONAL 의존성이므로 `from pykis import PyKis` 가 통과하도록
test_fundamentals 와 동일하게 sys.modules 에 가짜 모듈을 심는다.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from kr_ai_trader.broker.base import BrokerError, Order, OrderSide, Position, Quote


class _FakeAccount:
    """python-kis account() 가 돌려주는 객체 흉내."""

    def __init__(
        self,
        *,
        deposit: float = 0.0,
        stocks: list[Any] | None = None,
        cancel_raises: Exception | None = None,
    ) -> None:
        self.deposit_amount = deposit
        self.stocks = stocks or []
        self._cancel_raises = cancel_raises
        self.cancelled: list[str] = []

    def balance(self) -> _FakeAccount:
        return self

    def cancel(self, *, order_no: str) -> None:
        if self._cancel_raises is not None:
            raise self._cancel_raises
        self.cancelled.append(order_no)


class _FakeStock:
    """python-kis stock(ticker) 가 돌려주는 객체 흉내."""

    def __init__(
        self,
        *,
        quote_obj: Any = None,
        buy_result: Any = None,
        sell_result: Any = None,
        buy_raises: Exception | None = None,
    ) -> None:
        self._quote_obj = quote_obj
        self._buy_result = buy_result
        self._sell_result = sell_result
        self._buy_raises = buy_raises
        self.buy_kwargs: dict[str, Any] | None = None
        self.sell_kwargs: dict[str, Any] | None = None

    def quote(self) -> Any:
        return self._quote_obj

    def buy(self, **kwargs: Any) -> Any:
        self.buy_kwargs = kwargs
        if self._buy_raises is not None:
            raise self._buy_raises
        return self._buy_result

    def sell(self, **kwargs: Any) -> Any:
        self.sell_kwargs = kwargs
        return self._sell_result


class _FakePyKis:
    """PyKis 클라이언트 흉내. 생성 인자 보관 + stock/account 라우팅."""

    last_instance: _FakePyKis | None = None

    def __init__(self, **kwargs: Any) -> None:
        self.init_kwargs = kwargs
        self._account = _FakeAccount()
        self._stocks: dict[str, _FakeStock] = {}
        self.default_stock = _FakeStock()
        self.stock_raises: Exception | None = None
        _FakePyKis.last_instance = self

    # 테스트 헬퍼 — 라우팅 객체 주입
    def set_account(self, account: _FakeAccount) -> None:
        self._account = account

    def set_stock(self, ticker: str, stock: _FakeStock) -> None:
        self._stocks[ticker] = stock

    # python-kis API surface
    def account(self) -> _FakeAccount:
        return self._account

    def stock(self, ticker: str) -> _FakeStock:
        if self.stock_raises is not None:
            raise self.stock_raises
        return self._stocks.get(ticker, self.default_stock)


@pytest.fixture
def fake_pykis(monkeypatch: pytest.MonkeyPatch) -> type[_FakePyKis]:
    """`from pykis import PyKis` 가 가짜 클래스를 반환하도록 sys.modules 주입."""
    _FakePyKis.last_instance = None
    pykis_mod = types.ModuleType("pykis")
    pykis_mod.PyKis = _FakePyKis  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pykis", pykis_mod)
    return _FakePyKis


def _make_broker(fake: type[_FakePyKis], *, is_live: bool = False) -> Any:
    from kr_ai_trader.broker.kis import KISBroker

    return KISBroker(
        app_key="key",
        app_secret="secret",
        account_number="12345678-01",
        is_live=is_live,
    )


# --------------------------------------------------------------------------- #
# 생성자 / 자격증명 / paper-live 플래그
# --------------------------------------------------------------------------- #
def test_init_paper_sets_virtual_true(fake_pykis: type[_FakePyKis]) -> None:
    broker = _make_broker(fake_pykis, is_live=False)

    assert broker.is_live is False
    assert broker.name == "kis"
    inst = _FakePyKis.last_instance
    assert inst is not None
    assert inst.init_kwargs["virtual"] is True  # 모의투자
    # account_number 의 "-" 분리 검증
    assert inst.init_kwargs["account"] == ("12345678", "01")


def test_init_live_sets_virtual_false(fake_pykis: type[_FakePyKis]) -> None:
    broker = _make_broker(fake_pykis, is_live=True)

    assert broker.is_live is True
    inst = _FakePyKis.last_instance
    assert inst is not None
    assert inst.init_kwargs["virtual"] is False  # 실계좌


def test_init_account_without_dash_defaults_product_01(fake_pykis: type[_FakePyKis]) -> None:
    from kr_ai_trader.broker.kis import KISBroker

    KISBroker(app_key="k", app_secret="s", account_number="50012345", is_live=False)
    inst = _FakePyKis.last_instance
    assert inst is not None
    assert inst.init_kwargs["account"] == ("50012345", "01")


def test_init_missing_credentials_raises_before_import(fake_pykis: type[_FakePyKis]) -> None:
    from kr_ai_trader.broker.kis import KISBroker

    with pytest.raises(BrokerError, match="credentials missing"):
        KISBroker(app_key="", app_secret="s", account_number="123", is_live=False)


def test_init_without_pykis_installed_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """pykis 미설치 시 import 단계에서 명확한 BrokerError."""
    # import 가 실패하도록 sys.modules 에 None 을 심으면 ImportError 발생.
    monkeypatch.setitem(sys.modules, "pykis", None)
    from kr_ai_trader.broker.kis import KISBroker

    with pytest.raises(BrokerError, match="python-kis not installed"):
        KISBroker(app_key="k", app_secret="s", account_number="123", is_live=False)


# --------------------------------------------------------------------------- #
# get_cash
# --------------------------------------------------------------------------- #
async def test_get_cash_translates_deposit_amount(fake_pykis: type[_FakePyKis]) -> None:
    broker = _make_broker(fake_pykis)
    _FakePyKis.last_instance.set_account(_FakeAccount(deposit=1_234_567.0))  # type: ignore[union-attr]

    cash = await broker.get_cash()

    assert cash == 1_234_567.0
    assert isinstance(cash, float)


async def test_get_cash_wraps_sdk_error(fake_pykis: type[_FakePyKis]) -> None:
    broker = _make_broker(fake_pykis)

    class _Boom(_FakeAccount):
        def balance(self) -> _FakeAccount:
            raise RuntimeError("token expired")

    _FakePyKis.last_instance.set_account(_Boom())  # type: ignore[union-attr]

    with pytest.raises(BrokerError, match="KIS get_cash failed"):
        await broker.get_cash()


# --------------------------------------------------------------------------- #
# get_positions
# --------------------------------------------------------------------------- #
async def test_get_positions_translates_stock_shape(fake_pykis: type[_FakePyKis]) -> None:
    broker = _make_broker(fake_pykis)
    stock = types.SimpleNamespace(symbol="005930", qty=10, price=70000, current_price=72000)
    _FakePyKis.last_instance.set_account(_FakeAccount(stocks=[stock]))  # type: ignore[union-attr]

    positions = await broker.get_positions()

    assert len(positions) == 1
    pos = positions[0]
    assert isinstance(pos, Position)
    assert pos.ticker == "005930"
    assert pos.quantity == 10
    assert pos.avg_price == 70000.0
    assert pos.current_price == 72000.0


async def test_get_positions_empty_balance_returns_empty(fake_pykis: type[_FakePyKis]) -> None:
    broker = _make_broker(fake_pykis)
    _FakePyKis.last_instance.set_account(_FakeAccount(stocks=[]))  # type: ignore[union-attr]

    assert await broker.get_positions() == []


async def test_get_positions_malformed_stock_wraps_error(fake_pykis: type[_FakePyKis]) -> None:
    broker = _make_broker(fake_pykis)
    # qty 가 숫자로 변환 불가 → int() 폭발 → BrokerError 로 래핑.
    bad = types.SimpleNamespace(symbol="X", qty="not-a-number", price=1, current_price=2)
    _FakePyKis.last_instance.set_account(_FakeAccount(stocks=[bad]))  # type: ignore[union-attr]

    with pytest.raises(BrokerError, match="KIS get_positions failed"):
        await broker.get_positions()


# --------------------------------------------------------------------------- #
# get_quote
# --------------------------------------------------------------------------- #
async def test_get_quote_translates_with_bid_ask(fake_pykis: type[_FakePyKis]) -> None:
    broker = _make_broker(fake_pykis)
    q = types.SimpleNamespace(price=70100, bid=70000, ask=70200)
    _FakePyKis.last_instance.set_stock("005930", _FakeStock(quote_obj=q))  # type: ignore[union-attr]

    quote = await broker.get_quote("005930")

    assert isinstance(quote, Quote)
    assert quote.ticker == "005930"
    assert quote.price == 70100.0
    assert quote.bid == 70000.0
    assert quote.ask == 70200.0


async def test_get_quote_missing_bid_ask_becomes_none(fake_pykis: type[_FakePyKis]) -> None:
    """bid/ask 속성 부재 → getattr 디폴트 0 → None 으로 정규화."""
    broker = _make_broker(fake_pykis)
    q = types.SimpleNamespace(price=70100)  # bid/ask 없음
    _FakePyKis.last_instance.set_stock("005930", _FakeStock(quote_obj=q))  # type: ignore[union-attr]

    quote = await broker.get_quote("005930")

    assert quote.price == 70100.0
    assert quote.bid is None
    assert quote.ask is None


async def test_get_quote_wraps_sdk_error(fake_pykis: type[_FakePyKis]) -> None:
    broker = _make_broker(fake_pykis)
    inst = _FakePyKis.last_instance
    assert inst is not None
    inst.stock_raises = RuntimeError("rate limited")

    with pytest.raises(BrokerError, match=r"KIS get_quote\(005930\) failed"):
        await broker.get_quote("005930")


# --------------------------------------------------------------------------- #
# place_order
# --------------------------------------------------------------------------- #
def _order(side: OrderSide = OrderSide.buy, *, limit: float | None = 70000.0) -> Order:
    return Order(
        client_order_id="cid-1",
        ticker="005930",
        side=side,
        quantity=10,
        limit_price=limit,
    )


async def test_place_order_buy_maps_to_buy_call_and_fills(fake_pykis: type[_FakePyKis]) -> None:
    broker = _make_broker(fake_pykis)
    result = types.SimpleNamespace(
        order_no="KIS-999", filled=True, filled_qty=10, avg_price=70050
    )
    stock = _FakeStock(buy_result=result)
    _FakePyKis.last_instance.set_stock("005930", stock)  # type: ignore[union-attr]

    out = await broker.place_order(_order(OrderSide.buy))

    # buy() 가 호출되고 qty/price 가 전달됐는지
    assert stock.buy_kwargs == {"qty": 10, "price": 70000.0}
    assert out.broker_order_id == "KIS-999"
    assert out.status == "filled"
    assert out.filled_quantity == 10
    assert out.filled_avg_price == 70050.0


async def test_place_order_sell_maps_to_sell_call(fake_pykis: type[_FakePyKis]) -> None:
    broker = _make_broker(fake_pykis)
    result = types.SimpleNamespace(order_no="KIS-S1", filled=True)
    stock = _FakeStock(sell_result=result)
    _FakePyKis.last_instance.set_stock("005930", stock)  # type: ignore[union-attr]

    out = await broker.place_order(_order(OrderSide.sell))

    assert stock.sell_kwargs == {"qty": 10, "price": 70000.0}
    assert out.broker_order_id == "KIS-S1"
    assert out.status == "filled"


async def test_place_order_market_order_omits_price(fake_pykis: type[_FakePyKis]) -> None:
    """limit_price None → 시장가 → price kwarg 미전달."""
    broker = _make_broker(fake_pykis)
    result = types.SimpleNamespace(order_no="M1", filled=True)
    stock = _FakeStock(buy_result=result)
    _FakePyKis.last_instance.set_stock("005930", stock)  # type: ignore[union-attr]

    out = await broker.place_order(_order(OrderSide.buy, limit=None))

    assert stock.buy_kwargs == {"qty": 10}
    assert out.status == "filled"
    # avg_price 응답 부재 + 시장가 → 디폴트 0.0
    assert out.filled_avg_price == 0.0


async def test_place_order_missing_fields_use_defaults(fake_pykis: type[_FakePyKis]) -> None:
    """응답에 order_no/filled_qty/avg_price 부재 → 안전 디폴트 적용."""
    broker = _make_broker(fake_pykis)
    result = types.SimpleNamespace()  # 아무 속성도 없음
    stock = _FakeStock(buy_result=result)
    _FakePyKis.last_instance.set_stock("005930", stock)  # type: ignore[union-attr]

    out = await broker.place_order(_order(OrderSide.buy, limit=70000.0))

    # order_no 부재 → client_order_id 기반 대체키
    assert out.broker_order_id == "kis-cid-1"
    assert out.status == "filled"  # filled getattr 디폴트 True
    assert out.filled_quantity == 10  # 디폴트 order.quantity
    assert out.filled_avg_price == 70000.0  # 디폴트 limit_price


async def test_place_order_explicit_rejection_sets_rejected(fake_pykis: type[_FakePyKis]) -> None:
    """응답에 rejected/error 플래그 → status='rejected', 예외 없음."""
    broker = _make_broker(fake_pykis)
    result = types.SimpleNamespace(rejected=True, error="장 마감")
    stock = _FakeStock(buy_result=result)
    _FakePyKis.last_instance.set_stock("005930", stock)  # type: ignore[union-attr]

    out = await broker.place_order(_order(OrderSide.buy))

    assert out.status == "rejected"
    assert out.rejected_reason is not None
    assert out.broker_order_id is None  # rejected 는 broker_order_id 미설정


async def test_place_order_transport_error_raises_brokererror(fake_pykis: type[_FakePyKis]) -> None:
    """네트워크/인증 오류는 rejected 로 가리지 않고 BrokerError 전파."""
    broker = _make_broker(fake_pykis)
    stock = _FakeStock(buy_raises=RuntimeError("connection reset"))
    _FakePyKis.last_instance.set_stock("005930", stock)  # type: ignore[union-attr]

    with pytest.raises(BrokerError, match="transport error"):
        await broker.place_order(_order(OrderSide.buy))


async def test_place_order_stock_lookup_error_raises(fake_pykis: type[_FakePyKis]) -> None:
    broker = _make_broker(fake_pykis)
    inst = _FakePyKis.last_instance
    assert inst is not None
    inst.stock_raises = RuntimeError("symbol not found")

    with pytest.raises(BrokerError, match="stock lookup failed"):
        await broker.place_order(_order(OrderSide.buy))


async def test_place_order_scrubs_credentials_from_error(fake_pykis: type[_FakePyKis]) -> None:
    """에러 메시지에서 자격증명 키워드 제거 확인."""
    broker = _make_broker(fake_pykis)
    stock = _FakeStock(buy_raises=RuntimeError("bad appkey and secretkey leak"))
    _FakePyKis.last_instance.set_stock("005930", stock)  # type: ignore[union-attr]

    with pytest.raises(BrokerError) as ei:
        await broker.place_order(_order(OrderSide.buy))

    msg = str(ei.value)
    assert "appkey" not in msg
    assert "secretkey" not in msg
    assert "***" in msg


def test_scrub_clips_and_masks() -> None:
    from kr_ai_trader.broker.kis import KISBroker

    raw = "Authorization appsecret " + "x" * 500
    scrubbed = KISBroker._scrub(raw)
    assert len(scrubbed) <= 200
    assert "Authorization" not in scrubbed
    assert "appsecret" not in scrubbed


# --------------------------------------------------------------------------- #
# cancel_order
# --------------------------------------------------------------------------- #
async def test_cancel_order_success(fake_pykis: type[_FakePyKis]) -> None:
    broker = _make_broker(fake_pykis)
    acct = _FakeAccount()
    _FakePyKis.last_instance.set_account(acct)  # type: ignore[union-attr]

    ok = await broker.cancel_order("KIS-999")

    assert ok is True
    assert acct.cancelled == ["KIS-999"]


async def test_cancel_order_wraps_error(fake_pykis: type[_FakePyKis]) -> None:
    broker = _make_broker(fake_pykis)
    acct = _FakeAccount(cancel_raises=RuntimeError("already filled"))
    _FakePyKis.last_instance.set_account(acct)  # type: ignore[union-attr]

    with pytest.raises(BrokerError, match="KIS cancel_order failed"):
        await broker.cancel_order("KIS-999")
