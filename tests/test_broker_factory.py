"""get_broker 팩토리 테스트 — PaperBroker 디폴트 / KIS 설정 시 KISBroker.

KIS 경로는 가짜 `pykis` 모듈을 주입해 실제 SDK 없이 검증한다.
실계좌(kis_live) 활성화 가드는 config.Settings 검증기(_validate_thresholds)에 있으므로
그 가드가 confirm 없이는 Settings 자체를 거부하는지 확인한다.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest
from pydantic import SecretStr

from kr_ai_trader.broker.base import Broker
from kr_ai_trader.broker.factory import get_broker
from kr_ai_trader.broker.paper import PaperBroker
from kr_ai_trader.config import Settings


class _FakePyKis:
    """KISBroker 생성에 필요한 최소 PyKis 흉내. 생성 인자만 보관."""

    last_instance: _FakePyKis | None = None

    def __init__(self, **kwargs: Any) -> None:
        self.init_kwargs = kwargs
        _FakePyKis.last_instance = self


@pytest.fixture
def fake_pykis(monkeypatch: pytest.MonkeyPatch) -> type[_FakePyKis]:
    _FakePyKis.last_instance = None
    pykis_mod = types.ModuleType("pykis")
    pykis_mod.PyKis = _FakePyKis  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pykis", pykis_mod)
    return _FakePyKis


def _kis_settings(base: Settings, *, live: bool = False, confirm: str | None = None) -> Settings:
    """base settings 에 KIS 자격증명 주입."""
    update: dict[str, Any] = {
        "kis_app_key": SecretStr("app-key-123"),
        "kis_app_secret": SecretStr("app-secret-456"),
        "kis_account_number": "12345678-01",
        "kis_live": live,
    }
    if confirm is not None:
        update["kis_live_confirm"] = confirm
    return base.model_copy(update=update)


# --------------------------------------------------------------------------- #
# PaperBroker 디폴트 경로
# --------------------------------------------------------------------------- #
def test_default_returns_paper_broker_when_no_kis(settings: Settings) -> None:
    """KIS 자격증명 미설정 → PaperBroker."""
    broker = get_broker(settings)

    assert isinstance(broker, PaperBroker)
    assert broker.is_live is False
    assert isinstance(broker, Broker)


def test_force_paper_returns_paper_even_with_kis_config(
    settings: Settings, fake_pykis: type[_FakePyKis]
) -> None:
    """force_paper=True 면 KIS 가 설정돼 있어도 PaperBroker."""
    s = _kis_settings(settings)

    broker = get_broker(s, force_paper=True)

    assert isinstance(broker, PaperBroker)
    assert _FakePyKis.last_instance is None  # KIS 생성자 미호출


def test_partial_kis_config_falls_back_to_paper(settings: Settings) -> None:
    """일부 자격증명만 있으면(secret 누락) PaperBroker."""
    s = settings.model_copy(
        update={
            "kis_app_key": SecretStr("only-key"),
            "kis_app_secret": None,
            "kis_account_number": "12345678-01",
        }
    )

    broker = get_broker(s)

    assert isinstance(broker, PaperBroker)


# --------------------------------------------------------------------------- #
# KISBroker 경로
# --------------------------------------------------------------------------- #
def test_full_kis_config_returns_kis_broker(
    settings: Settings, fake_pykis: type[_FakePyKis]
) -> None:
    """전체 KIS 자격증명 → KISBroker, secret 평문 전달 확인."""
    s = _kis_settings(settings, live=False)

    broker = get_broker(s)

    from kr_ai_trader.broker.kis import KISBroker

    assert isinstance(broker, KISBroker)
    assert broker.is_live is False
    inst = _FakePyKis.last_instance
    assert inst is not None
    # SecretStr 가 평문으로 풀려 SDK 에 전달됐는지
    assert inst.init_kwargs["appkey"] == "app-key-123"
    assert inst.init_kwargs["secretkey"] == "app-secret-456"
    assert inst.init_kwargs["virtual"] is True  # paper 모드


def test_kis_live_propagates_is_live_flag(
    settings: Settings, fake_pykis: type[_FakePyKis]
) -> None:
    """kis_live=True (confirm 동반) → KISBroker.is_live True, virtual=False."""
    s = _kis_settings(settings, live=True, confirm="I_UNDERSTAND_REAL_MONEY")

    broker = get_broker(s)

    assert broker.is_live is True
    inst = _FakePyKis.last_instance
    assert inst is not None
    assert inst.init_kwargs["virtual"] is False  # 실계좌


# --------------------------------------------------------------------------- #
# 실계좌 활성화 가드 (config.Settings 검증기)
# --------------------------------------------------------------------------- #
def test_live_without_confirm_is_refused_at_settings(settings: Settings) -> None:
    """kis_live=True 인데 confirm 토큰 없으면 Settings 자체가 거부.

    팩토리에 도달하기 전에 차단된다 — 실계좌 가드는 Settings 검증기에 있음.
    """
    with pytest.raises(ValueError, match="kis_live_confirm"):
        Settings(halt_file=settings.halt_file, kis_live=True, kis_live_confirm=None)


def test_live_with_wrong_confirm_is_refused(settings: Settings) -> None:
    """confirm 토큰 오타 → 거부."""
    with pytest.raises(ValueError, match="I_UNDERSTAND_REAL_MONEY"):
        Settings(
            halt_file=settings.halt_file,
            kis_live=True,
            kis_live_confirm="yes-please",
        )


def test_live_with_correct_confirm_constructs(settings: Settings) -> None:
    """올바른 confirm 토큰 → Settings 생성 성공."""
    s = Settings(
        halt_file=settings.halt_file,
        kis_live=True,
        kis_live_confirm="I_UNDERSTAND_REAL_MONEY",
    )
    assert s.kis_live is True
    assert s.kis_live_confirm == "I_UNDERSTAND_REAL_MONEY"
