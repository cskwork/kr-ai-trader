"""KRX 거래 캘린더 — pykrx 모킹. 실제 네트워크/pykrx 호출 없음."""

from __future__ import annotations

import sys
import types
from datetime import date, datetime, timezone

import pytest

from kr_ai_trader.data import calendar as cal


def _install_fake_pykrx(monkeypatch: pytest.MonkeyPatch, **fns: object) -> None:
    """`from pykrx import stock` 가 반환할 가짜 모듈 주입."""
    stock_mod = types.SimpleNamespace(**fns)
    pykrx_mod = types.ModuleType("pykrx")
    pykrx_mod.stock = stock_mod  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pykrx", pykrx_mod)


def _remove_pykrx(monkeypatch: pytest.MonkeyPatch) -> None:
    """pykrx import 가 ImportError 로 떨어지도록 강제 — fallback 경로 검증용."""

    class _Blocker:
        def find_spec(self, name: str, *_a: object, **_k: object) -> None:
            if name == "pykrx" or name.startswith("pykrx."):
                raise ImportError("pykrx blocked in test")
            return None

    monkeypatch.delitem(sys.modules, "pykrx", raising=False)
    monkeypatch.setattr(sys, "meta_path", [_Blocker(), *sys.meta_path])


# --- is_business_day --------------------------------------------------------


def test_is_business_day_weekend_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    """토/일은 pykrx 호출 없이 즉시 False."""

    def must_not_call(*_a: object, **_k: object) -> str:
        raise AssertionError("주말은 pykrx 를 호출하면 안 됨")

    _install_fake_pykrx(monkeypatch, get_nearest_business_day_in_a_week=must_not_call)
    assert cal.is_business_day(date(2026, 5, 30)) is False  # 토요일
    assert cal.is_business_day(date(2026, 5, 31)) is False  # 일요일


def test_is_business_day_true_when_pykrx_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    """평일 + pykrx 가 같은 날 반환 -> 영업일."""
    _install_fake_pykrx(
        monkeypatch,
        get_nearest_business_day_in_a_week=lambda date: date,  # 입력 ymd 그대로 반환
    )
    assert cal.is_business_day(date(2026, 5, 29)) is True  # 금요일


def test_is_business_day_false_on_holiday(monkeypatch: pytest.MonkeyPatch) -> None:
    """평일이지만 pykrx 가 다른 날(가장 가까운 영업일)을 반환 -> 휴장."""
    _install_fake_pykrx(
        monkeypatch,
        get_nearest_business_day_in_a_week=lambda date: "20260528",  # 입력과 불일치
    )
    assert cal.is_business_day(date(2026, 5, 29)) is False


def test_is_business_day_fallback_on_pykrx_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """pykrx 미설치 -> 주중(<5) 여부로 fallback."""
    _remove_pykrx(monkeypatch)
    assert cal.is_business_day(date(2026, 5, 29)) is True  # 금
    assert cal.is_business_day(date(2026, 5, 30)) is False  # 토


# --- previous_business_day --------------------------------------------------


def test_previous_business_day_uses_pykrx(monkeypatch: pytest.MonkeyPatch) -> None:
    """pykrx 가 반환한 ymd 를 date 로 파싱."""
    _install_fake_pykrx(
        monkeypatch,
        get_previous_business_day=lambda date: "20260528",
    )
    assert cal.previous_business_day(date(2026, 5, 29)) == date(2026, 5, 28)


def test_previous_business_day_default_uses_today(monkeypatch: pytest.MonkeyPatch) -> None:
    """인자 없으면 now_kst().date() 를 기준으로 pykrx 호출."""
    monkeypatch.setattr(cal, "now_kst", lambda: datetime(2026, 5, 29, 10, 0, tzinfo=cal.KST))
    captured: dict[str, str] = {}

    def _prev(date: str) -> str:
        captured["ymd"] = date
        return "20260528"

    _install_fake_pykrx(monkeypatch, get_previous_business_day=_prev)
    result = cal.previous_business_day()
    assert captured["ymd"] == "20260529"
    assert result == date(2026, 5, 28)


def test_previous_business_day_fallback_skips_weekend(monkeypatch: pytest.MonkeyPatch) -> None:
    """pykrx 실패 -> 직전 평일까지 거슬러 올라감. 월요일 기준 -> 직전 금요일."""
    _remove_pykrx(monkeypatch)
    # 2026-06-01 은 월요일 -> 직전 영업일은 2026-05-29(금)
    assert cal.previous_business_day(date(2026, 6, 1)) == date(2026, 5, 29)


# --- market_session ---------------------------------------------------------


def test_market_session_regular(monkeypatch: pytest.MonkeyPatch) -> None:
    """평일 정규장 시간 -> 정규세션 & 시장가 주문 가능."""
    _install_fake_pykrx(monkeypatch, get_nearest_business_day_in_a_week=lambda date: date)
    at = datetime(2026, 5, 29, 10, 0, tzinfo=cal.KST)  # 금 10:00
    s = cal.market_session(at)
    assert s.is_business_day is True
    assert s.is_regular_session is True
    assert s.is_pre_auction is False
    assert s.is_closing_auction is False
    assert s.can_place_market_order is True


def test_market_session_pre_auction(monkeypatch: pytest.MonkeyPatch) -> None:
    """장 시작 전 동시호가 08:30-09:00."""
    _install_fake_pykrx(monkeypatch, get_nearest_business_day_in_a_week=lambda date: date)
    at = datetime(2026, 5, 29, 8, 45, tzinfo=cal.KST)
    s = cal.market_session(at)
    assert s.is_pre_auction is True
    assert s.is_regular_session is False
    assert s.can_place_market_order is False


def test_market_session_closing_auction(monkeypatch: pytest.MonkeyPatch) -> None:
    """장 마감 동시호가 15:20-15:30."""
    _install_fake_pykrx(monkeypatch, get_nearest_business_day_in_a_week=lambda date: date)
    at = datetime(2026, 5, 29, 15, 25, tzinfo=cal.KST)
    s = cal.market_session(at)
    assert s.is_closing_auction is True
    # 15:20-15:30 은 정규장(09:00-15:30) 범위와 겹치므로 정규세션 플래그도 True 다.
    assert s.is_regular_session is True
    assert s.can_place_market_order is True


def test_market_session_weekend_no_orders(monkeypatch: pytest.MonkeyPatch) -> None:
    """주말 -> 비영업일, 어떤 세션도 아님."""
    _install_fake_pykrx(monkeypatch, get_nearest_business_day_in_a_week=lambda date: date)
    at = datetime(2026, 5, 30, 10, 0, tzinfo=cal.KST)  # 토
    s = cal.market_session(at)
    assert s.is_business_day is False
    assert s.can_place_market_order is False


def test_now_kst_is_timezone_aware() -> None:
    n = cal.now_kst()
    assert n.tzinfo is not None
    # UTC 로 환산 가능한 aware datetime 인지만 확인 (시스템 시계 의존 회피).
    assert n.astimezone(timezone.utc).tzinfo is timezone.utc
