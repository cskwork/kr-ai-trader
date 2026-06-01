"""DART(전자공시) 최근 공시 수집 — OpenDART REST.

- `get_disclosures` : 종목(stock code)의 최근 N일 공시 목록
- OpenDART 는 종목코드가 아니라 8자리 `corp_code` 로 조회한다.
  → `corpCode.xml`(ZIP→CORPCODE.xml) 을 1회 내려받아 stock_code→corp_code
    매핑을 `cache/dart/corp_map.json` 에 캐시하고 이후 재사용.

비활성/오류 시 빈 리스트로 우아하게 degrade — 매매 사이클을 절대 멈추지 않는다.
"""

from __future__ import annotations

import contextlib
import io
import json
import zipfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET  # nosec B405 — DART 신뢰 소스, 파싱 전용

import httpx
import structlog

log = structlog.get_logger(__name__)

CACHE_DIR = Path("cache/dart")
CORP_MAP_FILE = CACHE_DIR / "corp_map.json"

LIST_URL = "https://opendart.fss.or.kr/api/list.json"
CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
VIEWER_URL = "https://dart.fss.or.kr/dsaf001/main.do"


@dataclass(frozen=True)
class Disclosure:
    """공시 1건. LLM 컨텍스트/알람용."""

    rcept_dt: str  # 접수일자 YYYYMMDD
    report_nm: str  # 보고서명
    corp_name: str  # 회사명
    rcept_no: str  # 접수번호 (뷰어 URL 키)
    url: str  # 원문 뷰어 URL


def disclosures_to_list(items: list[Disclosure]) -> list[dict[str, object]]:
    """Disclosure 리스트를 직렬화 가능한 dict 리스트로."""
    return [
        {
            "rcept_dt": d.rcept_dt,
            "report_nm": d.report_nm,
            "corp_name": d.corp_name,
            "rcept_no": d.rcept_no,
            "url": d.url,
        }
        for d in items
    ]


def _load_cached_corp_map() -> dict[str, str] | None:
    """캐시된 stock_code→corp_code 매핑 로드. 없거나 손상 시 None."""
    if not CORP_MAP_FILE.exists():
        return None
    with contextlib.suppress(Exception):
        data = json.loads(CORP_MAP_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data:
            return {str(k): str(v) for k, v in data.items()}
    return None


def _parse_corp_map(xml_bytes: bytes) -> dict[str, str]:
    """CORPCODE.xml 바이트에서 stock_code(6자리)→corp_code(8자리) 매핑 추출."""
    root = ET.fromstring(xml_bytes)  # nosec B314 — DART 발급 신뢰 파일
    mapping: dict[str, str] = {}
    for item in root.iter("list"):
        stock_el = item.find("stock_code")
        corp_el = item.find("corp_code")
        if stock_el is None or corp_el is None:
            continue
        stock_code = (stock_el.text or "").strip()
        corp_code = (corp_el.text or "").strip()
        # 비상장사는 stock_code 가 빈 값 → 스킵
        if stock_code and corp_code:
            mapping[stock_code] = corp_code
    return mapping


def _fetch_corp_map(api_key: str, *, timeout: float) -> dict[str, str]:
    """OpenDART corpCode.xml(ZIP) 다운로드 → 파싱 → 캐시 후 반환."""
    resp = httpx.get(CORP_CODE_URL, params={"crtfc_key": api_key}, timeout=timeout)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        # ZIP 내 CORPCODE.xml 1개만 존재
        xml_bytes = zf.read("CORPCODE.xml")
    mapping = _parse_corp_map(xml_bytes)
    with contextlib.suppress(Exception):
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        CORP_MAP_FILE.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
    return mapping


def _resolve_corp_code(ticker: str, api_key: str, *, timeout: float) -> str | None:
    """종목코드 → corp_code. 캐시 우선, 미스 시 1회 다운로드."""
    mapping = _load_cached_corp_map()
    if mapping is None:
        mapping = _fetch_corp_map(api_key, timeout=timeout)
    return mapping.get(ticker)


def _to_disclosure(row: dict[str, object]) -> Disclosure:
    """list.json 의 한 row(dict)를 Disclosure 로 변환."""
    rcept_no = str(row.get("rcept_no", ""))
    return Disclosure(
        rcept_dt=str(row.get("rcept_dt", "")),
        report_nm=str(row.get("report_nm", "")),
        corp_name=str(row.get("corp_name", "")),
        rcept_no=rcept_no,
        url=f"{VIEWER_URL}?rcpNo={rcept_no}",
    )


def get_disclosures(
    ticker: str,
    *,
    api_key: str | None,
    lookback_days: int = 14,
    max_items: int = 10,
    timeout: float = 10.0,
) -> list[Disclosure]:
    """종목의 최근 `lookback_days` 일 공시를 최신순 최대 `max_items` 건 반환.

    api_key 미설정 시 기능 off(빈 리스트). 네트워크/파싱 오류도 빈 리스트로 degrade.
    """
    if not api_key:
        log.info("dart.disabled", reason="no_api_key", ticker=ticker)
        return []

    try:
        corp_code = _resolve_corp_code(ticker, api_key, timeout=timeout)
        if not corp_code:
            log.warning("dart.corp_code_not_found", ticker=ticker)
            return []

        end = date.today()
        bgn = end - timedelta(days=lookback_days)
        params = {
            "crtfc_key": api_key,
            "corp_code": corp_code,
            "bgn_de": bgn.strftime("%Y%m%d"),
            "end_de": end.strftime("%Y%m%d"),
            "page_count": str(max(max_items, 1)),
        }
        resp = httpx.get(LIST_URL, params=params, timeout=timeout)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:  # 외부 소스 우아한 degrade
        log.warning("dart.fetch_failed", ticker=ticker, error=str(exc))
        return []

    # status "000" = 정상. "013" = 데이터 없음(정상 빈 응답).
    status = str(payload.get("status", ""))
    if status != "000":
        if status != "013":
            log.warning("dart.api_status", ticker=ticker, status=status, msg=payload.get("message"))
        return []

    rows = payload.get("list", [])
    if not isinstance(rows, list):
        return []
    return [_to_disclosure(r) for r in rows[:max_items] if isinstance(r, dict)]
