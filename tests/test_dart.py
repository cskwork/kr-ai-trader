"""DART 공시 수집 테스트. 실제 네트워크 없음 — httpx.get 을 monkeypatch."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from kr_ai_trader.data import dart


def _corp_zip(stock_code: str = "005930", corp_code: str = "00126380") -> bytes:
    """CORPCODE.xml 1개를 담은 ZIP 바이트 생성."""
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<result>"
        f"<list><corp_code>{corp_code}</corp_code>"
        f"<corp_name>삼성전자</corp_name><stock_code>{stock_code}</stock_code>"
        "<modify_date>20230101</modify_date></list>"
        # 비상장사 — stock_code 빈 값 → 매핑 제외 대상
        "<list><corp_code>99999999</corp_code><corp_name>비상장</corp_name>"
        "<stock_code> </stock_code><modify_date>20230101</modify_date></list>"
        "</result>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("CORPCODE.xml", xml.encode("utf-8"))
    return buf.getvalue()


class _FakeResponse:
    def __init__(self, *, json_data: object = None, content: bytes = b"") -> None:
        self._json = json_data
        self.content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._json


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """corp_map 캐시를 tmp_path 로 격리 — 테스트 간 오염 방지."""
    cache_dir = tmp_path / "dart"
    monkeypatch.setattr(dart, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(dart, "CORP_MAP_FILE", cache_dir / "corp_map.json")


def _patch_httpx(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    monkeypatch.setattr(dart.httpx, "get", handler)


def test_disabled_when_no_api_key() -> None:
    assert dart.get_disclosures("005930", api_key=None) == []
    assert dart.get_disclosures("005930", api_key="") == []


def test_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(url: str, *, params: dict, timeout: float) -> _FakeResponse:
        if url == dart.CORP_CODE_URL:
            return _FakeResponse(content=_corp_zip())
        assert url == dart.LIST_URL
        assert params["corp_code"] == "00126380"
        return _FakeResponse(
            json_data={
                "status": "000",
                "message": "정상",
                "list": [
                    {
                        "rcept_dt": "20260601",
                        "report_nm": "분기보고서",
                        "corp_name": "삼성전자",
                        "rcept_no": "20260601000123",
                    }
                ],
            }
        )

    _patch_httpx(monkeypatch, handler)
    items = dart.get_disclosures("005930", api_key="KEY", max_items=5)

    assert len(items) == 1
    d = items[0]
    assert d.report_nm == "분기보고서"
    assert d.rcept_no == "20260601000123"
    assert d.url == f"{dart.VIEWER_URL}?rcpNo=20260601000123"

    # 캐시 기록 확인 + 비상장사 제외 확인
    mapping = json.loads(dart.CORP_MAP_FILE.read_text(encoding="utf-8"))
    assert mapping == {"005930": "00126380"}


def test_corp_map_cache_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    """캐시가 있으면 corpCode.xml 을 재다운로드하지 않는다."""
    dart.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dart.CORP_MAP_FILE.write_text(json.dumps({"005930": "00126380"}), encoding="utf-8")
    calls: list[str] = []

    def handler(url: str, *, params: dict, timeout: float) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse(json_data={"status": "000", "list": []})

    _patch_httpx(monkeypatch, handler)
    dart.get_disclosures("005930", api_key="KEY")
    assert dart.CORP_CODE_URL not in calls


def test_empty_when_status_no_data(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(url: str, *, params: dict, timeout: float) -> _FakeResponse:
        if url == dart.CORP_CODE_URL:
            return _FakeResponse(content=_corp_zip())
        return _FakeResponse(json_data={"status": "013", "message": "조회된 데이타가 없습니다."})

    _patch_httpx(monkeypatch, handler)
    assert dart.get_disclosures("005930", api_key="KEY") == []


def test_empty_when_corp_code_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(url: str, *, params: dict, timeout: float) -> _FakeResponse:
        return _FakeResponse(content=_corp_zip(stock_code="005930"))

    _patch_httpx(monkeypatch, handler)
    # 매핑에 없는 종목 → 빈 리스트
    assert dart.get_disclosures("999999", api_key="KEY") == []


def test_graceful_degrade_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(url: str, *, params: dict, timeout: float) -> _FakeResponse:
        raise RuntimeError("network down")

    _patch_httpx(monkeypatch, handler)
    assert dart.get_disclosures("005930", api_key="KEY") == []


def test_disclosures_to_list() -> None:
    item = dart.Disclosure(
        rcept_dt="20260601",
        report_nm="주요사항보고서",
        corp_name="삼성전자",
        rcept_no="20260601000999",
        url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260601000999",
    )
    out = dart.disclosures_to_list([item])
    assert out == [
        {
            "rcept_dt": "20260601",
            "report_nm": "주요사항보고서",
            "corp_name": "삼성전자",
            "rcept_no": "20260601000999",
            "url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260601000999",
        }
    ]
