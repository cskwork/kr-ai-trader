"""백테스트 러너 — vectorbt 모킹. 실제 vectorbt 의존/계산 없음.

vectorbt 는 옵셔널 의존성이라 CI 환경에 없을 수 있다. runner.py 가 함수 내부에서
`import vectorbt as vbt` 하므로, import 가 실행되기 전에 sys.modules 에 가짜 모듈을
주입한다(test_fundamentals 의 pykrx 주입 기법과 동일). 검증 포인트:
  - commission/tax/slippage 가 vbt.Portfolio.from_signals 로 정확히 전달되는가
  - 반환 BacktestResult 의 shape/값이 stats 매핑과 일치하는가
  - 빈 시그널(거래 0건) 경로도 안전하게 처리되는가
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from kr_ai_trader.backtest.runner import BacktestResult, run_backtest


class _FakePortfolio:
    """vbt.Portfolio.from_signals 가 반환하는 객체 대역.

    생성 시 받은 kwargs 를 그대로 보관해, 러너가 넘긴 fees/slippage 등을 검증한다.
    stats() 는 pandas Series 를 돌려준다(러너가 .to_csv / 키 인덱싱을 호출하므로).
    """

    def __init__(self, stats: pd.Series) -> None:
        self._stats = stats

    def stats(self) -> pd.Series:
        return self._stats


def _install_fake_vectorbt(
    monkeypatch: pytest.MonkeyPatch, stats: pd.Series
) -> dict[str, Any]:
    """`import vectorbt as vbt` 가 반환할 가짜 모듈 주입. 캡처된 kwargs dict 반환."""
    captured: dict[str, Any] = {}

    def _from_signals(**kwargs: Any) -> _FakePortfolio:
        captured.clear()
        captured.update(kwargs)
        return _FakePortfolio(stats)

    portfolio_cls = types.SimpleNamespace(from_signals=_from_signals)
    vbt_mod = types.ModuleType("vectorbt")
    vbt_mod.Portfolio = portfolio_cls  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "vectorbt", vbt_mod)
    return captured


def _signal_frame(values: list[list[bool]], dates: list[str]) -> pd.DataFrame:
    """bool 시그널 DataFrame (index=date, columns=ticker)."""
    return pd.DataFrame(
        values, index=pd.to_datetime(dates), columns=["005930", "000660"]
    )


def _full_stats() -> pd.Series:
    """거래가 있는 정상 stats 시리즈."""
    return pd.Series(
        {
            "Total Return [%]": 12.5,
            "Sharpe Ratio": 1.8,
            "Max Drawdown [%]": -7.3,
            "Win Rate [%]": 55.0,
            "Total Trades": 4,
        }
    )


def test_passes_fees_slippage_and_returns_result_shape(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """해피 패스: 수수료+거래세 합산이 fees 로, 슬리피지가 slippage 로 전달되고
    BacktestResult 값이 stats 매핑과 일치한다."""
    captured = _install_fake_vectorbt(monkeypatch, _full_stats())
    prices = _signal_frame([[True, False], [False, True]], ["2026-05-28", "2026-05-29"])
    entries = _signal_frame([[True, False], [False, True]], ["2026-05-28", "2026-05-29"])
    exits = _signal_frame([[False, True], [True, False]], ["2026-05-28", "2026-05-29"])

    result = run_backtest(
        prices=prices,
        entries=entries,
        exits=exits,
        initial_cash=5_000_000.0,
        fee_pct=0.00015,
        tax_pct=0.0018,
        slippage_pct=0.0005,
        output_dir=tmp_path / "out",
    )

    # commission(0.00015) + tax(0.0018) 가 합쳐 fees 로 전달돼야 함
    assert captured["fees"] == pytest.approx(0.00195)
    assert captured["slippage"] == pytest.approx(0.0005)
    assert captured["init_cash"] == 5_000_000.0
    assert captured["freq"] == "1D"
    # close/entries/exits 가 그대로 전달됐는지 (동일 객체)
    assert captured["close"] is prices
    assert captured["entries"] is entries
    assert captured["exits"] is exits

    assert isinstance(result, BacktestResult)
    assert result.total_return_pct == 12.5
    assert result.sharpe == 1.8
    assert result.max_drawdown_pct == -7.3
    assert result.win_rate == 55.0
    assert result.n_trades == 4
    assert result.output_dir == tmp_path / "out"


def test_default_fees_combine_commission_and_tax(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """디폴트 인자만 줘도 fees=0.00015+0.0018, slippage=5bp 로 전달."""
    captured = _install_fake_vectorbt(monkeypatch, _full_stats())
    prices = _signal_frame([[True, False]], ["2026-05-29"])
    entries = _signal_frame([[True, False]], ["2026-05-29"])
    exits = _signal_frame([[False, True]], ["2026-05-29"])

    run_backtest(
        prices=prices, entries=entries, exits=exits, output_dir=tmp_path / "d"
    )

    assert captured["fees"] == pytest.approx(0.00195)
    assert captured["slippage"] == pytest.approx(0.0005)
    assert captured["init_cash"] == 10_000_000.0


def test_writes_stats_csv_to_output_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """stats CSV 가 output_dir 에 기록되고 디렉토리가 생성된다."""
    _install_fake_vectorbt(monkeypatch, _full_stats())
    out = tmp_path / "results"
    prices = _signal_frame([[True, False]], ["2026-05-29"])

    result = run_backtest(
        prices=prices, entries=prices, exits=prices, output_dir=out
    )

    assert out.is_dir()
    written = list(out.glob("*_stats.csv"))
    assert len(written) == 1
    assert result.output_dir == out


def test_empty_signals_zero_trades_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """빈/거래 0건 경로: Win Rate/Total Trades 키가 없어도 .get 디폴트로 안전.

    vectorbt 는 거래가 전혀 없으면 Sharpe/Win Rate/Total Trades 키를 누락하기도 한다.
    러너가 stats.get(..., 0.0) 으로 방어하는지 검증."""
    empty_stats = pd.Series(
        {
            "Total Return [%]": 0.0,
            "Max Drawdown [%]": 0.0,
            # Sharpe Ratio / Win Rate [%] / Total Trades 누락
        }
    )
    _install_fake_vectorbt(monkeypatch, empty_stats)
    # 모두 False 인 시그널 (진입/청산 없음)
    prices = _signal_frame([[False, False], [False, False]], ["2026-05-28", "2026-05-29"])

    result = run_backtest(
        prices=prices, entries=prices, exits=prices, output_dir=tmp_path / "empty"
    )

    assert result.total_return_pct == 0.0
    assert result.max_drawdown_pct == 0.0
    assert result.sharpe == 0.0       # .get fallback
    assert result.win_rate == 0.0     # .get fallback
    assert result.n_trades == 0       # .get fallback


def test_missing_required_stat_key_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """malformed stats: 필수 키(Total Return [%]) 누락 시 KeyError 전파.

    이 키는 .get 이 아니라 직접 인덱싱이라 누락되면 실패해야 정상(조용히 0 으로
    위장하지 않음)."""
    bad_stats = pd.Series({"Max Drawdown [%]": -1.0})
    _install_fake_vectorbt(monkeypatch, bad_stats)
    prices = _signal_frame([[True, False]], ["2026-05-29"])

    with pytest.raises(KeyError):
        run_backtest(
            prices=prices, entries=prices, exits=prices, output_dir=tmp_path / "bad"
        )
