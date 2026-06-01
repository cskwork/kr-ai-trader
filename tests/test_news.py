"""종목별 뉴스 헤드라인 수집 — Google News RSS."""

from __future__ import annotations

import httpx
import pytest
import respx

from kr_ai_trader.data.news import (
    GOOGLE_NEWS_RSS,
    NewsItem,
    get_news,
    news_to_list,
)

_RSS_WITH_SOURCE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>news</title>
    <item>
      <title>삼성전자 신고가 경신</title>
      <link>https://news.example/1</link>
      <pubDate>Mon, 02 Jun 2026 09:00:00 GMT</pubDate>
      <source url="https://a.co">한국경제</source>
    </item>
    <item>
      <title>삼성전자 실적 발표</title>
      <link>https://news.example/2</link>
      <pubDate>Mon, 02 Jun 2026 08:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>"""

_RSS_EMPTY = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>news</title></channel></rss>"""


@respx.mock
def test_get_news_happy_path() -> None:
    respx.get(url__startswith=GOOGLE_NEWS_RSS).mock(
        return_value=httpx.Response(200, content=_RSS_WITH_SOURCE.encode())
    )
    items = get_news("삼성전자")
    assert len(items) == 2
    assert items[0] == NewsItem(
        title="삼성전자 신고가 경신",
        link="https://news.example/1",
        published="Mon, 02 Jun 2026 09:00:00 GMT",
        source="한국경제",
    )
    # <source> 없는 두 번째 item 은 빈 문자열로 degrade.
    assert items[1].source == ""


@respx.mock
def test_get_news_respects_max_items() -> None:
    respx.get(url__startswith=GOOGLE_NEWS_RSS).mock(
        return_value=httpx.Response(200, content=_RSS_WITH_SOURCE.encode())
    )
    items = get_news("삼성전자", max_items=1)
    assert len(items) == 1
    assert items[0].title == "삼성전자 신고가 경신"


@respx.mock
def test_get_news_empty_feed_returns_empty_list() -> None:
    respx.get(url__startswith=GOOGLE_NEWS_RSS).mock(
        return_value=httpx.Response(200, content=_RSS_EMPTY.encode())
    )
    assert get_news("없는회사") == []


def test_get_news_blank_query_short_circuits() -> None:
    # 네트워크 호출 없이 빈 리스트 (respx 미가동이어도 통과해야 함).
    assert get_news("   ") == []


@respx.mock
def test_get_news_http_error_degrades_gracefully() -> None:
    respx.get(url__startswith=GOOGLE_NEWS_RSS).mock(
        return_value=httpx.Response(503)
    )
    assert get_news("삼성전자") == []


@respx.mock
def test_get_news_network_exception_degrades_gracefully() -> None:
    respx.get(url__startswith=GOOGLE_NEWS_RSS).mock(
        side_effect=httpx.ConnectError("boom")
    )
    assert get_news("삼성전자") == []


@respx.mock
def test_get_news_malformed_xml_degrades_gracefully() -> None:
    respx.get(url__startswith=GOOGLE_NEWS_RSS).mock(
        return_value=httpx.Response(200, content=b"<rss><not closed")
    )
    assert get_news("삼성전자") == []


def test_news_to_list_serializes_fields() -> None:
    items = [
        NewsItem(title="t1", link="l1", published="p1", source="s1"),
        NewsItem(title="t2", link="l2", published="p2", source=""),
    ]
    assert news_to_list(items) == [
        {"title": "t1", "link": "l1", "published": "p1", "source": "s1"},
        {"title": "t2", "link": "l2", "published": "p2", "source": ""},
    ]


def test_news_to_list_empty() -> None:
    assert news_to_list([]) == []


@pytest.mark.parametrize(
    ("lang_region", "expect"),
    [("ko-KR", "ceid=KR%3Ako"), ("en-US", "ceid=US%3Aen")],
)
@respx.mock
def test_get_news_builds_lang_region_params(lang_region: str, expect: str) -> None:
    route = respx.get(url__startswith=GOOGLE_NEWS_RSS).mock(
        return_value=httpx.Response(200, content=_RSS_EMPTY.encode())
    )
    get_news("삼성전자", lang_region=lang_region)
    assert expect in str(route.calls.last.request.url)
