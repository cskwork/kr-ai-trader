"""환경변수 기반 설정. .env 자동 로드."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMProviderName(str, Enum):
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
    claude_code_model: str = "haiku"

    codex_bin: str = "codex"
    codex_model: str = "gpt-5"

    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:14b-instruct"

    # KIS
    kis_app_key: SecretStr | None = None
    kis_app_secret: SecretStr | None = None
    kis_account_number: str | None = None
    kis_live: bool = False
    # 실계좌 활성화 시 반드시 함께 'I_UNDERSTAND_REAL_MONEY' 로 설정해야 함.
    kis_live_confirm: str | None = None

    # 리스크
    max_position_pct: float = Field(default=3.0, ge=0.0, le=100.0)
    max_sector_pct: float = Field(default=30.0, ge=0.0, le=100.0)
    daily_loss_halt_pct: float = Field(default=2.0, ge=0.0)
    daily_loss_flatten_pct: float = Field(default=4.0, ge=0.0)
    hard_stop_pct: float = Field(default=7.0, ge=0.0)
    leverage: float = Field(default=0.0, ge=0.0)

    # 거래비용 (PaperBroker + 백테 공용)
    commission_pct: float = Field(default=0.00015, ge=0.0)          # 매수/매도 양방향
    tax_kospi_sell_pct: float = Field(default=0.0018, ge=0.0)       # 코스피 매도 거래세
    tax_kosdaq_sell_pct: float = Field(default=0.0018, ge=0.0)      # 코스닥 매도 거래세

    # 유니버스
    universe: str = "kospi200"
    universe_file: Path | None = None

    # 리서치 데이터 (펀더멘털/DART/뉴스)
    dart_api_key: SecretStr | None = Field(
        default=None,
        description="OpenDART REST 인증키. 미설정 시 DART 공시 off(빈 리스트).",
    )
    dart_lookback_days: int = Field(default=14, ge=1, description="DART 최근 공시 조회 일수")
    enable_dart: bool = Field(default=True, description="DART 공시 수집 토글")
    news_lookback_items: int = Field(default=8, ge=1, description="종목당 뉴스 헤드라인 개수")
    enable_news: bool = Field(default=True, description="뉴스 수집 활성화 토글")

    # 알람
    slack_webhook_url: SecretStr | None = None
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None

    # 운영 안전장치
    halt_file: Path = Path.home() / ".kr-ai-trader" / "HALT"
    reconciliation_interval_sec: int = 60
    daily_pnl_file: Path = Field(
        default=Path.home() / ".kr-ai-trader" / "daily_pnl.json",
        description="DailyPnLTracker 가 장 시작 자본을 영속화하는 JSON 파일 경로",
    )

    @model_validator(mode="after")
    def _validate_thresholds(self) -> Settings:
        if self.daily_loss_halt_pct > self.daily_loss_flatten_pct:
            raise ValueError(
                f"daily_loss_halt_pct ({self.daily_loss_halt_pct}) must be <= "
                f"daily_loss_flatten_pct ({self.daily_loss_flatten_pct})"
            )
        if self.kis_live and self.kis_live_confirm != "I_UNDERSTAND_REAL_MONEY":
            raise ValueError(
                "kis_live=True requires kis_live_confirm='I_UNDERSTAND_REAL_MONEY' "
                "(set via KIS_LIVE_CONFIRM env). Refusing to start real-money mode."
            )
        return self


_settings: Settings | None = None


def get_settings() -> Settings:
    """싱글톤. 테스트는 `Settings()` 직접 인스턴스화 권장."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """테스트 픽스처용. 모듈 캐시 싱글톤 초기화."""
    global _settings
    _settings = None
