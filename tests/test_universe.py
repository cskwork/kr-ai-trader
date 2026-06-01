"""유니버스 로드 — pykrx 모킹. 실제 네트워크/pykrx 호출 없음."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from kr_ai_trader.data import universe as uni
from kr_ai_trader.data.universe import _FALLBACK_KOSPI, load_universe


def _install_fake_pykrx(monkeypatch: pytest.MonkeyPatch, fn: object) -> None:
    """`from pykrx import stock` 가 반환할 가짜 모듈 주입."""
    stock_mod = types.SimpleNamespace(get_index_portfolio_deposit_file=fn)
    pykrx_mod = types.ModuleType("pykrx")
    pykrx_mod.stock = stock_mod  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pykrx", pykrx_mod)


# --- file_path 경로 ---------------------------------------------------------


def test_file_path_reads_tickers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """파일이 있으면 줄당 티커를 읽고 주석/공백 줄은 무시. pykrx 호출 없음."""
    f = tmp_path / "u.txt"
    f.write_text("005930\n000660\n\n# 주석\n035420\n", encoding="utf-8")

    def must_not_call(*_a: object, **_k: object) -> list[str]:
        raise AssertionError("file_path 가 있으면 pykrx 를 호출하면 안 됨")

    _install_fake_pykrx(monkeypatch, must_not_call)
    result = load_universe(file_path=f)
    assert result == frozenset({"005930", "000660", "035420"})


def test_file_path_missing_falls_through_to_pykrx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """file_path 가 존재하지 않으면 pykrx 경로로 진입."""
    missing = tmp_path / "nope.txt"
    _install_fake_pykrx(monkeypatch, lambda _code: ["111111", "222222"])
    result = load_universe(name="kospi200", file_path=missing)
    assert result == frozenset({"111111", "222222"})


# --- pykrx 인덱스 경로 -------------------------------------------------------


def test_pykrx_kospi200_index_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """kospi200 -> 인덱스 코드 '1028' 로 조회."""
    captured: dict[str, str] = {}

    def _deposit(code: str) -> list[str]:
        captured["code"] = code
        return ["005930", "000660"]

    _install_fake_pykrx(monkeypatch, _deposit)
    result = load_universe(name="kospi200")
    assert captured["code"] == "1028"
    assert result == frozenset({"005930", "000660"})


def test_pykrx_kosdaq150_index_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """kosdaq150 -> 인덱스 코드 '2203' 로 조회."""
    captured: dict[str, str] = {}

    def _deposit(code: str) -> list[str]:
        captured["code"] = code
        return ["247540", "086520"]

    _install_fake_pykrx(monkeypatch, _deposit)
    result = load_universe(name="kosdaq150")
    assert captured["code"] == "2203"
    assert result == frozenset({"247540", "086520"})


def test_unknown_name_yields_empty_tickers_then_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """알 수 없는 이름 -> tickers=[] -> fallback 10종목."""

    def must_not_call(*_a: object, **_k: object) -> list[str]:
        raise AssertionError("알 수 없는 이름은 deposit 조회를 하면 안 됨")

    _install_fake_pykrx(monkeypatch, must_not_call)
    result = load_universe(name="weird")
    assert result == frozenset(_FALLBACK_KOSPI)


def test_pykrx_empty_result_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """pykrx 가 빈 리스트 반환 -> fallback 10종목."""
    _install_fake_pykrx(monkeypatch, lambda _code: [])
    result = load_universe(name="kospi200")
    assert result == frozenset(_FALLBACK_KOSPI)


# --- fallback (graceful degrade) -------------------------------------------


def test_fallback_when_pykrx_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """pykrx 호출이 예외를 던지면 fallback 10종목으로 graceful degrade."""

    def boom(*_a: object, **_k: object) -> list[str]:
        raise RuntimeError("pykrx down")

    _install_fake_pykrx(monkeypatch, boom)
    result = load_universe(name="kospi200")
    assert result == frozenset(_FALLBACK_KOSPI)
    assert len(result) == 10


def test_fallback_contains_known_blue_chips() -> None:
    """fallback 집합은 삼성전자/SK하이닉스 등 대표 종목 10개를 포함."""
    assert "005930" in _FALLBACK_KOSPI
    assert "000660" in _FALLBACK_KOSPI
    assert len(_FALLBACK_KOSPI) == 10
    assert uni.load_universe is load_universe
