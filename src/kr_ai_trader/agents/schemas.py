"""에이전트 입출력 JSON Schema. 모든 LLM 응답은 여기서 정의."""

from __future__ import annotations

PROPOSAL_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ticker", "side", "conviction", "size_pct", "thesis", "risks"],
    "properties": {
        "ticker": {
            "type": "string",
            "description": "한국 상장 종목코드 (6자리 숫자). 유니버스 화이트리스트에 있는 것만.",
        },
        "side": {"type": "string", "enum": ["buy", "sell", "hold"]},
        "conviction": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "description": "신뢰도 0.0-1.0. 0.6 미만은 hold 권장.",
        },
        "size_pct": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 10.0,
            "description": "포트폴리오 대비 비중 %. 리스크 게이트가 다시 한 번 한도 검사.",
        },
        "thesis": {"type": "string", "description": "1-2문장 매매 근거."},
        "risks": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "description": "이 매매가 틀릴 수 있는 시나리오 최소 1개.",
        },
        "stop_loss_pct": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 30.0,
        },
    },
}


DEBATE_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "rationale", "agreed_proposal"],
    "properties": {
        "verdict": {"type": "string", "enum": ["proceed", "reject"]},
        "rationale": {"type": "string"},
        "agreed_proposal": PROPOSAL_SCHEMA,
    },
}
