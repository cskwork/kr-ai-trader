"""티커 -> 업종(섹터) 매핑 — RiskGate 의 max_sector_pct 활성화용.

RiskGate 는 `sector_map: dict[str, str]` (ticker -> 섹터명) 로 섹터별 노출 한도를
강제한다. 이 모듈이 그 맵을 만든다.

전략:
- pykrx 의 KOSPI/KOSDAQ "업종" 지수를 열거 (`get_index_ticker_list`),
  각 지수의 이름(`get_index_ticker_name`)과 구성종목(`get_index_portfolio_deposit_file`)
  을 받아 ticker -> 섹터명 역매핑을 만든다.
- 한 번 만든 맵은 `cache/sector/map.json` 에 캐싱한다.
- pykrx 열거 실패 시 -> fallback 10종목 정적 맵으로 폴백한다. 절대 raise 하지 않는다.
"""

from __future__ import annotations

import contextlib
import json
from datetime import date
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

CACHE_FILE = Path("cache/sector/map.json")

# pykrx 업종지수가 존재하는 시장. 업종(섹터) 지수는 이 시장들 아래에 있다.
_SECTOR_MARKETS = ("KOSPI", "KOSDAQ")

# pykrx 열거 실패 시 사용하는 fallback 10종목 정적 맵 (data/universe.py 의 fallback-10).
_FALLBACK_SECTOR_MAP: dict[str, str] = {
    "005930": "전기전자",  # 삼성전자
    "000660": "전기전자",  # SK하이닉스
    "207940": "의약품",  # 삼성바이오로직스
    "373220": "전기전자",  # LG에너지솔루션
    "005380": "운수장비",  # 현대차
    "000270": "운수장비",  # 기아
    "035420": "서비스업",  # NAVER
    "035720": "서비스업",  # 카카오
    "068270": "의약품",  # 셀트리온
    "051910": "화학",  # LG화학
}


def _cache_path() -> Path:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    return CACHE_FILE


def _read_cache() -> dict[str, str] | None:
    """캐시된 전체 맵 로드. 없거나 깨졌으면 None."""
    if not CACHE_FILE.exists():
        return None
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    # 타입 방어: 문자열 키/값만 채택.
    return {str(k): str(v) for k, v in data.items()}


def _fetch_full_sector_map(as_of: date) -> dict[str, str]:
    """pykrx 로 ticker -> 섹터명 전체 맵을 만든다. 실패 시 빈 dict (raise 안 함)."""
    try:
        from pykrx import stock as krx_stock  # type: ignore[import-not-found]
    except Exception:  # pragma: no cover - 환경 의존
        log.warning("sector_map.pykrx_import_failed")
        return {}

    ymd = as_of.strftime("%Y%m%d")
    mapping: dict[str, str] = {}
    try:
        for market in _SECTOR_MARKETS:
            index_codes = krx_stock.get_index_ticker_list(ymd, market)
            for code in index_codes or []:
                sector_name = krx_stock.get_index_ticker_name(code)
                if not sector_name:
                    continue
                members = krx_stock.get_index_portfolio_deposit_file(code)
                for ticker in members or []:
                    # 먼저 매칭된 섹터를 유지 (한 종목이 여러 지수에 들 수 있음).
                    mapping.setdefault(str(ticker), str(sector_name))
    except Exception as exc:
        log.warning("sector_map.fetch_failed", error=str(exc))
        return {}

    if not mapping:
        log.warning("sector_map.fetch_empty")
    return mapping


def build_sector_map(
    tickers: frozenset[str] | set[str] | list[str],
    *,
    use_cache: bool = True,
    as_of: date | None = None,
) -> dict[str, str]:
    """요청한 티커들에 대한 ticker -> 섹터명 맵 반환.

    - 캐시 -> pykrx 열거 -> fallback 정적 맵 순으로 시도.
    - 요청 집합에 있는 티커만 결과에 포함.
    - 어떤 경우에도 dict 를 반환하며 예외를 던지지 않는다.
    """
    requested = {str(t) for t in tickers}
    if not requested:
        return {}

    full_map: dict[str, str] | None = None

    if use_cache:
        full_map = _read_cache()
        # 캐시에 요청 티커가 충분히 들어있지 않으면 갱신을 시도.
        if full_map is not None and not requested.issubset(full_map.keys()):
            full_map = None

    if full_map is None:
        full_map = _fetch_full_sector_map(as_of or date.today())
        if full_map and use_cache:
            with contextlib.suppress(Exception):
                _cache_path().write_text(
                    json.dumps(full_map, ensure_ascii=False, sort_keys=True),
                    encoding="utf-8",
                )

    result = {t: full_map[t] for t in requested if t in full_map}

    # pykrx/캐시에서 못 채운 티커는 fallback 정적 맵으로 보강.
    missing = requested - result.keys()
    for t in missing:
        if t in _FALLBACK_SECTOR_MAP:
            result[t] = _FALLBACK_SECTOR_MAP[t]

    if not result:
        log.warning("sector_map.empty_result", requested=len(requested))
    return result
