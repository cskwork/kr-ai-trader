"""Moderator.decide 3에이전트 토론 합의 로직 테스트. 실제 LLM 호출 없음.

FakeLLMProvider 가 system 프롬프트로 Bull/Bear/RiskOfficer 응답을 분기해서 캔드
LLMResponse 를 돌려준다. decide() 의 합의·거부·실패 short-circuit·malformed 파싱
예외 경로를 검증하고, market_context 4000자 클램프가 user 프롬프트에 적용됐는지 확인.
"""

from __future__ import annotations

from typing import Any

from kr_ai_trader.agents.moderator import Moderator, TradeProposal
from kr_ai_trader.agents.schemas import DEBATE_SCHEMA, PROPOSAL_SCHEMA
from kr_ai_trader.llm.base import LLMResponse

# moderator.py 가 system 프롬프트로 역할을 분기하므로, 식별용 시그니처 토막.
_BULL_TAG = "강세론자(bull)"
_BEAR_TAG = "약세론자(bear)"
_RISK_TAG = "리스크 책임자"


def _resp(data: dict[str, Any]) -> LLMResponse:
    """캔드 LLMResponse 생성 헬퍼."""
    return LLMResponse(
        raw_text="<faked>",
        data=data,
        model="fake-model",
        provider="fake",
    )


def _proposal_data(side: str = "buy") -> dict[str, Any]:
    """PROPOSAL_SCHEMA 를 만족하는 정상 제안 dict."""
    return {
        "ticker": "005930",
        "side": side,
        "conviction": 0.72,
        "size_pct": 2.0,
        "thesis": "계명 2 안전마진 충족, PER 10 저평가.",
        "risks": ["반도체 다운사이클 리스크"],
        "stop_loss_pct": 5.0,
    }


class FakeLLMProvider:
    """LLMProvider 프로토콜 구현. system 프롬프트로 응답을 분기하는 가짜 LLM.

    `responses` 는 역할 키('bull'/'bear'/'risk') -> LLMResponse | Exception.
    Exception 이면 해당 호출에서 raise 해 실패 경로를 시뮬레이션한다.
    모든 호출 인자를 `calls` 에 기록해 프롬프트 클램프 등을 검증할 수 있게 한다.
    """

    name = "fake"
    model = "fake-model"

    def __init__(self, responses: dict[str, LLMResponse | Exception]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def _role(self, system: str) -> str:
        if _BULL_TAG in system:
            return "bull"
        if _BEAR_TAG in system:
            return "bear"
        if _RISK_TAG in system:
            return "risk"
        raise AssertionError(f"unexpected system prompt: {system[:60]!r}")

    async def propose_structured(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> LLMResponse:
        role = self._role(system)
        self.calls.append({"role": role, "system": system, "user": user, "schema": schema})
        result = self.responses[role]
        if isinstance(result, Exception):
            raise result
        return result

    async def healthcheck(self) -> bool:
        return True


async def test_proceed_returns_trade_proposal() -> None:
    """Bull/Bear 성공 + verdict='proceed' + non-hold agreed_proposal -> TradeProposal 반환."""
    agreed = _proposal_data(side="buy")
    provider = FakeLLMProvider(
        {
            "bull": _resp(_proposal_data(side="buy")),
            "bear": _resp(_proposal_data(side="sell")),
            "risk": _resp(
                {"verdict": "proceed", "rationale": "계명 1 보존 OK", "agreed_proposal": agreed}
            ),
        }
    )
    mod = Moderator(provider)

    result = await mod.decide(ticker="005930", market_context="{}")

    assert isinstance(result, TradeProposal)
    assert result.ticker == "005930"
    assert result.side == "buy"
    assert result.conviction == 0.72
    assert result.size_pct == 2.0
    assert result.thesis == "계명 2 안전마진 충족, PER 10 저평가."
    assert result.risks == ["반도체 다운사이클 리스크"]
    assert result.stop_loss_pct == 5.0
    # 정확히 3회 호출: bull, bear, risk.
    assert [c["role"] for c in provider.calls] == ["bull", "bear", "risk"]
    # bull/bear 에는 PROPOSAL_SCHEMA, risk 에는 DEBATE_SCHEMA 가 전달돼야 한다.
    assert provider.calls[0]["schema"] is PROPOSAL_SCHEMA
    assert provider.calls[2]["schema"] is DEBATE_SCHEMA


async def test_verdict_reject_returns_none() -> None:
    """RiskOfficer verdict='reject' -> None."""
    provider = FakeLLMProvider(
        {
            "bull": _resp(_proposal_data(side="buy")),
            "bear": _resp(_proposal_data(side="sell")),
            "risk": _resp(
                {
                    "verdict": "reject",
                    "rationale": "계명 6 사이징 위배",
                    "agreed_proposal": _proposal_data(side="buy"),
                }
            ),
        }
    )

    result = await Moderator(provider).decide(ticker="005930", market_context="{}")

    assert result is None


async def test_agreed_proposal_hold_returns_none() -> None:
    """agreed_proposal.side='hold' -> None (무포지션 디폴트)."""
    provider = FakeLLMProvider(
        {
            "bull": _resp(_proposal_data(side="hold")),
            "bear": _resp(_proposal_data(side="hold")),
            "risk": _resp(
                {
                    "verdict": "proceed",
                    "rationale": "확신 부족",
                    "agreed_proposal": _proposal_data(side="hold"),
                }
            ),
        }
    )

    result = await Moderator(provider).decide(ticker="005930", market_context="{}")

    assert result is None


async def test_agreed_proposal_missing_returns_none() -> None:
    """agreed_proposal 이 dict 가 아니면(누락) -> None."""
    provider = FakeLLMProvider(
        {
            "bull": _resp(_proposal_data(side="buy")),
            "bear": _resp(_proposal_data(side="sell")),
            "risk": _resp({"verdict": "proceed", "rationale": "no proposal", "agreed_proposal": None}),
        }
    )

    result = await Moderator(provider).decide(ticker="005930", market_context="{}")

    assert result is None


async def test_both_sides_raise_short_circuits_to_none() -> None:
    """Bull/Bear 둘 다 예외 -> 토론 진입 없이 None (short-circuit). RiskOfficer 미호출."""
    provider = FakeLLMProvider(
        {
            "bull": RuntimeError("bull provider down"),
            "bear": RuntimeError("bear provider down"),
            "risk": _resp(
                {
                    "verdict": "proceed",
                    "rationale": "should never be reached",
                    "agreed_proposal": _proposal_data(side="buy"),
                }
            ),
        }
    )

    result = await Moderator(provider).decide(ticker="005930", market_context="{}")

    assert result is None
    # short-circuit: risk 역할은 호출되면 안 된다.
    assert [c["role"] for c in provider.calls] == ["bull", "bear"]


async def test_one_side_raises_still_proceeds_to_debate() -> None:
    """한쪽(Bull)만 실패해도 다른쪽(Bear) 성공 시 토론 진행 -> proceed 면 제안 반환."""
    agreed = _proposal_data(side="sell")
    provider = FakeLLMProvider(
        {
            "bull": RuntimeError("bull provider down"),
            "bear": _resp(_proposal_data(side="sell")),
            "risk": _resp(
                {"verdict": "proceed", "rationale": "bear 단독 근거 채택", "agreed_proposal": agreed}
            ),
        }
    )

    result = await Moderator(provider).decide(ticker="005930", market_context="{}")

    assert isinstance(result, TradeProposal)
    assert result.side == "sell"
    # 실패한 Bull 의견은 "(실패: RuntimeError)" 로 토론 user 프롬프트에 들어가야 한다.
    risk_call = next(c for c in provider.calls if c["role"] == "risk")
    assert "(실패: RuntimeError)" in risk_call["user"]


async def test_malformed_agreed_proposal_returns_none_via_except() -> None:
    """agreed_proposal 에 필수 키 누락/타입 불량 -> except 경로로 None."""
    # side 는 통과시키되(buy), conviction 을 float 로 변환 불가능한 값으로 -> ValueError.
    malformed = {
        "ticker": "005930",
        "side": "buy",
        "conviction": "높음",  # float("높음") -> ValueError
        "size_pct": 2.0,
        "thesis": "x",
        "risks": ["r"],
    }
    provider = FakeLLMProvider(
        {
            "bull": _resp(_proposal_data(side="buy")),
            "bear": _resp(_proposal_data(side="sell")),
            "risk": _resp(
                {"verdict": "proceed", "rationale": "bad payload", "agreed_proposal": malformed}
            ),
        }
    )

    result = await Moderator(provider).decide(ticker="005930", market_context="{}")

    assert result is None


async def test_malformed_missing_key_returns_none_via_except() -> None:
    """agreed_proposal 에 필수 키(thesis) 자체가 없으면 KeyError -> None."""
    malformed = {
        "ticker": "005930",
        "side": "buy",
        "conviction": 0.7,
        "size_pct": 2.0,
        # thesis 누락
        "risks": ["r"],
    }
    provider = FakeLLMProvider(
        {
            "bull": _resp(_proposal_data(side="buy")),
            "bear": _resp(_proposal_data(side="sell")),
            "risk": _resp(
                {"verdict": "proceed", "rationale": "missing key", "agreed_proposal": malformed}
            ),
        }
    )

    result = await Moderator(provider).decide(ticker="005930", market_context="{}")

    assert result is None


async def test_market_context_clamped_to_4000_chars() -> None:
    """4000자 초과 market_context 는 잘려서 user 프롬프트에 들어가야 한다."""
    long_ctx = "X" * 5000  # 4000자 초과
    provider = FakeLLMProvider(
        {
            "bull": _resp(_proposal_data(side="buy")),
            "bear": _resp(_proposal_data(side="sell")),
            "risk": _resp(
                {
                    "verdict": "proceed",
                    "rationale": "ok",
                    "agreed_proposal": _proposal_data(side="buy"),
                }
            ),
        }
    )

    await Moderator(provider).decide(ticker="005930", market_context=long_ctx)

    bull_user = provider.calls[0]["user"]
    # 정확히 4000자만 살아남고 5000자 전체는 들어가면 안 된다.
    assert "X" * 4000 in bull_user
    assert "X" * 4001 not in bull_user
    assert long_ctx not in bull_user
