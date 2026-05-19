"""LLM 프로바이더 공통 인터페이스.

목표: 5종 백엔드(Anthropic API, OpenAI API, Claude Code CLI, Codex CLI, Ollama)를
동일한 시그니처로 사용. 모든 매매 신호 생성은 `propose_structured()` 한 메서드로 흐름.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

try:
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import ValidationError as _JSValidationError

    _JS_AVAILABLE = True
except ImportError:                                  # 옵셔널 — 미설치 시 fallback 사용
    _JS_AVAILABLE = False
    _JSValidationError = Exception                   # type: ignore[assignment]


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


def _iter_balanced_objects(text: str):
    """문자열을 좌→우로 스캔하며 균형 잡힌 최상위 {…} 블록을 yield.

    문자열 리터럴 내부의 { } 와 이스케이프 처리를 인식하므로 본문에 예시 JSON 이 섞여 있어도
    안전하게 마지막 객체를 선택할 수 있다.
    """
    depth = 0
    start = -1
    in_string = False
    escape = False
    for i, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start != -1:
                    yield text[start : i + 1]
                    start = -1


def extract_json(text: str) -> dict[str, Any]:
    """모델 응답에서 JSON 객체 추출.

    우선순위: 마지막 ```json fenced block → 마지막 균형잡힌 {…} 객체.
    LLM 이 본문에 예시 JSON 을 끼워 넣어도 최종 답변만 선택된다.
    """
    fences = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fences:
        candidate = fences[-1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass    # fall through to balanced scan

    last_obj: str | None = None
    for obj in _iter_balanced_objects(text):
        last_obj = obj
    if last_obj is None:
        raise LLMError(f"no JSON object in response: {text[:200]!r}")
    try:
        return json.loads(last_obj)
    except json.JSONDecodeError as e:
        raise LLMError(f"invalid JSON: {e}; text={text[:200]!r}") from e


def validate_against_schema(data: dict[str, Any], schema: dict[str, Any]) -> None:
    """엄격한 JSON Schema 검증.

    `jsonschema` 가 설치돼 있으면 Draft202012Validator 로 전체 검증(enum/minimum/maximum/
    additionalProperties/array.items 포함). 미설치 환경에선 경량 fallback (required + type) 만.
    """
    if _JS_AVAILABLE:
        try:
            Draft202012Validator(schema).validate(data)        # type: ignore[reportPossiblyUnbound]
        except _JSValidationError as e:                          # type: ignore[misc]
            path = "/".join(str(p) for p in getattr(e, "absolute_path", []))
            msg = getattr(e, "message", str(e))
            raise LLMError(f"schema violation at {path or '<root>'}: {msg}") from e
        return

    # fallback: 최소한의 필수/타입 검사
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
