"""실시장 가격 수집 — pykrx 기반.

- `get_ohlcv` : 백테스트/지표 계산용 일봉
- `latest_quote` : 가장 최근 영업일 종가 (모의 거래 호가 대용)
- `compute_features` : 최근 가격에서 LLM 컨텍스트용 요약 피처 계산

캐싱: `cache/prices/{ticker}.parquet`. 같은 날짜 범위가 이미 캐시에 있으면 재사용.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from .calendar import previous_business_day

CACHE_DIR = Path("cache/prices")
PYKRX_COLUMNS = {
    "시가": "open",
    "고가": "high",
    "저가": "low",
    "종가": "close",
    "거래량": "volume",
}


@dataclass(frozen=True)
class PriceSummary:
    """LLM 컨텍스트에 넘기는 종목 요약."""

    ticker: str
    last_close: float
    pct_change_1d: float
    pct_change_5d: float
    pct_change_20d: float
    sma_5: float
    sma_20: float
    rsi_14: float
    volume: int
    as_of: date


def _cache_path(ticker: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{ticker}.parquet"


def get_ohlcv(
    ticker: str,
    start: date | str,
    end: date | str,
    *,
    use_cache: bool = True,
) -> pd.DataFrame:
    """일봉 OHLCV 조회. 컬럼: open/high/low/close/volume (index=일자)."""
    start_s = start if isinstance(start, str) else start.strftime("%Y%m%d")
    end_s = end if isinstance(end, str) else end.strftime("%Y%m%d")

    cache_file = _cache_path(ticker)
    if use_cache and cache_file.exists():
        cached = pd.read_parquet(cache_file)
        cached.index = pd.to_datetime(cached.index)
        mask = (cached.index >= pd.to_datetime(start_s)) & (cached.index <= pd.to_datetime(end_s))
        slice_ = cached.loc[mask]
        if not slice_.empty and slice_.index.max() >= pd.to_datetime(end_s) - pd.Timedelta(days=3):
            return slice_

    from pykrx import stock as krx_stock

    raw = krx_stock.get_market_ohlcv(start_s, end_s, ticker)
    if raw is None or raw.empty:
        return pd.DataFrame(columns=list(PYKRX_COLUMNS.values()))
    df = raw.rename(columns=PYKRX_COLUMNS)[list(PYKRX_COLUMNS.values())].copy()
    df.index = pd.to_datetime(df.index)
    df.index.name = "date"

    if use_cache:
        with contextlib.suppress(Exception):
            df.to_parquet(cache_file)
    return df


def latest_quote(ticker: str, lookback_days: int = 10) -> tuple[float, date]:
    """가장 최근 영업일의 종가 + 그 날짜 반환. 모의 거래용 단가."""
    end = previous_business_day()
    start = end - timedelta(days=lookback_days)
    df = get_ohlcv(ticker, start, end)
    if df.empty:
        raise ValueError(f"No price data for {ticker} in {start}..{end}")
    last_row = df.iloc[-1]
    last_date = df.index[-1].date()
    return float(last_row["close"]), last_date


def _rsi(close: pd.Series, period: int = 14) -> float:
    if len(close) < period + 1:
        return 50.0
    delta = close.diff().dropna()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    val = rsi.dropna().iloc[-1] if not rsi.dropna().empty else 50.0
    return float(val)


def compute_features(ticker: str, *, lookback_days: int = 60) -> PriceSummary:
    end = previous_business_day()
    start = end - timedelta(days=lookback_days)
    df = get_ohlcv(ticker, start, end)
    if df.empty or len(df) < 5:
        raise ValueError(f"Not enough price data for {ticker}")
    close = df["close"]
    last_close = float(close.iloc[-1])

    def pct(n: int) -> float:
        if len(close) <= n:
            return 0.0
        ref = float(close.iloc[-1 - n])
        return (last_close - ref) / ref * 100.0 if ref else 0.0

    sma_5 = float(close.rolling(5).mean().iloc[-1]) if len(close) >= 5 else last_close
    sma_20 = float(close.rolling(20).mean().iloc[-1]) if len(close) >= 20 else last_close
    return PriceSummary(
        ticker=ticker,
        last_close=last_close,
        pct_change_1d=round(pct(1), 3),
        pct_change_5d=round(pct(5), 3),
        pct_change_20d=round(pct(20), 3),
        sma_5=round(sma_5, 2),
        sma_20=round(sma_20, 2),
        rsi_14=round(_rsi(close), 2),
        volume=int(df["volume"].iloc[-1]),
        as_of=df.index[-1].date(),
    )


def summary_to_dict(s: PriceSummary) -> dict[str, object]:
    return {
        "ticker": s.ticker,
        "last_close": s.last_close,
        "pct_change_1d": s.pct_change_1d,
        "pct_change_5d": s.pct_change_5d,
        "pct_change_20d": s.pct_change_20d,
        "sma_5": s.sma_5,
        "sma_20": s.sma_20,
        "rsi_14": s.rsi_14,
        "volume": s.volume,
        "as_of": s.as_of.isoformat(),
    }
