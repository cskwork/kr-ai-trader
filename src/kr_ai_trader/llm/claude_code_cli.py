"""Claude Code CLI 백엔드 — API 키 불필요, OAuth 세션 사용.

`claude --print --output-format json --json-schema <schema>` 가
응답을 스키마 검증해 envelope 의 `structured_output` 필드에 박아준다.
실패 시 `result` 텍스트에서 JSON 추출로 폴백.

요구: `claude` PATH 존재 + `claude login` 사전 완료.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from typing import Any

from .base import LLMError, LLMResponse, extract_json, validate_against_schema


class ClaudeCodeCLIProvider:
    """Claude Code CLI 헤드리스 실행 래퍼."""

    name = "claude_code_cli"

    def __init__(
        self,
        *,
        bin_path: str = "claude",
        model: str | None = None,
        bare: bool = False,
        timeout_seconds: float = 120.0,
    ) -> None:
        if shutil.which(bin_path) is None:
            raise LLMError(
                f"`{bin_path}` not found in PATH. Install Claude Code and run `claude login`."
            )
        self.bin_path = bin_path
        self.model = model or "haiku"
        self.bare = bare
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
            f"{system}\n\n---\n\n{user}\n\n"
            "Respond as a single JSON object that matches the provided schema. "
            "No prose, no code fences."
        )
        args = [
            self.bin_path,
            "--print",
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(schema, ensure_ascii=False),
            "--model",
            self.model,
            "--no-session-persistence",
        ]
        if self.bare:
            args.append("--bare")
        args.append(prompt)

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout_seconds
            )
        except asyncio.TimeoutError as exc:
            proc.kill()
            raise LLMError(f"claude CLI timed out after {self.timeout_seconds}s") from exc

        if proc.returncode != 0:
            raise LLMError(
                f"claude CLI exited {proc.returncode}: {stderr.decode(errors='replace')[:500]}"
            )

        text = stdout.decode(errors="replace")
        try:
            envelope = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError(f"claude CLI emitted non-JSON envelope: {text[:300]}") from exc

        data = envelope.get("structured_output")
        if not isinstance(data, dict):
            result_text = envelope.get("result") or ""
            data = extract_json(result_text)
        validate_against_schema(data, schema)

        model_usage = envelope.get("modelUsage") or {}
        used_model = next(iter(model_usage.keys()), self.model) if model_usage else self.model

        return LLMResponse(
            raw_text=envelope.get("result") or text,
            data=data,
            model=used_model,
            provider=self.name,
        )

    async def healthcheck(self) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                self.bin_path,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=10.0)
            return proc.returncode == 0
        except Exception:
            return False
