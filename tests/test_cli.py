"""CLI (typer app) — get_settings/get_llm 모킹. 실제 프로바이더/네트워크 호출 없음.

cli.py 는 `from .config import get_settings` / `from .llm import get_llm` 로 심볼을
cli 네임스페이스에 바인딩한다. 따라서 patch 대상은 원본 모듈이 아니라
`kr_ai_trader.cli.get_settings` / `kr_ai_trader.cli.get_llm` 다.
ping-llm 은 asyncio.run(llm.healthcheck()) 를 호출하므로 healthcheck 는 async 여야 한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from kr_ai_trader import cli
from kr_ai_trader.config import Settings

runner = CliRunner()


@pytest.fixture
def cli_settings(tmp_path: Path) -> Settings:
    """info 출력에 쓰이는 필드를 가진 결정적 Settings."""
    return Settings(
        halt_file=tmp_path / "HALT",
        llm_provider="ollama",
        kis_live=False,
        universe="kospi200",
        max_position_pct=3.0,
        max_sector_pct=30.0,
        daily_loss_halt_pct=2.0,
        daily_loss_flatten_pct=4.0,
        hard_stop_pct=7.0,
    )


class _FakeLLM:
    """LLMProvider 대역. healthcheck 결과를 생성자에서 고정."""

    def __init__(self, *, name: str, model: str, healthy: bool) -> None:
        self.name = name
        self.model = model
        self._healthy = healthy

    async def healthcheck(self) -> bool:
        return self._healthy


def _patch_settings(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: settings)


def _patch_llm(monkeypatch: pytest.MonkeyPatch, llm: _FakeLLM) -> None:
    monkeypatch.setattr(cli, "get_llm", lambda _s: llm)


def test_info_prints_settings_summary(
    monkeypatch: pytest.MonkeyPatch, cli_settings: Settings
) -> None:
    """info: 설정 요약의 핵심 문자열이 모두 출력되고 exit 0."""
    _patch_settings(monkeypatch, cli_settings)

    result = runner.invoke(cli.app, ["info"])

    assert result.exit_code == 0
    out = result.stdout
    assert "LLM provider" in out
    assert "ollama" in out
    assert "KIS live" in out
    assert "모의투자" in out
    assert "kospi200" in out
    # 리스크 수치 (반올림/표기 흔들림 방지 위해 정수부 위주로 확인)
    assert "pos=3" in out
    assert "sector=30" in out


def test_ping_llm_ok_path(
    monkeypatch: pytest.MonkeyPatch, cli_settings: Settings
) -> None:
    """ping-llm: healthcheck True -> OK, 프로바이더/모델명 출력, exit 0."""
    _patch_settings(monkeypatch, cli_settings)
    _patch_llm(
        monkeypatch, _FakeLLM(name="ollama", model="qwen2.5", healthy=True)
    )

    result = runner.invoke(cli.app, ["ping-llm"])

    assert result.exit_code == 0
    assert "ollama" in result.stdout
    assert "qwen2.5" in result.stdout
    assert "OK" in result.stdout


def test_ping_llm_fail_path(
    monkeypatch: pytest.MonkeyPatch, cli_settings: Settings
) -> None:
    """graceful degrade: healthcheck False -> FAIL 표시하되 exit 0 (크래시 아님)."""
    _patch_settings(monkeypatch, cli_settings)
    _patch_llm(
        monkeypatch, _FakeLLM(name="ollama", model="qwen2.5", healthy=False)
    )

    result = runner.invoke(cli.app, ["ping-llm"])

    assert result.exit_code == 0
    assert "FAIL" in result.stdout
    assert "OK" not in result.stdout


def test_ping_llm_propagates_provider_error(
    monkeypatch: pytest.MonkeyPatch, cli_settings: Settings
) -> None:
    """error 경로: healthcheck 가 예외를 던지면 비정상 종료(exit != 0).

    조용히 OK 로 위장하지 않는지 검증."""
    _patch_settings(monkeypatch, cli_settings)

    class _BoomLLM:
        name = "ollama"
        model = "qwen2.5"

        async def healthcheck(self) -> bool:
            raise RuntimeError("provider unreachable")

    monkeypatch.setattr(cli, "get_llm", lambda _s: _BoomLLM())

    result = runner.invoke(cli.app, ["ping-llm"])

    assert result.exit_code != 0
    assert isinstance(result.exception, RuntimeError)


def test_help_lists_commands() -> None:
    """--help: 등록된 커맨드가 노출되고 exit 0 (앱 와이어링 스모크)."""
    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    assert "info" in result.stdout
    assert "ping-llm" in result.stdout


def test_unknown_command_exits_nonzero() -> None:
    """malformed 입력: 없는 커맨드는 비정상 종료."""
    result = runner.invoke(cli.app, ["does-not-exist"])

    assert result.exit_code != 0
