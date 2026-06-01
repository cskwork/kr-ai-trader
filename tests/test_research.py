"""research 집계 모듈 통합 테스트 — 네트워크/pykrx 호출 0 (전부 monkeypatch)."""

from __future__ import annotations

import json
from datetime import date

import pytest

from kr_ai_trader.config import Settings
from kr_ai_trader.data import research as research_mod
from kr_ai_trader.data.dart import Disclosure
from kr_ai_trader.data.fundamentals import FundamentalSummary
from kr_ai_trader.data.news import NewsItem
from kr_ai_trader.data.prices import PriceSummary


@pytest.fixture
def research_settings() -> Settings:
    """DART/뉴스 모두 켜고 키 주입한 설정."""
    return Settings(
        enable_dart=True,
        dart_api_key="dummy-key",
        dart_lookback_days=14,
        enable_news=True,
        news_lookback_items=8,
    )


def _price() -> PriceSummary:
    return PriceSummary(
        ticker="005930",
        last_close=70000.0,
        pct_change_1d=1.5,
        pct_change_5d=3.0,
        pct_change_20d=-2.0,
        sma_5=69000.0,
        sma_20=68000.0,
        rsi_14=55.0,
        volume=1000000,
        as_of=date(2026, 6, 1),
    )


def _fundamentals() -> FundamentalSummary:
    return FundamentalSummary(
        ticker="005930",
        per=12.3,
        pbr=1.4,
        eps=5600.0,
        bps=50000.0,
        div_yield=2.1,
        dps=1500.0,
        as_of=date(2026, 6, 1),
    )


def _disclosures() -> list[Disclosure]:
    return [
        Disclosure(
            rcept_dt="20260530",
            report_nm="주요사항보고서(유상증자결정)",
            corp_name="삼성전자",
            rcept_no="20260530000001",
            url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260530000001",
        )
    ]


def _news() -> list[NewsItem]:
    return [
        NewsItem(
            title="삼성전자 2분기 실적 호조 전망",
            link="https://news.example.com/1",
            published="Mon, 01 Jun 2026 09:00:00 GMT",
            source="연합뉴스",
        )
    ]


def _patch_all(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fundamentals: FundamentalSummary,
    disclosures: list[Disclosure],
    news: list[NewsItem],
    sector: str | None,
    name: str | None,
) -> None:
    monkeypatch.setattr(research_mod, "compute_features", lambda ticker, **kw: _price())
    monkeypatch.setattr(research_mod, "get_fundamentals", lambda ticker, **kw: fundamentals)
    monkeypatch.setattr(research_mod, "get_disclosures", lambda ticker, **kw: disclosures)
    monkeypatch.setattr(research_mod, "get_news", lambda query, **kw: news)
    monkeypatch.setattr(
        research_mod,
        "build_sector_map",
        lambda tickers, **kw: {"005930": sector} if sector else {},
    )
    monkeypatch.setattr(research_mod, "_resolve_company_name", lambda ticker: name)


def test_build_research_context_happy_path(
    monkeypatch: pytest.MonkeyPatch, research_settings: Settings
) -> None:
    _patch_all(
        monkeypatch,
        fundamentals=_fundamentals(),
        disclosures=_disclosures(),
        news=_news(),
        sector="전기전자",
        name="삼성전자",
    )

    rc = research_mod.build_research_context("005930", settings=research_settings)

    assert rc.ticker == "005930"
    assert rc.company_name == "삼성전자"
    assert rc.sector == "전기전자"
    assert rc.fundamentals.per == 12.3
    assert len(rc.disclosures) == 1
    assert len(rc.news) == 1

    ctx = research_mod.research_to_context_string(rc)
    parsed = json.loads(ctx)
    # 기술적 + 펀더멘털 + 공시 + 뉴스 + 섹터 모두 포함
    assert parsed["technical"]["rsi_14"] == 55.0
    assert parsed["fundamentals"]["per"] == 12.3
    assert parsed["disclosures"][0]["report_nm"].startswith("주요사항보고서")
    assert "삼성전자 2분기 실적 호조 전망" in parsed["news"]
    assert parsed["sector"] == "전기전자"
    # 모더레이터의 4000자 클램프 아래 유지
    assert len(ctx) < 4000


def test_build_research_context_dart_off_when_no_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DART 키 없으면 get_disclosures 미호출 — 빈 공시."""
    called = {"dart": False}

    def _fail_dart(ticker: str, **kw: object) -> list[Disclosure]:
        called["dart"] = True
        return _disclosures()

    monkeypatch.setattr(research_mod, "compute_features", lambda ticker, **kw: _price())
    monkeypatch.setattr(research_mod, "get_fundamentals", lambda ticker, **kw: _fundamentals())
    monkeypatch.setattr(research_mod, "get_disclosures", _fail_dart)
    monkeypatch.setattr(research_mod, "get_news", lambda query, **kw: [])
    monkeypatch.setattr(research_mod, "build_sector_map", lambda tickers, **kw: {})
    monkeypatch.setattr(research_mod, "_resolve_company_name", lambda ticker: "삼성전자")

    settings = Settings(enable_dart=True, dart_api_key=None, enable_news=False)
    rc = research_mod.build_research_context("005930", settings=settings)

    assert called["dart"] is False
    assert rc.disclosures == []


def test_build_research_context_fully_degraded(
    monkeypatch: pytest.MonkeyPatch, research_settings: Settings
) -> None:
    """모든 보조 소스가 비어도 유효한 컨텍스트 문자열을 만든다."""
    empty_fund = FundamentalSummary(
        ticker="005930",
        per=None,
        pbr=None,
        eps=None,
        bps=None,
        div_yield=None,
        dps=None,
        as_of=None,
    )
    _patch_all(
        monkeypatch,
        fundamentals=empty_fund,
        disclosures=[],
        news=[],
        sector=None,
        name=None,
    )

    rc = research_mod.build_research_context("005930", settings=research_settings)

    assert rc.company_name is None
    assert rc.sector is None
    assert rc.disclosures == []
    assert rc.news == []

    ctx = research_mod.research_to_context_string(rc)
    parsed = json.loads(ctx)
    assert parsed["ticker"] == "005930"
    assert parsed["fundamentals"]["per"] is None
    assert parsed["disclosures"] == []
    assert parsed["news"] == []
    assert parsed["sector"] is None


def test_news_skipped_when_no_company_name(
    monkeypatch: pytest.MonkeyPatch, research_settings: Settings
) -> None:
    """회사명 미해결 시 get_news 미호출 (뉴스 query 가 빈 문자열이 되는 것 방지)."""
    called = {"news": False}

    def _fail_news(query: str, **kw: object) -> list[NewsItem]:
        called["news"] = True
        return _news()

    monkeypatch.setattr(research_mod, "compute_features", lambda ticker, **kw: _price())
    monkeypatch.setattr(research_mod, "get_fundamentals", lambda ticker, **kw: _fundamentals())
    monkeypatch.setattr(research_mod, "get_disclosures", lambda ticker, **kw: [])
    monkeypatch.setattr(research_mod, "get_news", _fail_news)
    monkeypatch.setattr(research_mod, "build_sector_map", lambda tickers, **kw: {})
    monkeypatch.setattr(research_mod, "_resolve_company_name", lambda ticker: None)

    rc = research_mod.build_research_context("005930", settings=research_settings)

    assert called["news"] is False
    assert rc.news == []
