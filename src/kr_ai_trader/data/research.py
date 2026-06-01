"""리서치 컨텍스트 집계 — 기술적/펀더멘털/공시/뉴스/섹터 한 번에.

Phase 1 의 순수 모듈들(prices/fundamentals/dart/news/sector_map)을 묶어
모더레이터의 `market_context` 에 넣을 단일 JSON 문자열을 만든다.

- `build_research_context` : Settings -> 각 하위 소스 파라미터 주입 + 수집.
- `research_to_context_string` : 4000자 클램프 아래로 압축한 JSON 문자열.

각 하위 소스는 자체적으로 graceful degrade 하지만, 이 레이어에서도 방어적으로
예외를 잡아 부분 실패가 사이클을 멈추지 않도록 한다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import structlog

from ..config import Settings
from .dart import Disclosure, disclosures_to_list, get_disclosures
from .fundamentals import FundamentalSummary, fundamentals_to_dict, get_fundamentals
from .news import NewsItem, get_news
from .prices import PriceSummary, compute_features, summary_to_dict
from .sector_map import build_sector_map

log = structlog.get_logger(__name__)

# 컨텍스트 문자열을 모더레이터의 4000자 클램프 아래로 유지하기 위한 상한.
_MAX_DISCLOSURES = 5
_MAX_NEWS = 6


@dataclass(frozen=True)
class ResearchContext:
    """종목 1건의 통합 리서치 스냅샷."""

    ticker: str
    price: PriceSummary
    fundamentals: FundamentalSummary
    disclosures: list[Disclosure]
    news: list[NewsItem]
    sector: str | None
    company_name: str | None


def _resolve_company_name(ticker: str) -> str | None:
    """pykrx 로 한국어 종목명 조회. 실패 시 None (예외 미전파)."""
    try:
        from pykrx import stock as krx_stock  # type: ignore[import-not-found]

        name = krx_stock.get_market_ticker_name(ticker)
    except Exception as exc:  # 데이터 소스 죽어도 사이클은 살아남아야 함.
        log.warning("research.company_name_failed", ticker=ticker, error=str(exc))
        return None
    return str(name) if name else None


def build_research_context(
    ticker: str,
    *,
    settings: Settings,
    company_name: str | None = None,
) -> ResearchContext:
    """종목 1건의 통합 리서치 컨텍스트 수집.

    기술적 피처(필수) + 펀더멘털 + (활성 시) DART 공시 + (활성 시) 뉴스 + 섹터.
    하위 소스 각각 graceful degrade 하나, 여기서도 방어적으로 감싼다.
    """
    price = compute_features(ticker)  # 가격은 사이클의 핵심 — 실패 시 호출자가 skip 처리.

    fundamentals = get_fundamentals(ticker)

    name = company_name or _resolve_company_name(ticker)

    disclosures: list[Disclosure] = []
    if settings.enable_dart and settings.dart_api_key is not None:
        api_key = settings.dart_api_key.get_secret_value()
        disclosures = get_disclosures(
            ticker, api_key=api_key, lookback_days=settings.dart_lookback_days
        )

    news: list[NewsItem] = []
    if settings.enable_news and name:
        news = get_news(name, max_items=settings.news_lookback_items)

    sector = build_sector_map([ticker]).get(ticker)

    return ResearchContext(
        ticker=ticker,
        price=price,
        fundamentals=fundamentals,
        disclosures=disclosures,
        news=news,
        sector=sector,
        company_name=name,
    )


def research_to_context_string(rc: ResearchContext) -> str:
    """모더레이터 `market_context` 용 압축 JSON 문자열.

    공시/뉴스는 개수를 캡해 4000자 클램프 아래로 유지한다.
    """
    payload: dict[str, object] = {
        "ticker": rc.ticker,
        "company_name": rc.company_name,
        "sector": rc.sector,
        "technical": summary_to_dict(rc.price),
        "fundamentals": fundamentals_to_dict(rc.fundamentals),
        "disclosures": [
            {"rcept_dt": d["rcept_dt"], "report_nm": d["report_nm"]}
            for d in disclosures_to_list(rc.disclosures[:_MAX_DISCLOSURES])
        ],
        "news": [item.title for item in rc.news[:_MAX_NEWS]],
    }
    return json.dumps(payload, ensure_ascii=False)
