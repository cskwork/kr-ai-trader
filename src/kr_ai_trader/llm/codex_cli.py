"""OpenAI Codex CLI 백엔드.

`codex` CLI 로그인된 세션 활용 → **API 키 불필요**.
헤드리스 모드 `codex exec <prompt>` 호출.

요구: `codex --version` 동작, `codex login` 또는 ChatGPT 계정 연결.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
from typing import Any

from .base import LLMError, LLMResponse, extract_json, validate_against_schema

_JSON_INSTRUCTION = (
    "\n\n응답은 반드시 아래 JSON Schema 를 따르는 단일 JSON 객체로만 출력하세요. "
    "코드펜스, 설명, 머리말 없이 JSON 본문만.\n"
    "JSON Schema:\n{schema}\n"
)


class CodexCLIProvider:
    name = "codex_cli"

    DEFAULT_TIMEOUT_SEC = 120

    def __init__(
        self,
        *,
        bin_path: str = "codex",
        model: str | None = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        if shutil.which(bin_path) is None:
            raise LLMError(
                f"`{bin_path}` not found in PATH. Install OpenAI Codex CLI and run `codex login`."
            )
        self.bin_path = bin_path
        self.model = model or "gpt-5"
        self.timeout_seconds = timeout_seconds

    async def propose_structured(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> LLMResponse:
        prompt = (
            f"<system>\n{system}\n</system>\n\n"
            f"<user>\n{user}\n</user>"
            + _JSON_INSTRUCTION.format(schema=json.dumps(schema, ensure_ascii=False))
        )
        # codex exec: 헤드리스, --model: 모델 강제, --json: 구조화 envelope.
        # 프롬프트는 argv 로 전달되어 ps 노출 위험 있음 → 길이 제한.
        if len(prompt) > 32_000:
            raise LLMError("codex prompt too large for argv transport (>32KB)")
        args = [self.bin_path, "exec", "--model", self.model, "--json", prompt]
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout_seconds
            )
        except asyncio.TimeoutError as e:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            raise LLMError(f"codex CLI timed out after {self.timeout_seconds}s") from e
        if proc.returncode != 0:
            raise LLMError(
                f"codex CLI exited {proc.returncode}: {stderr.decode('utf-8', errors='replace')[:500]}"
            )
        text = stdout.decode("utf-8", errors="replace")
        # codex --json 은 NDJSON 형태로 여러 줄 출력 → 마지막 메시지(message/response/content) 찾기
        last_text = text
        for line in reversed(text.strip().splitlines()):
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            # tool_call / thought 등 중간 이벤트는 건너뛰고, 최종 텍스트 필드만 채택.
            event_type = obj.get("type") or obj.get("event")
            if event_type in {"tool_call", "tool_result", "thought", "reasoning"}:
                continue
            payload = obj.get("response") or obj.get("content") or obj.get("message")
            if payload:
                last_text = payload if isinstance(payload, str) else json.dumps(payload)
                break
        data = extract_json(last_text if isinstance(last_text, str) else json.dumps(last_text))
        validate_against_schema(data, schema)
        return LLMResponse(raw_text=text, data=data, model=self.model, provider=self.name)

    async def healthcheck(self) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                self.bin_path,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            return proc.returncode == 0
        except Exception:
            return False
