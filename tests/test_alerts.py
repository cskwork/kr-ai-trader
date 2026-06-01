"""ops.alerts 테스트 — respx 로 HTTP mock. 실제 네트워크 없음."""

from __future__ import annotations

import httpx
import respx

from kr_ai_trader.ops.alerts import send_alert

SLACK_WEBHOOK = "https://hooks.slack.com/services/T000/B000/xxx"
TG_TOKEN = "123:ABC"
TG_CHAT_ID = "555"
TG_URL = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"


@respx.mock
async def test_slack_happy_path() -> None:
    route = respx.post(SLACK_WEBHOOK).mock(return_value=httpx.Response(200, text="ok"))
    result = await send_alert("critical", "loss halt", slack_webhook=SLACK_WEBHOOK)

    assert result == {"slack": True, "telegram": False}
    assert route.called
    assert route.calls.last.request.url == SLACK_WEBHOOK
    sent = route.calls.last.request.content.decode()
    assert "loss halt" in sent
    assert "[CRITICAL]" in sent


@respx.mock
async def test_telegram_happy_path() -> None:
    route = respx.post(TG_URL).mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    result = await send_alert(
        "info",
        "hello",
        telegram_token=TG_TOKEN,
        telegram_chat_id=TG_CHAT_ID,
    )

    assert result == {"slack": False, "telegram": True}
    assert route.called
    body = route.calls.last.request.content.decode()
    assert TG_CHAT_ID in body
    assert "[INFO] hello" in body


@respx.mock
async def test_both_channels() -> None:
    respx.post(SLACK_WEBHOOK).mock(return_value=httpx.Response(200))
    respx.post(TG_URL).mock(return_value=httpx.Response(200, json={"ok": True}))
    result = await send_alert(
        "warning",
        "watch out",
        slack_webhook=SLACK_WEBHOOK,
        telegram_token=TG_TOKEN,
        telegram_chat_id=TG_CHAT_ID,
    )
    assert result == {"slack": True, "telegram": True}


async def test_no_creds_returns_all_false() -> None:
    # 채널 미설정 -> 네트워크 호출 없이 전부 False.
    result = await send_alert("info", "nobody listens")
    assert result == {"slack": False, "telegram": False}


async def test_telegram_partial_creds_skipped() -> None:
    # token 만 있고 chat_id 없음 -> 텔레그램 미발송, 다른 채널도 없음.
    result = await send_alert("info", "partial", telegram_token=TG_TOKEN)
    assert result == {"slack": False, "telegram": False}


@respx.mock
async def test_slack_http_error_graceful() -> None:
    respx.post(SLACK_WEBHOOK).mock(return_value=httpx.Response(500))
    result = await send_alert("critical", "boom", slack_webhook=SLACK_WEBHOOK)
    assert result == {"slack": False, "telegram": False}


@respx.mock
async def test_telegram_network_error_graceful() -> None:
    respx.post(TG_URL).mock(side_effect=httpx.ConnectError("down"))
    result = await send_alert(
        "warning",
        "boom",
        telegram_token=TG_TOKEN,
        telegram_chat_id=TG_CHAT_ID,
    )
    assert result == {"slack": False, "telegram": False}


@respx.mock
async def test_one_channel_fails_other_succeeds() -> None:
    respx.post(SLACK_WEBHOOK).mock(return_value=httpx.Response(500))
    respx.post(TG_URL).mock(return_value=httpx.Response(200, json={"ok": True}))
    result = await send_alert(
        "critical",
        "mixed",
        slack_webhook=SLACK_WEBHOOK,
        telegram_token=TG_TOKEN,
        telegram_chat_id=TG_CHAT_ID,
    )
    assert result == {"slack": False, "telegram": True}
