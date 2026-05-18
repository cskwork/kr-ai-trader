"""LLM 프로바이더 공통 인터페이스.

목표: 5종 백엔드(Anthropic API, OpenAI API, Claude Code CLI, Codex CLI, Ollama)를
동일한 시그니처로 사용. 모든 매매 신호 생성은 `propose_structured()` 한 메서드로 흐름.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


class LLMError(RuntimeError):
    """LLM 호출 실패 또는 응답 파싱 실패."""


@dataclass(frozen=True)
class LLMResponse:
    raw_text: str
    data: dict[str, Any]      # JSON Schema 통과한 파싱 결과
    model: str
    provider: str
    usage: dict[str, int] | None = None


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    model: str

    async def propose_structured(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> LLMResponse:
        """system+user 프롬프트 + JSON Schema → schema 통과한 dict 반환."""
        ...

    async def healthcheck(self) -> bool:
        """프로바이더 연결 가능 여부. 스모크 테스트용."""
        ...


def extract_json(text: str) -> dict[str, Any]:
    """모델 응답에서 JSON 객체 추출. ```json ... ``` 코드펜스도 처리."""
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise LLMError(f"no JSON object in response: {text[:200]!r}")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        raise LLMError(f"invalid JSON: {e}; text={text[:200]!r}") from e


def validate_against_schema(data: dict[str, Any], schema: dict[str, Any]) -> None:
    """필수 필드만 검사하는 경량 검증. 본격 검증은 jsonschema 패키지를 별도 도입.

    schema['required'] 와 schema['properties'] 의 type 만 체크.
    """
    required: list[str] = schema.get("required", [])
    for key in required:
        if key not in data:
            raise LLMError(f"missing required field: {key}")
    props: dict[str, Any] = schema.get("properties", {})
    type_map = {
        "string": str,
        "number": (int, float),
        "integer": int,
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    for key, val in data.items():
        if key not in props:
            continue
        expected = props[key].get("type")
        if expected is None:
            continue
        py_type = type_map.get(expected)
        if py_type and not isinstance(val, py_type):
            raise LLMError(f"field {key!r} expected {expected}, got {type(val).__name__}")
