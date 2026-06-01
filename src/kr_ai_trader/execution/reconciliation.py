"""포지션 정합성 검사 — 내부 장부(expected) vs 브로커 보고(actual).

내부 원장이 추적하는 수량과 브로커가 실제로 보고하는 수량이 어긋나면(drift)
신뢰할 수 없는 상태다. 이 경우 알림을 보내고 HALT 파일을 생성해
RiskGate 가 신규 주문을 차단하도록 한다 (Capital Preservation).

순수 모듈: get_settings() 를 읽지 않는다. halt_file/on_alert 는 호출자가 주입.
브로커 조회 실패는 graceful degrade — 빈 정합 결과를 반환하고 raise 하지 않는다.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import structlog

from ..broker.base import Broker

log = structlog.get_logger(__name__)

# 알림 콜백: 비동기 가능, 메시지 한 줄을 받는다 (예: functools.partial(send_alert)).
AlertFn = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class Discrepancy:
    """단일 종목 불일치. kind: 'missing'|'extra'|'qty_mismatch'."""

    ticker: str
    expected_qty: int
    actual_qty: int
    kind: str


@dataclass(frozen=True)
class ReconResult:
    """정합성 검사 결과. ok=True 면 tolerance 이내로 일치."""

    ok: bool
    discrepancies: list[Discrepancy] = field(default_factory=list)
    checked_at: str = ""


def discrepancy_to_dict(d: Discrepancy) -> dict[str, object]:
    return {
        "ticker": d.ticker,
        "expected_qty": d.expected_qty,
        "actual_qty": d.actual_qty,
        "kind": d.kind,
    }


def recon_result_to_dict(r: ReconResult) -> dict[str, object]:
    return {
        "ok": r.ok,
        "checked_at": r.checked_at,
        "discrepancies": [discrepancy_to_dict(d) for d in r.discrepancies],
    }


def _classify(ticker: str, expected_qty: int, actual_qty: int) -> Discrepancy:
    """expected/actual 쌍을 불일치 종류로 분류. 입력은 변경하지 않는다."""
    if expected_qty == 0:
        kind = "extra"          # 장부엔 없는데 브로커엔 있음
    elif actual_qty == 0:
        kind = "missing"        # 장부엔 있는데 브로커엔 없음
    else:
        kind = "qty_mismatch"   # 둘 다 있으나 수량 다름
    return Discrepancy(ticker=ticker, expected_qty=expected_qty, actual_qty=actual_qty, kind=kind)


async def reconcile(
    broker: Broker,
    expected: dict[str, int],
    *,
    tolerance: int = 0,
) -> ReconResult:
    """내부 장부 expected(ticker->qty) 를 브로커 보고 포지션과 대조.

    수량 차이가 tolerance 를 초과하는 종목만 Discrepancy 로 기록.
    브로커 조회 실패 시 graceful degrade — ok=True 빈 결과를 반환하고 raise 하지
    않는다 (조회 불가만으로 HALT 를 걸면 일시 장애에 과반응하게 되므로).
    """
    checked_at = datetime.now(timezone.utc).isoformat()

    try:
        positions = await broker.get_positions()
    except Exception as exc:  # 외부 의존성 죽어도 거래 사이클은 살아야 함
        log.warning("reconcile.get_positions_failed", error=str(exc))
        return ReconResult(ok=True, discrepancies=[], checked_at=checked_at)

    actual: dict[str, int] = {p.ticker: p.quantity for p in positions}

    discrepancies: list[Discrepancy] = []
    for ticker in sorted(set(expected) | set(actual)):
        exp = expected.get(ticker, 0)
        act = actual.get(ticker, 0)
        if abs(exp - act) > tolerance:
            discrepancies.append(_classify(ticker, exp, act))

    ok = not discrepancies
    if not ok:
        log.warning(
            "reconcile.drift_detected",
            count=len(discrepancies),
            tickers=[d.ticker for d in discrepancies],
        )
    return ReconResult(ok=ok, discrepancies=discrepancies, checked_at=checked_at)


async def reconcile_and_guard(
    broker: Broker,
    expected: dict[str, int],
    *,
    halt_file: Path,
    tolerance: int = 0,
    on_alert: AlertFn | None = None,
) -> ReconResult:
    """정합성 검사 후 불일치 시 알림 + HALT 파일 생성으로 신규 주문 차단.

    on_alert 실패는 절대 raise 하지 않는다 — HALT 만은 반드시 떨어뜨려야 하므로.
    """
    result = await reconcile(broker, expected, tolerance=tolerance)
    if result.ok:
        return result

    message = _build_alert_message(result)

    if on_alert is not None:
        try:
            await on_alert(message)
        except Exception as exc:  # 알림 실패가 HALT 를 막아선 안 됨
            log.warning("reconcile.alert_failed", error=str(exc))

    # HALT 파일 touch — RiskGate.evaluate 가 존재만으로 신규 주문을 막는다.
    try:
        halt_file.parent.mkdir(parents=True, exist_ok=True)
        halt_file.touch(exist_ok=True)
        log.error("reconcile.halt_engaged", halt_file=str(halt_file), message=message)
    except Exception as exc:  # HALT 파일 쓰기 실패도 사이클을 멈추지 않음
        log.error("reconcile.halt_touch_failed", halt_file=str(halt_file), error=str(exc))

    return result


def _build_alert_message(result: ReconResult) -> str:
    """사람이 읽을 한 줄 알림 메시지."""
    parts = [
        f"{d.ticker} {d.kind}(exp={d.expected_qty},act={d.actual_qty})"
        for d in result.discrepancies
    ]
    return "POSITION DRIFT — halting trading: " + "; ".join(parts)
