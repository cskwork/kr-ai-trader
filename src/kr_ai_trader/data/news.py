"""종목별 최근 한국어 뉴스 헤드라인 — API 키 불필요.

- 1차 소스: Google News RSS 검색 (https://news.google.com/rss/search)
- httpx 동기 클라이언트로 받아 stdlib `xml.etree.ElementTree` 로 파싱 (feedparser 미사용).
- `query` 는 한국어 회사명 (티커->이름 변환은 호출측 책임).

GRACEFUL DEGRADE: 네트워크/파싱 오류는 전부 잡아 빈 리스트 반환 + log.warning.
거래 사이클이 죽은 데이터 소스에도 살아남도록 절대 예외를 호출측으로 던지지 않는다.
"""

from __future__ import annotations

import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import httpx
import structlog

log = structlog.get_logger(__name__)

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"


@dataclass(frozen=True)
class NewsItem:
    """뉴스 헤드라인 한 건."""

    title: str
    link: str
    published: str
    source: str


def _build_url(query: str, lang_region: str) -> str:
    """검색 URL 조립. lang_region(예: 'ko-KR') -> hl/gl/ceid 파라미터."""
    lang, _, region = lang_region.partition("-")
    lang = lang or "ko"
    region = region or "KR"
    params = {
        "q": query,
        "hl": lang,
        "gl": region,
        "ceid": f"{region}:{lang}",
    }
    return f"{GOOGLE_NEWS_RSS}?{urllib.parse.urlencode(params)}"


def _text(elem: ET.Element | None) -> str:
    """엘리먼트 텍스트 안전 추출 (None -> 빈 문자열)."""
    if elem is None or elem.text is None:
        return ""
    return elem.text.strip()


def _parse_item(item: ET.Element) -> NewsItem:
    """RSS <item> 한 건을 NewsItem 으로 변환. <source> 없으면 빈 문자열."""
    source_elem = item.find("source")
    # <source> 의 텍스트가 매체명. 없으면 빈 문자열로 graceful.
    source = _text(source_elem) if source_elem is not None else ""
    return NewsItem(
        title=_text(item.find("title")),
        link=_text(item.find("link")),
        published=_text(item.find("pubDate")),
        source=source,
    )


def get_news(
    query: str,
    *,
    max_items: int = 8,
    timeout: float = 10.0,
    lang_region: str = "ko-KR",
) -> list[NewsItem]:
    """Google News RSS 검색으로 최근 헤드라인 조회.

    `query` 는 한국어 회사명. 네트워크/파싱 실패 시 빈 리스트 반환.
    """
    if not query.strip():
        return []

    url = _build_url(query, lang_region)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
    except Exception as exc:  # 네트워크/HTTP/XML 파싱 전부 graceful degrade
        log.warning("news_fetch_failed", query=query, error=str(exc))
        return []

    # RSS 구조: <rss><channel><item>...</item></channel></rss>
    items = root.findall(".//item")
    parsed: list[NewsItem] = []
    for item in items[:max_items]:
        try:
            parsed.append(_parse_item(item))
        except Exception as exc:  # 개별 item 파싱 실패는 건너뜀
            log.warning("news_item_parse_failed", query=query, error=str(exc))
    return parsed


def news_to_list(items: list[NewsItem]) -> list[dict[str, object]]:
    """NewsItem 리스트를 직렬화용 dict 리스트로 변환."""
    return [
        {
            "title": item.title,
            "link": item.link,
            "published": item.published,
            "source": item.source,
        }
        for item in items
    ]
