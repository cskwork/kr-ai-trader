"""종목 펀더멘털 수집 — 네이버 금융(로그인 불필요) 우선, pykrx fallback.

- `get_fundamentals` : 최근 영업일 기준 PER/PBR/EPS/BPS/배당 요약
- `fundamentals_to_dict` : None 안전 직렬화 (LLM 컨텍스트/로그용)

'안전마진(Margin of Safety)' 원칙을 직접 지원한다 — PER/PBR 로 가치 대비 가격을 판단.
소스 우선순위:
1. 네이버 금융 모바일 API (https://m.stock.naver.com) — KRX 회원 로그인 불필요, 리테일 기본.
2. pykrx `get_market_fundamental_by_date` — KRX_ID/KRX_PW 가 설정된 환경에서만 동작(캐시).
3. 둘 다 실패하면 모든 필드 None 인 유효 객체로 graceful degrade (예외 미전파).

pykrx 1.2.x 부터 펀더멘털 조회에 KRX 회원 로그인을 요구하므로 네이버를 1순위로 둔다.
캐싱: `cache/fundamentals/{ticker}.parquet` (pykrx 경로만). prices.py 와 동일한 패턴.
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import httpx
import pandas as pd
import structlog

from .calendar import previous_business_day

log = structlog.get_logger(__name__)

CACHE_DIR = Path("cache/fundamentals")

# 네이버 금융 모바일 통합 API. totalInfos[].code -> FundamentalSummary 필드.
NAVER_URL = "https://m.stock.naver.com/api/stock/{ticker}/integration"
NAVER_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}
NAVER_FIELD_MAP = {
    "per": "per",
    "pbr": "pbr",
    "eps": "eps",
    "bps": "bps",
    "dividendYieldRatio": "div_yield",
    "dividend": "dps",
}
# pykrx 한글 컬럼 -> 표준 영문 키. prices.py PYKRX_COLUMNS 패턴 동일.
PYKRX_COLUMNS = {
    "BPS": "bps",
    "PER": "per",
    "PBR": "pbr",
    "EPS": "eps",
    "DIV": "div_yield",
    "DPS": "dps",
}


@dataclass(frozen=True)
class FundamentalSummary:
    """LLM 컨텍스트에 넘기는 펀더멘털 요약. 결측값은 None."""

    ticker: str
    per: float | None
    pbr: float | None
    eps: float | None
    bps: float | None
    div_yield: float | None
    dps: float | None
    as_of: date | None


def _empty(ticker: str) -> FundamentalSummary:
    """모든 지표가 비어 있는 유효 객체. graceful degrade 기본값."""
    return FundamentalSummary(
        ticker=ticker,
        per=None,
        pbr=None,
        eps=None,
        bps=None,
        div_yield=None,
        dps=None,
        as_of=None,
    )


def _cache_path(ticker: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{ticker}.parquet"


def _to_float(value: object) -> float | None:
    """NaN/None/변환불가 -> None. 그 외 float."""
    if value is None:
        return None
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


def _parse_naver_num(value: object) -> float | None:
    """'28.21배' / '12,372원' / '0.48%' 등 네이버 표기 -> float. 변환불가 -> None."""
    if value is None:
        return None
    text = str(value).replace(",", "")
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(m.group(0)) if m else None


def _has_data(s: FundamentalSummary) -> bool:
    """최소 한 개 핵심 지표라도 채워졌는지 (소스 성공 판정)."""
    return any(v is not None for v in (s.per, s.pbr, s.eps, s.bps))


def _fetch_naver(ticker: str, *, timeout: float = 10.0) -> FundamentalSummary:
    """네이버 금융 모바일 API 에서 펀더멘털 조회. 실패 시 _empty (예외 미전파)."""
    try:
        resp = httpx.get(
            NAVER_URL.format(ticker=ticker), headers=NAVER_HEADERS, timeout=timeout
        )
        resp.raise_for_status()
        infos = resp.json().get("totalInfos") or []
    except Exception as exc:
        log.warning("fundamentals.naver_failed", ticker=ticker, error=str(exc))
        return _empty(ticker)

    parsed: dict[str, float | None] = {}
    for item in infos:
        field = NAVER_FIELD_MAP.get(item.get("code"))
        if field:
            parsed[field] = _parse_naver_num(item.get("value"))
    if not parsed:
        return _empty(ticker)
    return FundamentalSummary(
        ticker=ticker,
        per=parsed.get("per"),
        pbr=parsed.get("pbr"),
        eps=parsed.get("eps"),
        bps=parsed.get("bps"),
        div_yield=parsed.get("div_yield"),
        dps=parsed.get("dps"),
        as_of=previous_business_day(),
    )


def _summary_from_row(ticker: str, row: pd.Series, as_of: date) -> FundamentalSummary:
    """정규화된 row(영문 컬럼) -> FundamentalSummary."""
    return FundamentalSummary(
        ticker=ticker,
        per=_to_float(row.get("per")),
        pbr=_to_float(row.get("pbr")),
        eps=_to_float(row.get("eps")),
        bps=_to_float(row.get("bps")),
        div_yield=_to_float(row.get("div_yield")),
        dps=_to_float(row.get("dps")),
        as_of=as_of,
    )


def get_fundamentals(
    ticker: str,
    *,
    lookback_days: int = 10,
    use_cache: bool = True,
    timeout: float = 10.0,
) -> FundamentalSummary:
    """최근 영업일 기준 펀더멘털 요약 조회.

    네이버 금융(로그인 불필요) 1순위 -> 데이터 없으면 pykrx(KRX 로그인 필요) fallback.
    어떤 단계든 실패하면 모든 필드 None 인 유효 객체 반환 (예외 미전파).
    """
    naver = _fetch_naver(ticker, timeout=timeout)
    if _has_data(naver):
        return naver

    # 네이버 실패 시 pykrx fallback (KRX_ID/KRX_PW 설정 환경에서만 데이터 반환).
    end = previous_business_day()
    start = end - timedelta(days=lookback_days)
    cache_file = _cache_path(ticker)
    try:
        df = _fetch_fundamentals(ticker, start, end, cache_file, use_cache=use_cache)
    except Exception as exc:  # 데이터 소스 죽어도 거래 사이클은 살아남아야 함.
        log.warning("fundamentals.fetch_failed", ticker=ticker, error=str(exc))
        return _empty(ticker)

    if df is None or df.empty:
        log.warning("fundamentals.empty", ticker=ticker, start=str(start), end=str(end))
        return _empty(ticker)

    last_row = df.iloc[-1]
    as_of = df.index[-1].date()
    return _summary_from_row(ticker, last_row, as_of)


def _fetch_fundamentals(
    ticker: str,
    start: date,
    end: date,
    cache_file: Path,
    *,
    use_cache: bool,
) -> pd.DataFrame:
    """정규화된(영문 컬럼) 펀더멘털 DataFrame 반환. 캐시 우선, 없으면 pykrx 조회."""
    start_s = start.strftime("%Y%m%d")
    end_s = end.strftime("%Y%m%d")

    if use_cache and cache_file.exists():
        cached = pd.read_parquet(cache_file)
        cached.index = pd.to_datetime(cached.index)
        mask = (cached.index >= pd.to_datetime(start_s)) & (cached.index <= pd.to_datetime(end_s))
        slice_ = cached.loc[mask]
        if not slice_.empty and slice_.index.max() >= pd.to_datetime(end_s) - pd.Timedelta(days=3):
            return slice_

    from pykrx import stock as krx_stock

    raw = krx_stock.get_market_fundamental_by_date(start_s, end_s, ticker)
    if raw is None or raw.empty:
        return pd.DataFrame(columns=list(PYKRX_COLUMNS.values()))

    # 한글/영문 혼재 가능 -> 알려진 컬럼만 표준 영문 키로 rename.
    present = {col: PYKRX_COLUMNS[col] for col in raw.columns if col in PYKRX_COLUMNS}
    df = raw.rename(columns=present)[list(present.values())].copy()
    df.index = pd.to_datetime(df.index)
    df.index.name = "date"

    if use_cache:
        with contextlib.suppress(Exception):
            df.to_parquet(cache_file)
    return df


def fundamentals_to_dict(s: FundamentalSummary) -> dict[str, object]:
    """None 안전 직렬화. as_of 는 isoformat 문자열 또는 None."""
    return {
        "ticker": s.ticker,
        "per": s.per,
        "pbr": s.pbr,
        "eps": s.eps,
        "bps": s.bps,
        "div_yield": s.div_yield,
        "dps": s.dps,
        "as_of": s.as_of.isoformat() if s.as_of is not None else None,
    }
