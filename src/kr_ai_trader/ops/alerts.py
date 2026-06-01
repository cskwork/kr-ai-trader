"""Slack / Telegram 알람 발송 — graceful degrade.

- `send_alert` : 자격증명이 주어진 채널에만 비동기 발송.
- 어떤 전송 오류도 예외로 던지지 않음. 실패 채널은 False 로 표시하고 log.warning.
- 순수 모듈: get_settings() 를 읽지 않음. 통합 단계에서 Settings -> 파라미터로 주입.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import httpx
import structlog

log = structlog.get_logger(__name__)

AlertLevel = Literal["info", "warning", "critical"]

# 레벨별 머리말. Slack/Telegram 본문 앞에 붙여 식별성 높임.
_LEVEL_PREFIX: dict[AlertLevel, str] = {
    "info": "[INFO]",
    "warning": "[WARN]",
    "critical": "[CRITICAL]",
}

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


@dataclass(frozen=True)
class AlertResult:
    """채널별 전송 성공 여부."""

    slack: bool
    telegram: bool


def result_to_dict(r: AlertResult) -> dict[str, object]:
    """prices.summary_to_dict 와 동일 스타일의 직렬화 헬퍼."""
    return {"slack": r.slack, "telegram": r.telegram}


def _format(level: AlertLevel, message: str) -> str:
    """레벨 머리말을 붙인 발송 본문."""
    return f"{_LEVEL_PREFIX.get(level, '[INFO]')} {message}"


async def _post_slack(client: httpx.AsyncClient, webhook: str, text: str) -> bool:
    """Slack incoming webhook 으로 발송. 실패 시 False (예외 미전파)."""
    try:
        resp = await client.post(webhook, json={"text": text})
        resp.raise_for_status()
        return True
    except Exception as exc:  # graceful degrade: 어떤 오류도 삼킴
        log.warning("alert.slack_failed", error=str(exc))
        return False


async def _post_telegram(
    client: httpx.AsyncClient, token: str, chat_id: str, text: str
) -> bool:
    """Telegram sendMessage API 로 발송. 실패 시 False (예외 미전파)."""
    try:
        resp = await client.post(
            _TELEGRAM_API.format(token=token),
            json={"chat_id": chat_id, "text": text},
        )
        resp.raise_for_status()
        return True
    except Exception as exc:  # graceful degrade: 어떤 오류도 삼킴
        log.warning("alert.telegram_failed", error=str(exc))
        return False


async def send_alert(
    level: AlertLevel,
    message: str,
    *,
    slack_webhook: str | None = None,
    telegram_token: str | None = None,
    telegram_chat_id: str | None = None,
    timeout: float = 10.0,
) -> dict[str, bool]:
    """자격증명이 주어진 채널에만 알람 발송.

    - Slack: `slack_webhook` 가 있으면 `{"text": ...}` POST.
    - Telegram: `telegram_token` 과 `telegram_chat_id` 가 모두 있으면 sendMessage POST.
    - 채널이 하나도 설정되지 않으면 log.info 후 전부 False 반환.
    - 어떤 전송 오류도 예외로 던지지 않음 (channel=False 로 표시).

    반환: {'slack': bool, 'telegram': bool} — 채널별 전송 성공 여부.
    """
    text = _format(level, message)
    telegram_ready = bool(telegram_token) and bool(telegram_chat_id)

    if not slack_webhook and not telegram_ready:
        log.info("alert.no_channel_configured", level=level)
        return {"slack": False, "telegram": False}

    slack_ok = False
    telegram_ok = False
    async with httpx.AsyncClient(timeout=timeout) as client:
        if slack_webhook:
            slack_ok = await _post_slack(client, slack_webhook, text)
        if telegram_ready:
            # telegram_ready 가 True 이면 token/chat_id 모두 존재함이 보장됨.
            telegram_ok = await _post_telegram(
                client, telegram_token, telegram_chat_id, text  # type: ignore[arg-type]
            )

    return {"slack": slack_ok, "telegram": telegram_ok}
