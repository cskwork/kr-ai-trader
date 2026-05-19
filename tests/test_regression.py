"""리뷰에서 발견된 핵심 버그에 대한 회귀 테스트.

각 테스트의 이름이 해당 결함을 설명. 향후 누군가 해당 로직을 만지면 즉시 깨지도록.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from kr_ai_trader.agents.moderator import TradeProposal
from kr_ai_trader.broker.base import Order, OrderSide, Position
from kr_ai_trader.config import Settings
from kr_ai_trader.execution.executor import Executor
from kr_ai_trader.journal.recorder import JournalRecorder
from kr_ai_trader.llm.base import LLMError, extract_json, validate_against_schema
from kr_ai_trader.risk.gate import RiskGate

# --------------------------------------------------------------------------- #
# 1. RiskGate halt/flatten 독립 평가                                          #
# --------------------------------------------------------------------------- #

def test_loss_between_halt_and_flatten_still_blocks_buy() -> None:
    s = Settings(daily_loss_halt_pct=2.0, daily_loss_flatten_pct=4.0)
    gate = RiskGate(settings=s, universe=frozenset({"005930"}))
    order = Order(client_order_id="t", ticker="005930", side=OrderSide.buy, quantity=1)
    decision = gate.evaluate(
        order=order,
        cash=10_000_000.0,
        positions=[],
        portfolio_equity=10_000_000.0,
        day_pnl_pct=-3.0,                # halt 와 flatten 사이
        last_price=70_000.0,
    )
    assert not decision.accepted
    assert any("halt threshold" in r for r in decision.reasons), decision.reasons


def test_loss_deeper_than_flatten_logs_both_halt_and_flatten() -> None:
    s = Settings(daily_loss_halt_pct=2.0, daily_loss_flatten_pct=4.0)
    gate = RiskGate(settings=s, universe=frozenset({"005930"}))
    order = Order(client_order_id="t", ticker="005930", side=OrderSide.buy, quantity=1)
    decision = gate.evaluate(
        order=order,
        cash=10_000_000.0,
        positions=[],
        portfolio_equity=10_000_000.0,
        day_pnl_pct=-5.0,
        last_price=70_000.0,
    )
    assert not decision.accepted
    # halt 와 flatten 메시지가 모두 기록되어야 함 (이전엔 elif 로 인해 flatten 만 기록됨).
    assert any("halt threshold" in r for r in decision.reasons), decision.reasons
    assert any("flatten threshold" in r for r in decision.reasons), decision.reasons


def test_config_rejects_halt_greater_than_flatten() -> None:
    with pytest.raises(ValueError):
        Settings(daily_loss_halt_pct=10.0, daily_loss_flatten_pct=5.0)


# --------------------------------------------------------------------------- #
# 2. 결정론적 client_order_id                                                #
# --------------------------------------------------------------------------- #

def test_idempotent_id_is_deterministic_within_same_day() -> None:
    fixed = datetime(2026, 5, 19, 12, 30, tzinfo=timezone.utc)
    a = Executor.make_idempotent_id("ui", "005930", "buy", 10, when=fixed)
    b = Executor.make_idempotent_id("ui", "005930", "buy", 10, when=fixed)
    assert a == b


def test_idempotent_id_changes_with_qty_or_side() -> None:
    fixed = datetime(2026, 5, 19, 12, 30, tzinfo=timezone.utc)
    base = Executor.make_idempotent_id("ui", "005930", "buy", 10, when=fixed)
    assert Executor.make_idempotent_id("ui", "005930", "buy", 11, when=fixed) != base
    assert Executor.make_idempotent_id("ui", "005930", "sell", 10, when=fixed) != base


# --------------------------------------------------------------------------- #
# 3. extract_json 견고화                                                     #
# --------------------------------------------------------------------------- #

def test_extract_json_picks_last_balanced_object() -> None:
    text = (
        "Example shape: {\"foo\": 1}\n"
        "Final answer below:\n"
        "{\"ticker\": \"005930\", \"side\": \"buy\"}\n"
    )
    data = extract_json(text)
    assert data == {"ticker": "005930", "side": "buy"}


def test_extract_json_ignores_braces_inside_strings() -> None:
    text = 'noise {"text": "this has { and } inside"} trailing'
    data = extract_json(text)
    assert data == {"text": "this has { and } inside"}


def test_extract_json_prefers_last_fence_block() -> None:
    text = (
        "```json\n{\"a\": 1}\n```\n"
        "and then\n"
        "```json\n{\"b\": 2}\n```"
    )
    assert extract_json(text) == {"b": 2}


# --------------------------------------------------------------------------- #
# 4. Schema validation 엄격성                                                #
# --------------------------------------------------------------------------- #

def test_schema_rejects_out_of_range_conviction() -> None:
    from kr_ai_trader.agents.schemas import PROPOSAL_SCHEMA

    bad = {
        "ticker": "005930",
        "side": "buy",
        "conviction": 2.5,              # 0..1 범위 위반
        "size_pct": 1.0,
        "thesis": "test",
        "risks": ["r"],
    }
    with pytest.raises(LLMError):
        validate_against_schema(bad, PROPOSAL_SCHEMA)


def test_schema_rejects_unknown_side() -> None:
    from kr_ai_trader.agents.schemas import PROPOSAL_SCHEMA

    bad = {
        "ticker": "005930",
        "side": "long",                 # enum 위반
        "conviction": 0.7,
        "size_pct": 1.0,
        "thesis": "test",
        "risks": ["r"],
    }
    with pytest.raises(LLMError):
        validate_against_schema(bad, PROPOSAL_SCHEMA)


# --------------------------------------------------------------------------- #
# 5. Journal sanitize + path stays inside dir                                #
# --------------------------------------------------------------------------- #

async def test_journal_escapes_backtick_fences(tmp_path: Path) -> None:
    journal = JournalRecorder(journal_dir=tmp_path)
    proposal = TradeProposal(
        ticker="005930",
        side="buy",
        conviction=0.7,
        size_pct=1.0,
        thesis="```\nMALICIOUS\n```",
        risks=["normal"],
    )
    await journal.record_rejection(proposal=proposal, reasons=["test"])
    content = next(tmp_path.glob("*.md")).read_text()
    # 원본 ``` 시퀀스가 zero-width 로 분리되어 펜스를 끊지 못해야 함.
    assert "```\nMALICIOUS\n```" not in content


def test_journal_path_is_inside_dir(tmp_path: Path) -> None:
    journal = JournalRecorder(journal_dir=tmp_path)
    path = journal.path_for(datetime(2026, 5, 19, tzinfo=timezone.utc))
    assert path.parent.resolve() == tmp_path.resolve()


# --------------------------------------------------------------------------- #
# 6. Halt threshold sanity assertion fail-fast                               #
# --------------------------------------------------------------------------- #

def test_kis_live_requires_explicit_confirm() -> None:
    with pytest.raises(ValueError):
        Settings(kis_live=True, kis_live_confirm=None)
    # 올바른 confirm 은 통과 (KIS 자격증명 없어도 Settings 자체는 valid).
    s = Settings(kis_live=True, kis_live_confirm="I_UNDERSTAND_REAL_MONEY")
    assert s.kis_live is True


# --------------------------------------------------------------------------- #
# 7. Sector double-count regression                                          #
# --------------------------------------------------------------------------- #

def test_existing_position_not_double_counted_in_sector_check() -> None:
    """동일 ticker 의 기존 포지션은 sector 합계에 한 번만 포함되어야 함."""
    s = Settings(max_sector_pct=10.0)
    universe = frozenset({"005930", "000660"})
    gate = RiskGate(
        settings=s,
        universe=universe,
        sector_map={"005930": "tech", "000660": "tech"},
    )
    # 이미 동일 ticker 로 9% (900k) 보유, 추가로 0.5% (50k) 더 매수 시도 → 9.5% 이지만 게이트는
    # 동일 ticker 의 기존 가치를 빼지 않고 더해 19% 로 잘못 계산하던 결함이 있었음.
    positions = [Position(ticker="005930", quantity=9, avg_price=100_000.0, current_price=100_000.0)]
    order = Order(client_order_id="t", ticker="005930", side=OrderSide.buy, quantity=1)
    decision = gate.evaluate(
        order=order,
        cash=10_000_000.0,
        positions=positions,
        portfolio_equity=10_000_000.0,
        day_pnl_pct=0.0,
        last_price=100_000.0,
    )
    # 9.5% 이므로 max_position_pct(3%) 는 위반하지만, sector 한도(10%)는 위반 안 해야 함.
    sector_msgs = [r for r in decision.reasons if "sector" in r]
    assert not sector_msgs, f"sector double-counted: {sector_msgs}"
