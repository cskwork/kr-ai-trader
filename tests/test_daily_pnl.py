"""DailyPnLTracker 단위 테스트 — KST 날짜 동결로 롤오버/멱등/손익 검증."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from kr_ai_trader.ops.daily_pnl import DailyPnLTracker, day_pnl_to_dict

KST = ZoneInfo("Asia/Seoul")


@pytest.fixture
def pnl_file(tmp_path: Path) -> Path:
    return tmp_path / "daily_pnl.json"


def _kst(year: int, month: int, day: int, hour: int = 10) -> datetime:
    return datetime(year, month, day, hour, tzinfo=KST)


def test_no_start_recorded_yields_zero(pnl_file: Path) -> None:
    tracker = DailyPnLTracker(path=pnl_file)
    result = tracker.compute(9_500_000.0, when=_kst(2026, 6, 2))
    assert result.pnl_pct == 0.0
    assert result.start_equity == 0.0
    assert result.current_equity == 9_500_000.0
    assert result.trading_date == "2026-06-02"


def test_pnl_math_loss_and_gain(pnl_file: Path) -> None:
    tracker = DailyPnLTracker(path=pnl_file)
    when = _kst(2026, 6, 2)
    tracker.start_of_day(10_000_000.0, when=when)

    loss = tracker.compute(9_800_000.0, when=when)
    assert loss.pnl_pct == pytest.approx(-2.0)

    gain = tracker.compute(10_300_000.0, when=when)
    assert gain.pnl_pct == pytest.approx(3.0)
    assert gain.start_equity == 10_000_000.0


def test_start_of_day_is_idempotent(pnl_file: Path) -> None:
    tracker = DailyPnLTracker(path=pnl_file)
    when = _kst(2026, 6, 2)
    tracker.start_of_day(10_000_000.0, when=when)
    # 같은 KST 날 재호출(다른 시각/다른 자본)은 무시되어야 함.
    tracker.start_of_day(11_000_000.0, when=_kst(2026, 6, 2, hour=14))
    result = tracker.compute(10_000_000.0, when=when)
    assert result.start_equity == 10_000_000.0
    assert result.pnl_pct == pytest.approx(0.0)


def test_day_rollover_resets_start(pnl_file: Path) -> None:
    tracker = DailyPnLTracker(path=pnl_file)
    tracker.start_of_day(10_000_000.0, when=_kst(2026, 6, 2))

    # 다음 날: 이전 start 무시 → 미기록으로 0.0.
    next_day = tracker.compute(9_000_000.0, when=_kst(2026, 6, 3))
    assert next_day.pnl_pct == 0.0
    assert next_day.trading_date == "2026-06-03"

    # 새 날 start 기록 후 정상 계산.
    tracker.start_of_day(9_000_000.0, when=_kst(2026, 6, 3))
    after = tracker.compute(9_180_000.0, when=_kst(2026, 6, 3))
    assert after.pnl_pct == pytest.approx(2.0)


def test_utc_when_converts_to_kst_date(pnl_file: Path) -> None:
    # 2026-06-02 23:00 UTC == 2026-06-03 08:00 KST.
    tracker = DailyPnLTracker(path=pnl_file)
    utc_when = datetime(2026, 6, 2, 23, 0, tzinfo=timezone.utc)
    result = tracker.compute(10_000_000.0, when=utc_when)
    assert result.trading_date == "2026-06-03"


def test_corrupt_file_treated_as_no_start(pnl_file: Path) -> None:
    pnl_file.write_text("{ not valid json", encoding="utf-8")
    tracker = DailyPnLTracker(path=pnl_file)
    result = tracker.compute(9_000_000.0, when=_kst(2026, 6, 2))
    assert result.pnl_pct == 0.0  # 손상 파일은 미기록 강등, 예외 없음


def test_day_pnl_to_dict_roundtrip(pnl_file: Path) -> None:
    tracker = DailyPnLTracker(path=pnl_file)
    tracker.start_of_day(10_000_000.0, when=_kst(2026, 6, 2))
    d = day_pnl_to_dict(tracker.compute(10_500_000.0, when=_kst(2026, 6, 2)))
    assert d == {
        "start_equity": 10_000_000.0,
        "current_equity": 10_500_000.0,
        "pnl_pct": pytest.approx(5.0),
        "trading_date": "2026-06-02",
    }
