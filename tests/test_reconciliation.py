"""reconciliation 단위 테스트 — 가짜 인메모리 브로커로 drift 탐지/HALT 검증."""

from __future__ import annotations

from pathlib import Path

from kr_ai_trader.broker.base import Position
from kr_ai_trader.execution.reconciliation import (
    reconcile,
    reconcile_and_guard,
)


class FakeBroker:
    """get_positions 만 의미 있는 인메모리 스텁. Broker 프로토콜 충족."""

    name = "fake"
    is_live = False

    def __init__(self, positions: list[Position], *, raise_on_get: bool = False) -> None:
        self._positions = positions
        self._raise = raise_on_get

    async def get_cash(self) -> float:
        return 0.0

    async def get_positions(self) -> list[Position]:
        if self._raise:
            raise RuntimeError("broker connection lost")
        return list(self._positions)

    async def get_quote(self, ticker: str):  # pragma: no cover - 미사용
        raise NotImplementedError

    async def place_order(self, order):  # pragma: no cover - 미사용
        raise NotImplementedError

    async def cancel_order(self, broker_order_id: str):  # pragma: no cover - 미사용
        raise NotImplementedError


def _pos(ticker: str, qty: int) -> Position:
    return Position(ticker=ticker, quantity=qty, avg_price=100.0, current_price=110.0)


async def test_ok_path_no_discrepancies() -> None:
    broker = FakeBroker([_pos("005930", 10), _pos("000660", 5)])
    result = await reconcile(broker, {"005930": 10, "000660": 5})
    assert result.ok is True
    assert result.discrepancies == []
    assert result.checked_at  # ISO 타임스탬프 존재


async def test_detects_missing() -> None:
    # 장부엔 있는데 브로커엔 없음 -> missing
    broker = FakeBroker([])
    result = await reconcile(broker, {"005930": 10})
    assert result.ok is False
    [d] = result.discrepancies
    assert d.kind == "missing"
    assert (d.expected_qty, d.actual_qty) == (10, 0)


async def test_detects_extra() -> None:
    # 브로커엔 있는데 장부엔 없음 -> extra
    broker = FakeBroker([_pos("035420", 7)])
    result = await reconcile(broker, {})
    assert result.ok is False
    [d] = result.discrepancies
    assert d.kind == "extra"
    assert (d.expected_qty, d.actual_qty) == (0, 7)


async def test_detects_qty_mismatch() -> None:
    broker = FakeBroker([_pos("005930", 8)])
    result = await reconcile(broker, {"005930": 10})
    assert result.ok is False
    [d] = result.discrepancies
    assert d.kind == "qty_mismatch"
    assert (d.expected_qty, d.actual_qty) == (10, 8)


async def test_tolerance_absorbs_small_drift() -> None:
    broker = FakeBroker([_pos("005930", 9)])
    result = await reconcile(broker, {"005930": 10}, tolerance=1)
    assert result.ok is True


async def test_graceful_degrade_on_broker_exception() -> None:
    broker = FakeBroker([], raise_on_get=True)
    result = await reconcile(broker, {"005930": 10})
    # 외부 의존성이 죽어도 raise 하지 않고 안전한 빈 결과 반환
    assert result.ok is True
    assert result.discrepancies == []


async def test_guard_touches_halt_file_and_alerts_on_mismatch(tmp_path: Path) -> None:
    halt = tmp_path / "guard" / "HALT"
    broker = FakeBroker([_pos("005930", 8)])

    sent: list[str] = []

    async def on_alert(msg: str) -> None:
        sent.append(msg)

    result = await reconcile_and_guard(
        broker, {"005930": 10}, halt_file=halt, on_alert=on_alert
    )
    assert result.ok is False
    assert halt.exists()  # RiskGate 차단용
    assert len(sent) == 1
    assert "005930" in sent[0]


async def test_guard_no_halt_when_ok(tmp_path: Path) -> None:
    halt = tmp_path / "HALT"
    broker = FakeBroker([_pos("005930", 10)])
    result = await reconcile_and_guard(broker, {"005930": 10}, halt_file=halt)
    assert result.ok is True
    assert not halt.exists()


async def test_guard_halts_even_if_alert_raises(tmp_path: Path) -> None:
    halt = tmp_path / "HALT"
    broker = FakeBroker([_pos("005930", 8)])

    async def bad_alert(msg: str) -> None:
        raise RuntimeError("slack down")

    result = await reconcile_and_guard(
        broker, {"005930": 10}, halt_file=halt, on_alert=bad_alert
    )
    # 알림 실패해도 HALT 는 반드시 떨어져야 함
    assert result.ok is False
    assert halt.exists()
