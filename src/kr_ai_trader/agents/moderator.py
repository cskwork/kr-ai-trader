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


_PRINCIPLES = """투자 의사결정 10대 계명 (cskwork/investment-agent-rules):
1. Capital Preservation First — 영구적 손실 회피가 1번. (Buffett)
2. Margin of Safety — 내재가치 대비 충분한 할인 없으면 보류. (Graham)
3. Circle of Competence — 모르는 비즈니스에 베팅 금지. (Buffett, Lynch)
4. Second-Level Thinking — 컨센서스는 이미 가격에 반영. (Marks, Munger)
5. Cycles & Reflexivity — 과열·과매도 국면 인식, 자기참조 피드백 경계. (Marks, Soros)
6. Position Sizing & Asymmetric R:R — 손절거리 기반 사이징, 비대칭 보상 추구. (Druckenmiller)
7. Concentration vs Diversification — 확신엔 집중, 무지엔 분산. (Buffett, Dalio)
8. Long-term Compounding — 단기 트레이드보다 복리 효과 우선. (Munger)
9. Process Over Outcome — 검증된 시그널만, 결과론 금지. (Simons, Greenblatt)
10. Behavioral Discipline — 자기 심리가 가장 큰 적. (Graham Mr.Market, Munger)
"""

_BULL_SYSTEM = (
    "당신은 한국 주식 시장의 강세론자(bull) 애널리스트입니다. "
    "주어진 종목과 시장 컨텍스트를 분석해 매수 근거를 찾되, **10대 계명을 우선 적용**하세요. "
    "근거가 약하면 'hold'. 거래세(코스피 0.05% / 코스닥 0.20%, 2026 적용)·슬리피지·시간선택 비용 고려. "
    "thesis 에 어떤 계명(번호)을 적용했는지 명시. "
    "RSI>70 또는 20일 +30% 등 과열 신호(계명 5) 시 진입 보수화 또는 hold.\n\n" + _PRINCIPLES
)
_BEAR_SYSTEM = (
    "당신은 한국 주식 시장의 약세론자(bear) 애널리스트입니다. "
    "하방 리스크·매도 근거를 찾되, **10대 계명을 우선**. "
    "근거가 약하면 'hold'. 공매도는 무포지션 시 불가. thesis 에 적용 계명 명시.\n\n" + _PRINCIPLES
)
_RISK_SYSTEM = (
    "당신은 리스크 책임자입니다. Bull/Bear 의견을 검토해 포지션 사이즈와 손절선을 결정. "
    "**Capital Preservation(계명 1) 이 최우선**. Margin of Safety(2)·Position Sizing(6)·"
    "Behavioral Discipline(10) 위배 시 verdict='reject'. "
    "확신 0.6 미만 또는 size_pct>3 또는 stop_loss_pct>10 이면 reject. "
    "rationale 에 어떤 계명에 따라 결정했는지 명시.\n\n" + _PRINCIPLES
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
