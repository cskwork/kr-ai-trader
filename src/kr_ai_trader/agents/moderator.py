"""Bull / Bear / RiskOfficer 3에이전트 토론 + Moderator 합의.

TradingAgents 스타일의 경량 구현. 의견 불일치 시 무포지션이 디폴트.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal

from ..llm.base import LLMProvider
from .schemas import DEBATE_SCHEMA, PROPOSAL_SCHEMA

Side = Literal["buy", "sell", "hold"]


@dataclass(frozen=True)
class TradeProposal:
    ticker: str
    side: Side
    conviction: float
    size_pct: float
    thesis: str
    risks: list[str]
    stop_loss_pct: float | None = None


_BULL_SYSTEM = (
    "당신은 한국 주식 시장의 강세론자(bull) 애널리스트입니다. "
    "주어진 종목과 시장 컨텍스트를 분석해 매수 근거를 찾고, "
    "근거가 약하면 'hold' 로 솔직하게 응답하세요. 거래세 0.18% 와 슬리피지를 항상 고려."
)
_BEAR_SYSTEM = (
    "당신은 한국 주식 시장의 약세론자(bear) 애널리스트입니다. "
    "주어진 종목의 하방 리스크·매도 근거를 찾고, "
    "근거가 약하면 'hold' 로 응답하세요."
)
_RISK_SYSTEM = (
    "당신은 리스크 책임자입니다. Bull/Bear 의견을 검토해 포지션 사이즈와 손절선을 보수적으로 결정하세요. "
    "확신이 낮으면 verdict='reject'."
)


class Moderator:
    def __init__(self, llm: LLMProvider) -> None:
        self.llm = llm

    async def decide(self, *, ticker: str, market_context: str) -> TradeProposal | None:
        user_prompt = (
            f"종목코드: {ticker}\n\n"
            f"시장 컨텍스트:\n{market_context}\n\n"
            "위 정보를 바탕으로 매매 제안을 JSON 으로 출력하세요."
        )
        bull_task = self.llm.propose_structured(
            system=_BULL_SYSTEM, user=user_prompt, schema=PROPOSAL_SCHEMA
        )
        bear_task = self.llm.propose_structured(
            system=_BEAR_SYSTEM, user=user_prompt, schema=PROPOSAL_SCHEMA
        )
        bull, bear = await asyncio.gather(bull_task, bear_task, return_exceptions=True)

        debate_user = (
            f"종목: {ticker}\n\n"
            f"Bull 의견: {bull.data if not isinstance(bull, Exception) else f'(실패: {bull})'}\n\n"
            f"Bear 의견: {bear.data if not isinstance(bear, Exception) else f'(실패: {bear})'}\n\n"
            "두 의견을 종합해 최종 매매 제안과 진행 여부(proceed/reject)를 결정하세요."
        )
        debate = await self.llm.propose_structured(
            system=_RISK_SYSTEM, user=debate_user, schema=DEBATE_SCHEMA
        )
        if debate.data["verdict"] != "proceed":
            return None
        p = debate.data["agreed_proposal"]
        if p["side"] == "hold":
            return None
        return TradeProposal(
            ticker=p["ticker"],
            side=p["side"],
            conviction=float(p["conviction"]),
            size_pct=float(p["size_pct"]),
            thesis=p["thesis"],
            risks=list(p["risks"]),
            stop_loss_pct=p.get("stop_loss_pct"),
        )
