"""LLM 프로바이더 팩토리. `LLM_PROVIDER` 환경변수 한 줄로 전환."""

from __future__ import annotations

from ..config import LLMProviderName, Settings, get_settings
from .base import LLMError, LLMProvider


def get_llm(settings: Settings | None = None) -> LLMProvider:
    s = settings or get_settings()
    name = s.llm_provider

    if name == LLMProviderName.anthropic_api:
        from .anthropic_api import AnthropicAPIProvider

        key = s.anthropic_api_key.get_secret_value() if s.anthropic_api_key else ""
        return AnthropicAPIProvider(api_key=key, model=s.anthropic_model)

    if name == LLMProviderName.openai_api:
        from .openai_api import OpenAIAPIProvider

        key = s.openai_api_key.get_secret_value() if s.openai_api_key else ""
        return OpenAIAPIProvider(api_key=key, model=s.openai_model)

    if name == LLMProviderName.claude_code_cli:
        from .claude_code_cli import ClaudeCodeCLIProvider

        return ClaudeCodeCLIProvider(bin_path=s.claude_code_bin, model=s.claude_code_model)

    if name == LLMProviderName.codex_cli:
        from .codex_cli import CodexCLIProvider

        return CodexCLIProvider(bin_path=s.codex_bin, model=s.codex_model)

    if name == LLMProviderName.ollama:
        from .ollama import OllamaProvider

        return OllamaProvider(host=s.ollama_host, model=s.ollama_model)

    raise LLMError(f"unknown LLM provider: {name}")
