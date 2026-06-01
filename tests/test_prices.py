"""실시장 가격 수집 — pykrx 모킹. 실제 네트워크/pykrx/parquet 의존 없음."""

from __future__ import annotations

import sys
import types
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from kr_ai_trader.data import prices
from kr_ai_trader.data.prices import (
    PriceSummary,
    compute_features,
    get_ohlcv,
    latest_quote,
    summary_to_dict,
)

FIXED_END = date(2026, 5, 29)


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """캐시 디렉토리 격리 + 영업일 고정."""
    monkeypatch.setattr(prices, "CACHE_DIR", tmp_path / "prices")
    monkeypatch.setattr(prices, "previous_business_day", lambda *_: FIXED_END)


def _install_fake_pykrx(monkeypatch: pytest.MonkeyPatch, fn: object) -> None:
    """`from pykrx import stock` 가 반환할 가짜 모듈 주입."""
    stock_mod = types.SimpleNamespace(get_market_ohlcv=fn)
    pykrx_mod = types.ModuleType("pykrx")
    pykrx_mod.stock = stock_mod  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pykrx", pykrx_mod)


def _krx_frame(closes: list[float], *, end: str = "2026-05-29") -> pd.DataFrame:
    """pykrx 가 돌려주는 한글 컬럼 OHLCV 프레임 생성. index 는 연속 일자."""
    n = len(closes)
    dates = pd.date_range(end=pd.to_datetime(end), periods=n, freq="D")
    return pd.DataFrame(
        {
            "시가": [c - 1 for c in closes],
            "고가": [c + 2 for c in closes],
            "저가": [c - 2 for c in closes],
            "종가": closes,
            "거래량": [1000 + i for i in range(n)],
        },
        index=dates,
    )


def _patch_parquet_store(monkeypatch: pytest.MonkeyPatch) -> dict[str, pd.DataFrame]:
    """pyarrow 비의존: read_parquet/to_parquet/Path.exists 를 인메모리 store 로 대체."""
    store: dict[str, pd.DataFrame] = {}
    monkeypatch.setattr(pd, "read_parquet", lambda p, *_a, **_k: store[str(p)].copy(), raising=True)
    monkeypatch.setattr(
        pd.DataFrame,
        "to_parquet",
        lambda self, p, *_a, **_k: store.__setitem__(str(p), self.copy()),
        raising=True,
    )
    monkeypatch.setattr(Path, "exists", lambda self: str(self) in store, raising=True)
    return store


# --- get_ohlcv : 컬럼 rename + 빈 데이터 -----------------------------------


def test_get_ohlcv_renames_korean_columns(monkeypatch: pytest.MonkeyPatch) -> None:
    """한글 컬럼이 open/high/low/close/volume 로 변환되고 index 이름은 date."""
    _install_fake_pykrx(monkeypatch, lambda *_a, **_k: _krx_frame([100, 101, 102]))
    df = get_ohlcv("005930", date(2026, 5, 20), FIXED_END, use_cache=False)
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df.index.name == "date"
    assert float(df["close"].iloc[-1]) == 102.0
    assert isinstance(df.index, pd.DatetimeIndex)


def test_get_ohlcv_empty_returns_typed_empty_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    """pykrx 빈 결과 -> 정의된 컬럼만 가진 빈 프레임."""
    _install_fake_pykrx(monkeypatch, lambda *_a, **_k: pd.DataFrame())
    df = get_ohlcv("000660", "20260520", "20260529", use_cache=False)
    assert df.empty
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]


def test_get_ohlcv_none_returns_empty_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    """pykrx None 반환 -> 빈 프레임 (예외 없음)."""
    _install_fake_pykrx(monkeypatch, lambda *_a, **_k: None)
    df = get_ohlcv("035420", "20260520", "20260529", use_cache=False)
    assert df.empty


def test_get_ohlcv_accepts_string_dates(monkeypatch: pytest.MonkeyPatch) -> None:
    """start/end 가 문자열이어도 그대로 ymd 로 전달."""
    captured: dict[str, str] = {}

    def _ohlcv(start: str, end: str, ticker: str) -> pd.DataFrame:
        captured.update(start=start, end=end, ticker=ticker)
        return _krx_frame([100, 101])

    _install_fake_pykrx(monkeypatch, _ohlcv)
    get_ohlcv("005930", "20260101", "20260131", use_cache=False)
    assert captured == {"start": "20260101", "end": "20260131", "ticker": "005930"}


# --- get_ohlcv : 캐시 hit/miss --------------------------------------------


def test_get_ohlcv_cache_miss_then_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    """첫 호출(miss)은 pykrx 사용 + 캐시 기록, 둘째 호출(hit)은 pykrx 미호출."""
    _patch_parquet_store(monkeypatch)
    # end 를 FIXED_END 로 두어 캐시 신선도 조건(최근 3일 이내)을 만족시킨다.
    _install_fake_pykrx(monkeypatch, lambda *_a, **_k: _krx_frame([100, 101, 102], end="2026-05-29"))

    first = get_ohlcv("005930", date(2026, 5, 20), FIXED_END, use_cache=True)
    assert float(first["close"].iloc[-1]) == 102.0

    def must_not_call(*_a: object, **_k: object) -> pd.DataFrame:
        raise AssertionError("캐시 적중 시 pykrx 를 호출하면 안 됨")

    _install_fake_pykrx(monkeypatch, must_not_call)
    second = get_ohlcv("005930", date(2026, 5, 20), FIXED_END, use_cache=True)
    assert float(second["close"].iloc[-1]) == 102.0
    assert list(second.columns) == ["open", "high", "low", "close", "volume"]


def test_get_ohlcv_stale_cache_refetches(monkeypatch: pytest.MonkeyPatch) -> None:
    """캐시는 있으나 최신성(최근 3일) 미달이면 pykrx 재조회."""
    _patch_parquet_store(monkeypatch)
    # 오래된 캐시: 마지막 일자가 요청 end 보다 한참 이전.
    _install_fake_pykrx(monkeypatch, lambda *_a, **_k: _krx_frame([90, 91], end="2026-05-01"))
    get_ohlcv("005930", date(2026, 4, 25), date(2026, 5, 1), use_cache=True)

    # 신선한 데이터로 교체 후 최근 end 요청 -> 캐시가 stale 이므로 다시 호출되어야 함.
    refetched = {"hit": False}

    def _fresh(*_a: object, **_k: object) -> pd.DataFrame:
        refetched["hit"] = True
        return _krx_frame([100, 101, 102], end="2026-05-29")

    _install_fake_pykrx(monkeypatch, _fresh)
    df = get_ohlcv("005930", date(2026, 5, 20), FIXED_END, use_cache=True)
    assert refetched["hit"] is True
    assert float(df["close"].iloc[-1]) == 102.0


# --- latest_quote ----------------------------------------------------------


def test_latest_quote_returns_last_close_and_date(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_pykrx(monkeypatch, lambda *_a, **_k: _krx_frame([100, 101, 105], end="2026-05-29"))
    price, when = latest_quote("005930")
    assert price == 105.0
    assert when == FIXED_END


def test_latest_quote_empty_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """데이터가 없으면 ValueError."""
    _install_fake_pykrx(monkeypatch, lambda *_a, **_k: pd.DataFrame())
    with pytest.raises(ValueError, match="No price data"):
        latest_quote("999999")


# --- compute_features : RSI/SMA/pct_change 수치 정확성 ---------------------


def test_compute_features_numeric_correctness(monkeypatch: pytest.MonkeyPatch) -> None:
    """크래프트한 종가 시리즈로 RSI/SMA/pct_change 수치를 정확히 검증."""
    closes = [
        100, 101, 102, 101, 103, 104, 103, 105, 106, 105,
        107, 108, 107, 109, 110, 109, 111, 112, 111, 113,
        114, 113, 115, 116, 118,
    ]
    _install_fake_pykrx(monkeypatch, lambda *_a, **_k: _krx_frame(closes, end="2026-05-29"))

    s = compute_features("005930")
    assert isinstance(s, PriceSummary)
    assert s.ticker == "005930"
    assert s.last_close == 118.0
    # pct_change: 직전/5일전/20일전 종가 대비 (모듈과 동일 산식, 사전 계산값).
    assert s.pct_change_1d == 1.724
    assert s.pct_change_5d == 4.425
    assert s.pct_change_20d == 14.563
    assert s.sma_5 == 115.2
    assert s.sma_20 == 109.8
    assert s.rsi_14 == 78.95
    assert s.volume == 1024  # 1000 + (25-1)
    assert s.as_of == FIXED_END


def test_compute_features_short_series_uses_neutral_rsi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """period+1 미만이면 RSI 는 중립 50, SMA 는 종가로 대체. (>=5 행 필요)"""
    closes = [100, 102, 101, 103, 105]  # 5 행
    _install_fake_pykrx(monkeypatch, lambda *_a, **_k: _krx_frame(closes, end="2026-05-29"))
    s = compute_features("005930")
    assert s.rsi_14 == 50.0  # 데이터 부족 -> 중립
    assert s.sma_20 == s.last_close  # 20일 미만 -> last_close 대체
    assert s.sma_5 == 102.2  # (100+102+101+103+105)/5
    assert s.pct_change_20d == 0.0  # 20일 데이터 부족 -> 0


def test_compute_features_not_enough_data_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """5행 미만이면 ValueError."""
    _install_fake_pykrx(monkeypatch, lambda *_a, **_k: _krx_frame([100, 101], end="2026-05-29"))
    with pytest.raises(ValueError, match="Not enough price data"):
        compute_features("005930")


def test_compute_features_empty_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """완전 빈 데이터도 ValueError."""
    _install_fake_pykrx(monkeypatch, lambda *_a, **_k: pd.DataFrame())
    with pytest.raises(ValueError, match="Not enough price data"):
        compute_features("005930")


# --- summary_to_dict --------------------------------------------------------


def test_summary_to_dict_isoformats_date() -> None:
    s = PriceSummary(
        ticker="005930",
        last_close=118.0,
        pct_change_1d=1.724,
        pct_change_5d=4.425,
        pct_change_20d=14.563,
        sma_5=115.2,
        sma_20=109.8,
        rsi_14=78.95,
        volume=1024,
        as_of=date(2026, 5, 29),
    )
    d = summary_to_dict(s)
    assert d["ticker"] == "005930"
    assert d["last_close"] == 118.0
    assert d["rsi_14"] == 78.95
    assert d["volume"] == 1024
    assert d["as_of"] == "2026-05-29"
