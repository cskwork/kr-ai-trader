"""펀더멘털 수집 — pykrx 모킹. 실제 네트워크/pykrx 호출 없음."""

from __future__ import annotations

import sys
import types
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from kr_ai_trader.data import fundamentals as fund
from kr_ai_trader.data.fundamentals import (
    FundamentalSummary,
    fundamentals_to_dict,
    get_fundamentals,
)

FIXED_END = date(2026, 5, 29)


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """캐시 디렉토리 격리 + 영업일 고정 + 네이버 기본 차단.

    네이버가 1순위이므로 pykrx fallback 경로를 검증하는 기존 테스트가 실제 네트워크를 타지
    않도록, 기본값으로 _fetch_naver 를 빈 결과로 만든다. 네이버 경로 테스트는 따로 오버라이드.
    """
    def _no_network(*_a: object, **_k: object) -> object:
        raise RuntimeError("network disabled in tests")

    monkeypatch.setattr(fund, "CACHE_DIR", tmp_path / "fundamentals")
    monkeypatch.setattr(fund, "previous_business_day", lambda *_: FIXED_END)
    monkeypatch.setattr(fund.httpx, "get", _no_network)


def _install_fake_pykrx(monkeypatch: pytest.MonkeyPatch, fn: object) -> None:
    """`from pykrx import stock` 가 반환할 가짜 모듈 주입."""
    stock_mod = types.SimpleNamespace(get_market_fundamental_by_date=fn)
    pykrx_mod = types.ModuleType("pykrx")
    pykrx_mod.stock = stock_mod  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pykrx", pykrx_mod)


def _frame(rows: list[dict[str, float]], dates: list[str]) -> pd.DataFrame:
    df = pd.DataFrame(rows, index=pd.to_datetime(dates))
    return df


def test_happy_path_takes_latest_row(monkeypatch: pytest.MonkeyPatch) -> None:
    df = _frame(
        [
            {"BPS": 50000, "PER": 11.0, "PBR": 1.2, "EPS": 5000, "DIV": 2.5, "DPS": 1200},
            {"BPS": 50500, "PER": 10.0, "PBR": 1.1, "EPS": 5300, "DIV": 2.6, "DPS": 1250},
        ],
        ["2026-05-28", "2026-05-29"],
    )
    _install_fake_pykrx(monkeypatch, lambda *_a, **_k: df)

    s = get_fundamentals("005930", use_cache=False)

    assert isinstance(s, FundamentalSummary)
    assert s.ticker == "005930"
    assert s.per == 10.0  # 최근 row
    assert s.pbr == 1.1
    assert s.eps == 5300.0
    assert s.bps == 50500.0
    assert s.div_yield == 2.6
    assert s.dps == 1250.0
    assert s.as_of == FIXED_END


def test_empty_dataframe_yields_all_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_pykrx(monkeypatch, lambda *_a, **_k: pd.DataFrame())

    s = get_fundamentals("000660", use_cache=False)

    assert s.ticker == "000660"
    assert s.per is None
    assert s.pbr is None
    assert s.eps is None
    assert s.bps is None
    assert s.div_yield is None
    assert s.dps is None
    assert s.as_of is None


def test_none_result_yields_all_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_pykrx(monkeypatch, lambda *_a, **_k: None)

    s = get_fundamentals("035420", use_cache=False)

    assert s.as_of is None
    assert s.per is None


def test_graceful_degrade_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> pd.DataFrame:
        raise RuntimeError("pykrx down")

    _install_fake_pykrx(monkeypatch, boom)

    s = get_fundamentals("005930", use_cache=False)  # 예외 전파 안 됨

    assert s == FundamentalSummary(
        ticker="005930",
        per=None,
        pbr=None,
        eps=None,
        bps=None,
        div_yield=None,
        dps=None,
        as_of=None,
    )


def test_nan_values_become_none(monkeypatch: pytest.MonkeyPatch) -> None:
    df = _frame(
        [{"BPS": float("nan"), "PER": 9.5, "PBR": float("nan"),
          "EPS": 100, "DIV": 1.0, "DPS": 50}],
        ["2026-05-29"],
    )
    _install_fake_pykrx(monkeypatch, lambda *_a, **_k: df)

    s = get_fundamentals("005930", use_cache=False)

    assert s.bps is None
    assert s.pbr is None
    assert s.per == 9.5
    assert s.eps == 100.0


def test_cache_write_then_read_without_pykrx(monkeypatch: pytest.MonkeyPatch) -> None:
    # parquet 엔진(pyarrow) 비의존: 인메모리 store 로 read_parquet/to_parquet 대체.
    store: dict[str, pd.DataFrame] = {}
    monkeypatch.setattr(
        pd, "read_parquet", lambda p, *_a, **_k: store[str(p)].copy(), raising=True
    )
    monkeypatch.setattr(
        pd.DataFrame,
        "to_parquet",
        lambda self, p, *_a, **_k: store.__setitem__(str(p), self.copy()),
        raising=True,
    )
    # 캐시 적중 시 Path.exists 가 True 를 돌려주도록 store 키 기준으로 판정.
    monkeypatch.setattr(
        Path, "exists", lambda self: str(self) in store, raising=True
    )

    df = _frame(
        [{"BPS": 50500, "PER": 10.0, "PBR": 1.1, "EPS": 5300, "DIV": 2.6, "DPS": 1250}],
        ["2026-05-29"],
    )
    _install_fake_pykrx(monkeypatch, lambda *_a, **_k: df)
    first = get_fundamentals("005930", use_cache=True)
    assert first.per == 10.0

    # 캐시 적중 시 pykrx 를 호출하면 안 됨 -> 호출되면 폭발하는 함수로 교체.
    def must_not_call(*_a: object, **_k: object) -> pd.DataFrame:
        raise AssertionError("pykrx should not be called on cache hit")

    _install_fake_pykrx(monkeypatch, must_not_call)
    second = get_fundamentals("005930", use_cache=True)
    assert second.per == 10.0
    assert second.as_of == FIXED_END


def _fake_naver_response(infos: list[dict[str, str]]) -> object:
    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"totalInfos": infos}

    return _Resp()


def test_naver_happy_path_no_pykrx_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """네이버 응답이 정상이면 pykrx 는 호출되지 않고 네이버 값 사용."""
    infos = [
        {"code": "per", "value": "28.21배"},
        {"code": "eps", "value": "12,372원"},
        {"code": "pbr", "value": "4.85배"},
        {"code": "bps", "value": "71,907원"},
        {"code": "dividendYieldRatio", "value": "0.48%"},
        {"code": "dividend", "value": "1,668원"},
    ]
    # autouse 가 막아둔 httpx.get 을 정상 응답으로 오버라이드 (네이버 경로 검증).
    monkeypatch.setattr(fund.httpx, "get", lambda *_a, **_k: _fake_naver_response(infos))

    def must_not_call(*_a: object, **_k: object) -> pd.DataFrame:
        raise AssertionError("pykrx must not be called when 네이버 succeeds")

    _install_fake_pykrx(monkeypatch, must_not_call)

    s = get_fundamentals("005930", use_cache=False)
    assert s.per == 28.21
    assert s.eps == 12372.0
    assert s.pbr == 4.85
    assert s.bps == 71907.0
    assert s.div_yield == 0.48
    assert s.dps == 1668.0
    assert s.as_of == FIXED_END


def test_naver_failure_falls_back_to_pykrx(monkeypatch: pytest.MonkeyPatch) -> None:
    """네이버 실패(autouse 기본 차단) -> pykrx fallback 값 사용."""
    df = _frame(
        [{"BPS": 50500, "PER": 10.0, "PBR": 1.1, "EPS": 5300, "DIV": 2.6, "DPS": 1250}],
        ["2026-05-29"],
    )
    _install_fake_pykrx(monkeypatch, lambda *_a, **_k: df)

    s = get_fundamentals("005930", use_cache=False)
    assert s.per == 10.0  # pykrx fallback 값
    assert s.bps == 50500.0


def test_parse_naver_num() -> None:
    assert fund._parse_naver_num("28.21배") == 28.21
    assert fund._parse_naver_num("12,372원") == 12372.0
    assert fund._parse_naver_num("0.48%") == 0.48
    assert fund._parse_naver_num("-") is None
    assert fund._parse_naver_num(None) is None
    assert fund._parse_naver_num("N/A") is None


def test_to_dict_is_none_safe_and_isoformats_date() -> None:
    s = FundamentalSummary(
        ticker="005930",
        per=10.0,
        pbr=None,
        eps=5300.0,
        bps=None,
        div_yield=2.6,
        dps=None,
        as_of=date(2026, 5, 29),
    )
    d = fundamentals_to_dict(s)
    assert d == {
        "ticker": "005930",
        "per": 10.0,
        "pbr": None,
        "eps": 5300.0,
        "bps": None,
        "div_yield": 2.6,
        "dps": None,
        "as_of": "2026-05-29",
    }


def test_to_dict_none_as_of() -> None:
    d = fundamentals_to_dict(
        FundamentalSummary(
            ticker="X", per=None, pbr=None, eps=None,
            bps=None, div_yield=None, dps=None, as_of=None,
        )
    )
    assert d["as_of"] is None
