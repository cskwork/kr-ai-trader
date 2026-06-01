"""sector_map 빌더 테스트 — 실제 pykrx/네트워크 호출 없음, 전부 monkeypatch."""

from __future__ import annotations

import json
import sys
import types
from datetime import date
from pathlib import Path

import pytest

from kr_ai_trader.data import sector_map


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """캐시 파일 경로를 tmp 로 격리해 테스트 간 오염 방지."""
    cache_file = tmp_path / "sector" / "map.json"
    monkeypatch.setattr(sector_map, "CACHE_FILE", cache_file)


def _fake_pykrx(
    *,
    index_codes: dict[str, list[str]],
    names: dict[str, str],
    members: dict[str, list[str]],
    raise_on: str | None = None,
) -> types.ModuleType:
    """가짜 pykrx.stock 모듈 생성. raise_on 으로 특정 호출에서 예외 유발."""
    stock = types.ModuleType("pykrx.stock")

    def get_index_ticker_list(ymd: str, market: str) -> list[str]:
        if raise_on == "list":
            raise RuntimeError("boom")
        return index_codes.get(market, [])

    def get_index_ticker_name(code: str) -> str:
        if raise_on == "name":
            raise RuntimeError("boom")
        return names.get(code, "")

    def get_index_portfolio_deposit_file(code: str) -> list[str]:
        if raise_on == "members":
            raise RuntimeError("boom")
        return members.get(code, [])

    stock.get_index_ticker_list = get_index_ticker_list  # type: ignore[attr-defined]
    stock.get_index_ticker_name = get_index_ticker_name  # type: ignore[attr-defined]
    stock.get_index_portfolio_deposit_file = (  # type: ignore[attr-defined]
        get_index_portfolio_deposit_file
    )

    pkg = types.ModuleType("pykrx")
    pkg.stock = stock  # type: ignore[attr-defined]
    return pkg


def _install_pykrx(monkeypatch: pytest.MonkeyPatch, pkg: types.ModuleType) -> None:
    monkeypatch.setitem(sys.modules, "pykrx", pkg)
    monkeypatch.setitem(sys.modules, "pykrx.stock", pkg.stock)  # type: ignore[attr-defined]


def test_happy_path_builds_from_pykrx(monkeypatch: pytest.MonkeyPatch) -> None:
    pkg = _fake_pykrx(
        index_codes={"KOSPI": ["1015", "1080"], "KOSDAQ": []},
        names={"1015": "전기전자", "1080": "화학"},
        members={"1015": ["005930", "000660"], "1080": ["051910"]},
    )
    _install_pykrx(monkeypatch, pkg)

    result = sector_map.build_sector_map(
        {"005930", "000660", "051910"}, as_of=date(2026, 6, 1)
    )

    assert result == {
        "005930": "전기전자",
        "000660": "전기전자",
        "051910": "화학",
    }


def test_only_requested_tickers_included(monkeypatch: pytest.MonkeyPatch) -> None:
    pkg = _fake_pykrx(
        index_codes={"KOSPI": ["1015"], "KOSDAQ": []},
        names={"1015": "전기전자"},
        members={"1015": ["005930", "000660", "999999"]},
    )
    _install_pykrx(monkeypatch, pkg)

    result = sector_map.build_sector_map({"005930"}, as_of=date(2026, 6, 1))

    assert result == {"005930": "전기전자"}


def test_result_is_cached_and_reused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pkg = _fake_pykrx(
        index_codes={"KOSPI": ["1015"], "KOSDAQ": []},
        names={"1015": "전기전자"},
        members={"1015": ["005930"]},
    )
    _install_pykrx(monkeypatch, pkg)

    sector_map.build_sector_map({"005930"}, as_of=date(2026, 6, 1))
    cached = json.loads(sector_map.CACHE_FILE.read_text(encoding="utf-8"))
    assert cached["005930"] == "전기전자"

    # pykrx 제거 후에도 캐시로 동작해야 함.
    monkeypatch.delitem(sys.modules, "pykrx", raising=False)
    result = sector_map.build_sector_map({"005930"}, as_of=date(2026, 6, 1))
    assert result == {"005930": "전기전자"}


def test_empty_request_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    result = sector_map.build_sector_map(frozenset())
    assert result == {}


def test_empty_pykrx_data_falls_back_to_static(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pykrx 가 빈 데이터를 주면 fallback 정적 맵으로 보강."""
    pkg = _fake_pykrx(index_codes={}, names={}, members={})
    _install_pykrx(monkeypatch, pkg)

    result = sector_map.build_sector_map({"005930", "051910"})

    assert result == {"005930": "전기전자", "051910": "화학"}


def test_pykrx_exception_degrades_to_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pykrx 호출이 예외를 던져도 raise 하지 않고 fallback 으로 degrade."""
    pkg = _fake_pykrx(
        index_codes={"KOSPI": ["1015"]},
        names={"1015": "전기전자"},
        members={},
        raise_on="members",
    )
    _install_pykrx(monkeypatch, pkg)

    result = sector_map.build_sector_map({"005930", "000270"})

    assert result == {"005930": "전기전자", "000270": "운수장비"}


def test_unknown_ticker_not_in_fallback_is_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pkg = _fake_pykrx(index_codes={}, names={}, members={})
    _install_pykrx(monkeypatch, pkg)

    result = sector_map.build_sector_map({"999999"})
    assert result == {}


def test_pykrx_import_failure_uses_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pykrx 자체가 import 불가여도 fallback 으로 degrade."""
    monkeypatch.delitem(sys.modules, "pykrx", raising=False)
    monkeypatch.delitem(sys.modules, "pykrx.stock", raising=False)

    class _BlockImport:
        def find_spec(self, name: str, *args: object, **kwargs: object) -> None:
            if name == "pykrx":
                raise ImportError("blocked")
            return None

    monkeypatch.setattr(sys, "meta_path", [_BlockImport(), *sys.meta_path])

    result = sector_map.build_sector_map({"068270"})
    assert result == {"068270": "의약품"}
