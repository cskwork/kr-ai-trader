"""FastAPI 백엔드 API 테스트 — 네트워크/실 LLM/pykrx 호출 없음.

전략: server 모듈은 협력자 심볼을 자기 네임스페이스로 import 하므로
(`from ..data.prices import compute_features` 등), 무거운 의존성은
server 모듈 네임스페이스에서 monkeypatch 한다. WebSocket 은 starlette
TestClient 의 `websocket_connect` 로 구동한다 (네트워크 없음).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from kr_ai_trader.agents.moderator import TradeProposal
from kr_ai_trader.api import server
from kr_ai_trader.broker.base import Order, OrderSide
from kr_ai_trader.config import LLMProviderName, Settings
from kr_ai_trader.data.prices import PriceSummary

FIXED_AS_OF = datetime(2026, 5, 29, tzinfo=timezone.utc).date()


def _summary(ticker: str = "005930", last_close: float = 70_000.0) -> PriceSummary:
    """결정론적 피처 요약. compute_features 대체용."""
    return PriceSummary(
        ticker=ticker,
        last_close=last_close,
        pct_change_1d=1.2,
        pct_change_5d=2.5,
        pct_change_20d=5.0,
        sma_5=69_000.0,
        sma_20=68_000.0,
        rsi_14=55.0,
        volume=1_000_000,
        as_of=FIXED_AS_OF,
    )


class _FakeLLM:
    """get_llm 대체. Moderator 도 함께 대체하므로 실제 호출되지는 않음."""

    name = "fake-provider"
    model = "fake-model"


class _StubModerator:
    """Moderator 대체. ws_cycle 안에서 `Moderator(llm=...)` 로 생성됨."""

    proposal: TradeProposal | None = None
    raise_exc: bool = False

    def __init__(self, *, llm: Any) -> None:
        self.llm = llm

    async def decide(self, *, ticker: str, market_context: str) -> TradeProposal | None:
        if _StubModerator.raise_exc:
            raise RuntimeError("moderator boom")
        return _StubModerator.proposal


@pytest.fixture(autouse=True)
def _reset_globals() -> Iterator[None]:
    """모듈 전역 브로커/저널 싱글톤을 테스트 간 격리."""
    server._paper_broker = None
    server._journal = None
    _StubModerator.proposal = None
    _StubModerator.raise_exc = False
    yield
    server._paper_broker = None
    server._journal = None


@pytest.fixture
def fixed_settings(tmp_path: Any) -> Settings:
    """halt 파일/유니버스 고정. tmp 경로로 파일 시스템 격리."""
    return Settings(
        halt_file=tmp_path / "HALT",
        universe="kospi200",
        llm_provider=LLMProviderName.claude_code_cli,
        commission_pct=0.0,
        tax_kospi_sell_pct=0.0,
        tax_kosdaq_sell_pct=0.0,
    )


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch, fixed_settings: Settings, tmp_path: Any
) -> Iterator[TestClient]:
    """모든 무거운 협력자를 server 네임스페이스에서 가짜로 교체한 TestClient."""
    monkeypatch.setattr(server, "get_settings", lambda: fixed_settings)
    monkeypatch.setattr(server, "load_universe", lambda *_a, **_k: frozenset({"005930", "000660"}))
    monkeypatch.setattr(server, "get_llm", lambda *_a, **_k: _FakeLLM())
    monkeypatch.setattr(server, "Moderator", _StubModerator)
    # 저널은 tmp 디렉토리로 격리 (cwd 의 journal/ 오염 방지).
    from kr_ai_trader.journal.recorder import JournalRecorder

    recorder = JournalRecorder(journal_dir=tmp_path / "journal")
    monkeypatch.setattr(server, "JournalRecorder", lambda *_a, **_k: recorder)
    monkeypatch.setattr(server, "compute_features", lambda ticker: _summary(ticker))
    with TestClient(server.app) as c:
        yield c


# ---------------------------------------------------------------------------
# GET 엔드포인트
# ---------------------------------------------------------------------------


def test_health_ok(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["provider"] == "claude_code_cli"
    assert "now_kst" in body and isinstance(body["kis_live"], bool)


def test_settings_shape(client: TestClient) -> None:
    r = client.get("/api/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["llm_provider"] == "claude_code_cli"
    assert body["universe"] == "kospi200"
    assert body["halt_active"] is False  # halt 파일 없음
    assert set(body["session"]) == {"is_business_day", "is_regular_session", "now_kst"}


def test_settings_halt_active_reflects_file(
    client: TestClient, fixed_settings: Settings
) -> None:
    fixed_settings.halt_file.write_text("halt", encoding="utf-8")
    body = client.get("/api/settings").json()
    assert body["halt_active"] is True


def test_universe_list(client: TestClient) -> None:
    r = client.get("/api/universe")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "kospi200"
    assert body["count"] == 2
    assert body["tickers"] == ["000660", "005930"]  # sorted, max 50


def test_positions_empty_broker(client: TestClient) -> None:
    r = client.get("/api/positions")
    assert r.status_code == 200
    body = r.json()
    assert body["broker"] == "paper"
    assert body["is_live"] is False
    assert body["cash"] == 10_000_000.0
    assert body["positions"] == []
    assert body["equity"] == 10_000_000.0


def test_features_happy(client: TestClient) -> None:
    r = client.get("/api/features/005930")
    assert r.status_code == 200
    body = r.json()
    assert body["ticker"] == "005930"
    assert body["last_close"] == 70_000.0
    assert body["rsi_14"] == 55.0
    assert body["as_of"] == "2026-05-29"


def test_features_bad_ticker_400(client: TestClient) -> None:
    r = client.get("/api/features/ABC")  # 6자리 숫자 아님
    assert r.status_code == 400
    assert "invalid ticker" in r.json()["detail"]


def test_features_unavailable_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_ticker: str) -> PriceSummary:
        raise ValueError("Not enough price data")

    monkeypatch.setattr(server, "compute_features", boom)
    r = client.get("/api/features/005930")
    assert r.status_code == 404
    assert "features unavailable" in r.json()["detail"]


def test_journal_missing_returns_empty(client: TestClient) -> None:
    r = client.get("/api/journal")
    assert r.status_code == 200
    body = r.json()
    assert body["exists"] is False
    assert body["markdown"] == ""
    assert body["date"]  # YYYY-MM-DD


def test_journal_existing_file(client: TestClient) -> None:
    recorder = server._journal_recorder()
    path = recorder.path_for()
    path.write_text("# Journal\nhello", encoding="utf-8")
    body = client.get("/api/journal").json()
    assert body["exists"] is True
    assert "hello" in body["markdown"]


def test_ohlcv_bad_ticker_400(client: TestClient) -> None:
    r = client.get("/api/ohlcv/12")  # 6자리 아님
    assert r.status_code == 400


def test_ohlcv_no_data_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pandas as pd

    import kr_ai_trader.data.prices as prices_mod

    monkeypatch.setattr(prices_mod, "get_ohlcv", lambda *_a, **_k: pd.DataFrame())
    # calendar.previous_business_day 는 pykrx fallback (weekday) 으로 네트워크 없음.
    r = client.get("/api/ohlcv/005930")
    assert r.status_code == 404
    assert "no ohlcv" in r.json()["detail"]


def test_ohlcv_happy(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import pandas as pd

    import kr_ai_trader.data.prices as prices_mod

    df = pd.DataFrame(
        {
            "open": [100.0, 110.0],
            "high": [120.0, 130.0],
            "low": [90.0, 95.0],
            "close": [115.0, 125.0],
            "volume": [1000, 2000],
        },
        index=pd.to_datetime(["2026-05-28", "2026-05-29"]),
    )
    monkeypatch.setattr(prices_mod, "get_ohlcv", lambda *_a, **_k: df)
    r = client.get("/api/ohlcv/005930?days=5")
    assert r.status_code == 200
    body = r.json()
    assert body["ticker"] == "005930"
    assert body["count"] == 2
    assert body["rows"][0]["close"] == 115.0
    assert body["rows"][-1]["volume"] == 2000


# ---------------------------------------------------------------------------
# WebSocket /ws/cycle
# ---------------------------------------------------------------------------


# 사이클을 종료시키는 터미널 이벤트. 정상 완료 시 서버는 ws.close() 를 명시 호출하지 않고
# 핸들러가 그냥 return 하므로, 클라이언트는 종단 이벤트를 보면 수신 루프를 멈춘다.
_TERMINAL_KINDS = frozenset({"cycle_done", "error"})


def _drain(ws: Any) -> list[dict[str, Any]]:
    """터미널 이벤트(cycle_done/error) 또는 소켓 종료까지 모든 이벤트를 수집."""
    from starlette.websockets import WebSocketDisconnect

    events: list[dict[str, Any]] = []
    try:
        while True:
            ev = ws.receive_json()
            events.append(ev)
            if ev.get("kind") in _TERMINAL_KINDS:
                break
    except WebSocketDisconnect:
        pass
    return events


def test_ws_cycle_no_action_sequence(client: TestClient) -> None:
    """제안 None -> settings_loaded -> features_computed -> moderator_started -> no_action -> cycle_done."""
    _StubModerator.proposal = None
    with client.websocket_connect("/ws/cycle") as ws:
        ws.send_json({"tickers": ["005930"], "cash": 5_000_000.0})
        events = _drain(ws)

    kinds = [e["kind"] for e in events]
    assert kinds == [
        "settings_loaded",
        "features_computed",
        "moderator_started",
        "no_action",
        "cycle_done",
    ]
    loaded = events[0]
    assert loaded["provider"] == "fake-provider"
    assert loaded["model"] == "fake-model"
    assert loaded["tickers"] == ["005930"]
    assert loaded["cash"] == 5_000_000.0
    assert events[-1]["final_cash"] == 5_000_000.0


def test_ws_cycle_order_placed_sequence(client: TestClient) -> None:
    """매수 제안 -> proposal_built -> risk_gate_decision -> order_placed -> cycle_done."""
    _StubModerator.proposal = TradeProposal(
        ticker="005930",
        side="buy",
        conviction=0.8,
        size_pct=2.0,
        thesis="저평가 + 모멘텀",
        risks=["변동성"],
        stop_loss_pct=5.0,
    )
    with client.websocket_connect("/ws/cycle") as ws:
        ws.send_json({"tickers": ["005930"], "cash": 10_000_000.0})
        events = _drain(ws)

    kinds = [e["kind"] for e in events]
    assert kinds == [
        "settings_loaded",
        "features_computed",
        "moderator_started",
        "proposal_built",
        "risk_gate_decision",
        "order_placed",
        "cycle_done",
    ]
    proposal_ev = events[3]
    assert proposal_ev["side"] == "buy"
    assert proposal_ev["conviction"] == 0.8
    risk_ev = events[4]
    assert risk_ev["accepted"] is True
    order_ev = events[5]
    assert order_ev["side"] == "buy"
    assert order_ev["quantity"] > 0
    done = events[-1]
    assert any(p["ticker"] == "005930" for p in done["final_positions"])


def test_ws_cycle_order_rejected_by_risk_gate(client: TestClient) -> None:
    """size_pct 가 max_position_pct 초과 -> risk_gate 거부 -> order_rejected."""
    _StubModerator.proposal = TradeProposal(
        ticker="005930",
        side="buy",
        conviction=0.9,
        size_pct=80.0,  # max_position_pct(기본 3%) 크게 초과
        thesis="과대 베팅",
        risks=["집중위험"],
        stop_loss_pct=5.0,
    )
    with client.websocket_connect("/ws/cycle") as ws:
        ws.send_json({"tickers": ["005930"], "cash": 10_000_000.0})
        events = _drain(ws)

    kinds = [e["kind"] for e in events]
    assert "risk_gate_decision" in kinds
    assert "order_rejected" in kinds
    assert "order_placed" not in kinds
    risk_ev = next(e for e in events if e["kind"] == "risk_gate_decision")
    assert risk_ev["accepted"] is False
    assert risk_ev["reasons"]  # 거부 사유 비어있지 않음


def test_ws_cycle_no_valid_tickers_error(client: TestClient) -> None:
    """6자리 숫자 ticker 가 하나도 없으면 error 이벤트 후 종료."""
    with client.websocket_connect("/ws/cycle") as ws:
        ws.send_json({"tickers": ["ABCDEF", "12"]})
        events = _drain(ws)

    assert len(events) == 1
    assert events[0]["kind"] == "error"
    assert "no valid" in events[0]["message"]


def test_ws_cycle_ticker_not_in_universe_skipped(client: TestClient) -> None:
    """유효 6자리지만 universe 밖이면 ticker_skipped 후 cycle_done."""
    with client.websocket_connect("/ws/cycle") as ws:
        ws.send_json({"tickers": ["999999"], "cash": 10_000_000.0})
        events = _drain(ws)

    kinds = [e["kind"] for e in events]
    assert kinds == ["settings_loaded", "ticker_skipped", "cycle_done"]
    assert events[1]["reason"] == "not in universe"


def test_ws_cycle_features_failed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """compute_features 예외 -> features_failed 후 다음 종목으로 진행 -> cycle_done."""

    def boom(_ticker: str) -> PriceSummary:
        raise ValueError("pykrx down")

    monkeypatch.setattr(server, "compute_features", boom)
    with client.websocket_connect("/ws/cycle") as ws:
        ws.send_json({"tickers": ["005930"], "cash": 10_000_000.0})
        events = _drain(ws)

    kinds = [e["kind"] for e in events]
    assert kinds == ["settings_loaded", "features_failed", "cycle_done"]
    assert "pykrx down" in events[1]["error"]


def test_ws_cycle_moderator_failed(client: TestClient) -> None:
    """moderator.decide 예외 -> moderator_failed 후 cycle_done."""
    _StubModerator.raise_exc = True
    with client.websocket_connect("/ws/cycle") as ws:
        ws.send_json({"tickers": ["005930"], "cash": 10_000_000.0})
        events = _drain(ws)

    kinds = [e["kind"] for e in events]
    assert kinds == [
        "settings_loaded",
        "features_computed",
        "moderator_started",
        "moderator_failed",
        "cycle_done",
    ]
    assert "moderator boom" in events[3]["error"]


def test_ws_cycle_cash_clamped_to_min(client: TestClient) -> None:
    """비정상적으로 작은 cash 는 _MIN_CASH 로 clamp."""
    with client.websocket_connect("/ws/cycle") as ws:
        ws.send_json({"tickers": ["005930"], "cash": 1.0})
        events = _drain(ws)

    assert events[0]["cash"] == server._MIN_CASH


def test_ws_cycle_invalid_cash_defaults(client: TestClient) -> None:
    """파싱 불가 cash 는 기본값(1천만)으로 대체."""
    with client.websocket_connect("/ws/cycle") as ws:
        ws.send_json({"tickers": ["005930"], "cash": "not-a-number"})
        events = _drain(ws)

    assert events[0]["cash"] == 10_000_000.0


def test_ws_cycle_default_ticker_when_missing(client: TestClient) -> None:
    """tickers 누락 시 기본 005930 사용."""
    with client.websocket_connect("/ws/cycle") as ws:
        ws.send_json({"cash": 10_000_000.0})
        events = _drain(ws)

    assert events[0]["tickers"] == ["005930"]


def test_make_idempotent_id_deterministic() -> None:
    """ws_cycle 이 의존하는 멱등키가 결정론적임을 직접 검증."""
    a = Order(
        client_order_id=server.Executor.make_idempotent_id("ui", "005930", "buy", 10),
        ticker="005930",
        side=OrderSide.buy,
        quantity=10,
    )
    b = Order(
        client_order_id=server.Executor.make_idempotent_id("ui", "005930", "buy", 10),
        ticker="005930",
        side=OrderSide.buy,
        quantity=10,
    )
    assert a.client_order_id == b.client_order_id
