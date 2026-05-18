"""vectorbt 기반 백테스트 골격. 본격 전략은 strategies/ 에 분리 권장."""

from .runner import BacktestResult, run_backtest

__all__ = ["BacktestResult", "run_backtest"]
