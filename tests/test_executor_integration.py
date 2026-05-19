"""Executor 통합: PaperBroker + RiskGate + JournalRecorder 가 한 흐름에서 동작하는지.

LLM 은 호출하지 않는다 — 사전에 만든 TradeProposal 을 주입.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from kr_ai_trader.agents.moderator import TradeProposal
from kr_ai_trader.broker.base import Quote
from kr_ai_trader.broker.paper import PaperBroker
from kr_ai_trader.config import Settings
from kr_ai_trader.execution.executor import Executor
from kr_ai_trader.journal.recorder import JournalRecorder
from kr_ai_trader.risk.gate import RiskGate


def _wire(tmp_path: Path) -> tuple[Executor, PaperBroker, JournalRecorder, Settings]:
    s = Settings(
        halt_file=tmp_path / "HALT",
        commission_pct=0.0,
        max_position_pct=10.0,
    )
    broker = PaperBroker(initial_cash=10_000_000.0, settings=s)
    broker.set_quote(Quote(ticker="005930", price=70_000.0, timestamp=datetime.now(timezone.utc)))
    journal = JournalRecorder(journal_dir=tmp_path / "journal")
    gate = RiskGate(settings=s, universe=frozenset({"005930"}))
    executor = Executor(broker=broker, risk_gate=gate, journal=journal, strategy_name="test")
    return executor, broker, journal, s


async def test_happy_path_fills_and_journals(tmp_path: Path) -> None:
    executor, _broker, _journal, _settings = _wire(tmp_path)
    proposal = TradeProposal(
        ticker="005930", side="buy", conviction=0.7, size_pct=2.0,
        thesis="momentum", risks=["macro shock"], stop_loss_pct=5.0,
    )
    order = await executor.execute(proposal)
    assert order is not None
    assert order.status == "filled"
    # 동일 (전략, ticker, KST date, side, qty) 재시도 — 같은 client_order_id, 같은 결과.
    order2 = await executor.execute(proposal)
    assert order2 is not None and order2.client_order_id == order.client_order_id
    # PaperBroker 의 idempotent path 가 같은 broker_order_id 를 반환.
    assert order2.broker_order_id == order.broker_order_id
    # 저널 작성 확인.
    md = next((tmp_path / "journal").glob("*.md")).read_text()
    assert "ORDER FILLED" in md
    assert order.client_order_id in md


async def test_min_notional_skip_records_rejection(tmp_path: Path) -> None:
    executor, _broker, _journal, _settings = _wire(tmp_path)
    # 0.001% 사이즈 → 1주 미만이므로 skip.
    proposal = TradeProposal(
        ticker="005930", side="buy", conviction=0.7, size_pct=0.001,
        thesis="too small", risks=["min"],
    )
    order = await executor.execute(proposal)
    assert order is None
    md = next((tmp_path / "journal").glob("*.md")).read_text()
    assert "REJECTED" in md
    assert "below 1 share" in md


async def test_pre_quote_universe_guard(tmp_path: Path) -> None:
    """LLM 이 universe 외 ticker 를 뱉어도 broker.get_quote 가 호출되기 전에 차단."""
    executor, broker, _, _ = _wire(tmp_path)
    # broker 에 999999 견적이 없으므로, 만약 가드가 작동하지 않으면 BrokerError 발생.
    proposal = TradeProposal(
        ticker="999999", side="buy", conviction=0.9, size_pct=2.0,
        thesis="hallucinated", risks=["hallucinated"],
    )
    order = await executor.execute(proposal)
    assert order is None
    # broker 잔고는 변동 없어야 함.
    assert await broker.get_cash() == 10_000_000.0
