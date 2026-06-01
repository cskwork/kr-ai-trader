"""LLM 팩토리 테스트 — provider enum → 올바른 구현 클래스 매핑.

SDK 미설치(anthropic/openai) 환경에서도 구성이 깨지지 않도록 가짜 모듈을 sys.modules 에
주입한다(test_fundamentals 의 fake pykrx 기법). CLI 프로바이더는 shutil.which 를 패치해
PATH 의존을 제거하고, unknown provider 는 명확히 LLMError 를 던지는지 확인한다.
"""

from __future__ import annotations

import sys
import types

import pytest

from kr_ai_trader.config import LLMProviderName, Settings
from kr_ai_trader.llm import claude_code_cli, codex_cli
from kr_ai_trader.llm.base import LLMError
from kr_ai_trader.llm.factory import get_llm


def _make_settings(provider: LLMProviderName, **overrides: object) -> Settings:
    """provider 만 바꾼 최소 Settings. 키 검증을 통과하도록 더미 키 주입."""
    base: dict[str, object] = {
        "llm_provider": provider,
        "anthropic_api_key": "dummy-anthropic-key",
        "openai_api_key": "dummy-openai-key",
    }
    base.update(overrides)
    return Settings(**base)


def _install_fake_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    """`from anthropic import AsyncAnthropic` / `from anthropic.types import ToolParam`."""

    class _AsyncAnthropic:
        def __init__(self, *_a: object, **_k: object) -> None: ...

    anthropic_mod = types.ModuleType("anthropic")
    anthropic_mod.AsyncAnthropic = _AsyncAnthropic  # type: ignore[attr-defined]
    types_mod = types.ModuleType("anthropic.types")
    types_mod.ToolParam = dict  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", anthropic_mod)
    monkeypatch.setitem(sys.modules, "anthropic.types", types_mod)


def _install_fake_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    """`from openai import AsyncOpenAI`."""

    class _AsyncOpenAI:
        def __init__(self, *_a: object, **_k: object) -> None: ...

    openai_mod = types.ModuleType("openai")
    openai_mod.AsyncOpenAI = _AsyncOpenAI  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", openai_mod)


def _drop_cached_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    """anthropic/openai 를 import 한 프로바이더 모듈 캐시를 비워 재import 시 가짜를 타게 한다."""
    for name in ("kr_ai_trader.llm.anthropic_api", "kr_ai_trader.llm.openai_api"):
        monkeypatch.delitem(sys.modules, name, raising=False)


def test_factory_returns_anthropic_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_anthropic(monkeypatch)
    _drop_cached_modules(monkeypatch)
    from kr_ai_trader.llm.anthropic_api import AnthropicAPIProvider

    provider = get_llm(_make_settings(LLMProviderName.anthropic_api))

    assert isinstance(provider, AnthropicAPIProvider)
    assert provider.name == "anthropic_api"


def test_factory_returns_openai_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_openai(monkeypatch)
    _drop_cached_modules(monkeypatch)
    from kr_ai_trader.llm.openai_api import OpenAIAPIProvider

    provider = get_llm(_make_settings(LLMProviderName.openai_api))

    assert isinstance(provider, OpenAIAPIProvider)
    assert provider.name == "openai_api"


def test_factory_returns_claude_code_cli_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    # CLI 생성자는 PATH 확인이 필수 → which 를 통과시킨다.
    monkeypatch.setattr(claude_code_cli.shutil, "which", lambda _b: "/usr/bin/claude")

    provider = get_llm(_make_settings(LLMProviderName.claude_code_cli))

    assert isinstance(provider, claude_code_cli.ClaudeCodeCLIProvider)
    assert provider.name == "claude_code_cli"


def test_factory_returns_codex_cli_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex_cli.shutil, "which", lambda _b: "/usr/bin/codex")

    provider = get_llm(_make_settings(LLMProviderName.codex_cli))

    assert isinstance(provider, codex_cli.CodexCLIProvider)
    assert provider.name == "codex_cli"


def test_factory_returns_ollama_provider() -> None:
    # ollama 는 httpx 기반(설치됨) → 네트워크 호출 없이 객체 생성만 검증.
    from kr_ai_trader.llm.ollama import OllamaProvider

    provider = get_llm(_make_settings(LLMProviderName.ollama))

    assert isinstance(provider, OllamaProvider)
    assert provider.name == "ollama"


def test_factory_passes_model_and_host_to_ollama() -> None:
    from kr_ai_trader.llm.ollama import OllamaProvider

    provider = get_llm(
        _make_settings(
            LLMProviderName.ollama,
            ollama_host="http://example:9999/",
            ollama_model="qwen-test",
        )
    )

    assert isinstance(provider, OllamaProvider)
    assert provider.host == "http://example:9999"  # rstrip('/') 적용
    assert provider.model == "qwen-test"


def test_factory_cli_missing_bin_raises_llm_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """오설정(바이너리 부재)이면 팩토리가 LLMError 를 명확히 전파."""
    monkeypatch.setattr(codex_cli.shutil, "which", lambda _b: None)

    with pytest.raises(LLMError, match="not found in PATH"):
        get_llm(_make_settings(LLMProviderName.codex_cli))


def test_factory_unknown_provider_raises_llm_error() -> None:
    """enum 에 없는 값으로 강제 설정하면 unknown provider LLMError."""

    class _Stub:
        """Settings 의 필요한 속성만 흉내내는 가짜."""

        llm_provider = "made_up_provider"

    with pytest.raises(LLMError, match="unknown LLM provider"):
        get_llm(_Stub())  # type: ignore[arg-type]
