"""LLM 백엔드 동작 확인 스크립트.

- 설정된 provider 의 `healthcheck()` 호출
- 더미 매매 제안 1회 (PROPOSAL_SCHEMA) 받아 콘솔 출력
- 종료 코드 0 = 정상, 1 = 실패

usage:
    python -m scripts.smoke_llm
    LLM_PROVIDER=ollama python -m scripts.smoke_llm
"""

from __future__ import annotations

import asyncio
import json
import sys

from kr_ai_trader.agents.schemas import PROPOSAL_SCHEMA
from kr_ai_trader.config import get_settings
from kr_ai_trader.llm.factory import get_llm


async def main() -> int:
    settings = get_settings()
    llm = get_llm(settings)
    print(f"[smoke_llm] provider={llm.name} model={llm.model}")

    ok = await llm.healthcheck()
    print(f"[smoke_llm] healthcheck={'OK' if ok else 'FAIL'}")
    if not ok:
        return 1

    resp = await llm.propose_structured(
        system=(
            "당신은 한국 주식 트레이더입니다. 주어진 데이터로 매매 제안을 JSON 으로 출력하세요. "
            "신뢰도가 낮으면 side='hold'."
        ),
        user=(
            "종목 005930 (삼성전자). 종가 70000원. 5일 +2%, 20일 +5%. RSI 55.\n"
            "PROPOSAL_SCHEMA 에 맞춰 응답하세요."
        ),
        schema=PROPOSAL_SCHEMA,
    )
    print("[smoke_llm] proposal:")
    print(json.dumps(resp.data, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
