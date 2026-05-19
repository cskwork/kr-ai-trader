"""공용 fixtures."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from kr_ai_trader.broker.base import Quote
from kr_ai_trader.broker.paper import PaperBroker
from kr_ai_trader.config import Settings
from kr_ai_trader.risk.gate import RiskGate


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    halt = tmp_path / "HALT"
    return Settings(
        halt_file=halt,
        max_position_pct=3.0,
        max_sector_pct=30.0,
        daily_loss_halt_pct=2.0,
        daily_loss_flatten_pct=4.0,
        leverage=0.0,
        universe="kospi200",
        commission_pct=0.00015,
        tax_kospi_sell_pct=0.0018,
        tax_kosdaq_sell_pct=0.0018,
    )


@pytest.fixture
def zero_fee_settings(settings: Settings) -> Settings:
    """기존 fee-less 테스트 호환용. PaperBroker 수수료 0 으로 잠금."""
    return settings.model_copy(
        update={
            "commission_pct": 0.0,
            "tax_kospi_sell_pct": 0.0018,
            "tax_kosdaq_sell_pct": 0.0018,
        }
    )


@pytest.fixture
def universe() -> frozenset[str]:
    return frozenset({"005930", "000660", "035420"})


@pytest.fixture
def risk_gate(settings: Settings, universe: frozenset[str]) -> RiskGate:
    return RiskGate(settings=settings, universe=universe)


@pytest.fixture
def paper_broker(zero_fee_settings: Settings) -> PaperBroker:
    broker = PaperBroker(initial_cash=10_000_000.0, settings=zero_fee_settings)
    broker.set_quote(
        Quote(
            ticker="005930",
            price=70_000.0,
            timestamp=datetime.now(timezone.utc),
        )
    )
    broker.set_quote(
        Quote(
            ticker="000660",
            price=150_000.0,
            timestamp=datetime.now(timezone.utc),
        )
    )
    return broker
