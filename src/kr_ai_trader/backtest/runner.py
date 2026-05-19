"""vectorbt 백테스트 래퍼.

거래세 0.18% + 수수료 0.015% + 슬리피지 5bp 를 디폴트로 반영.
LLM 신호를 사전 생성해 시그널 DataFrame 으로 주입하는 형태.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd


@dataclass
class BacktestResult:
    total_return_pct: float
    sharpe: float
    max_drawdown_pct: float
    win_rate: float
    n_trades: int
    output_dir: Path


def run_backtest(
    *,
    prices: pd.DataFrame,           # index=date, columns=ticker
    entries: pd.DataFrame,          # bool, same shape
    exits: pd.DataFrame,            # bool, same shape
    initial_cash: float = 10_000_000.0,
    fee_pct: float = 0.00015,       # 수수료 0.015%
    tax_pct: float = 0.0018,        # 거래세 0.18% (매도시)
    slippage_pct: float = 0.0005,   # 슬리피지 5bp
    output_dir: Path | str = "backtest_results",
) -> BacktestResult:
    import vectorbt as vbt  # type: ignore[import-not-found]

    pf = vbt.Portfolio.from_signals(
        close=prices,
        entries=entries,
        exits=exits,
        init_cash=initial_cash,
        fees=fee_pct + tax_pct,     # 단순화: 양방향 모두 동일 부과 (보수적)
        slippage=slippage_pct,
        freq="1D",
    )

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    pf.stats().to_csv(out / f"{today}_stats.csv")

    stats = pf.stats()
    return BacktestResult(
        total_return_pct=float(stats["Total Return [%]"]),
        sharpe=float(stats.get("Sharpe Ratio", 0.0)),
        max_drawdown_pct=float(stats["Max Drawdown [%]"]),
        win_rate=float(stats.get("Win Rate [%]", 0.0)),
        n_trades=int(stats.get("Total Trades", 0)),
        output_dir=out,
    )
