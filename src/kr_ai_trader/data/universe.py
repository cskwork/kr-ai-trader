"""티커 화이트리스트 로드. LLM 환각 방지를 위해 모든 주문은 이 집합 안에서만 허용."""

from __future__ import annotations

from pathlib import Path

_FALLBACK_KOSPI = [
    "005930",  # 삼성전자
    "000660",  # SK하이닉스
    "207940",  # 삼성바이오로직스
    "373220",  # LG에너지솔루션
    "005380",  # 현대차
    "000270",  # 기아
    "035420",  # NAVER
    "035720",  # 카카오
    "068270",  # 셀트리온
    "051910",  # LG화학
]


def load_universe(name: str = "kospi200", file_path: Path | None = None) -> frozenset[str]:
    """유니버스 로드.

    - file_path 가 주어지면 줄당 하나의 티커 형식 텍스트 파일 사용
    - 아니면 pykrx 로 KOSPI200 / KOSDAQ150 동적 로드 시도
    - 모두 실패 시 fallback 10종목 (개발용)
    """
    if file_path and file_path.exists():
        return frozenset(
            line.strip()
            for line in file_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        )

    try:
        from pykrx import stock as krx_stock     # type: ignore[import-not-found]

        if name == "kospi200":
            tickers = krx_stock.get_index_portfolio_deposit_file("1028")
        elif name == "kosdaq150":
            tickers = krx_stock.get_index_portfolio_deposit_file("2203")
        else:
            tickers = []
        if tickers:
            return frozenset(tickers)
    except Exception:
        pass

    return frozenset(_FALLBACK_KOSPI)
