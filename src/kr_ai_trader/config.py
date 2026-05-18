"""환경변수 기반 설정. .env 자동 로드."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMProviderName(StrEnum):
    anthropic_api = "anthropic_api"
    openai_api = "openai_api"
    claude_code_cli = "claude_code_cli"
    codex_cli = "codex_cli"
    ollama = "ollama"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # 운영
    app_env: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"

    # LLM
    llm_provider: LLMProviderName = LLMProviderName.claude_code_cli

    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-sonnet-4-6"

    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-5"

    claude_code_bin: str = "claude"
    claude_code_model: str = "claude-sonnet-4-6"

    codex_bin: str = "codex"
    codex_model: str = "gpt-5"

    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:14b-instruct"

    # KIS
    kis_app_key: SecretStr | None = None
    kis_app_secret: SecretStr | None = None
    kis_account_number: str | None = None
    kis_live: bool = False

    # 리스크
    max_position_pct: float = Field(default=3.0, ge=0.0, le=100.0)
    max_sector_pct: float = Field(default=30.0, ge=0.0, le=100.0)
    daily_loss_halt_pct: float = Field(default=2.0, ge=0.0)
    daily_loss_flatten_pct: float = Field(default=4.0, ge=0.0)
    hard_stop_pct: float = Field(default=7.0, ge=0.0)
    leverage: float = Field(default=0.0, ge=0.0)

    # 유니버스
    universe: str = "kospi200"
    universe_file: Path | None = None

    # 알람
    slack_webhook_url: SecretStr | None = None
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None

    # 운영 안전장치
    halt_file: Path = Path("/tmp/kr-ai-trader.HALT")
    reconciliation_interval_sec: int = 60


_settings: Settings | None = None


def get_settings() -> Settings:
    """싱글톤. 테스트는 `Settings()` 직접 인스턴스화 권장."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
