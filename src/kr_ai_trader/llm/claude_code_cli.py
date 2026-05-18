"""Claude Code CLI 백엔드.

`claude` CLI의 로그인된 OAuth 세션을 활용 → **API 키 불필요**.
헤드리스 모드 `claude -p <prompt> --output-format json` 으로 호출.

요구: `claude --version` 동작, `claude login` 사전 완료.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from typing import Any

from .base import LLMError, LLMResponse, extract_json, validate_against_schema

_JSON_INSTRUCTION = (
    "\n\n응답은 반드시 아래 JSON Schema 를 따르는 단일 JSON 객체로만 출력하세요. "
    "코드펜스, 설명, 머리말 없이 JSON 본문만.\n"
    "JSON Schema:\n{schema}\n"
)


class ClaudeCodeCLIProvider:
    name = "claude_code_cli"

    def __init__(self, *, bin_path: str = "claude", model: str | None = None) -> None:
        if shutil.which(bin_path) is None:
            raise LLMError(
                f"`{bin_path}` not found in PATH. Install Claude Code and run `claude login`."
            )
        self.bin_path = bin_path
        self.model = model or "claude-sonnet-4-6"

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
        # --print: 헤드리스, --output-format json: 구조화 출력, --model: 모델 강제
        args = [
            self.bin_path,
            "--print",
            "--output-format",
            "json",
            "--model",
            self.model,
            prompt,
        ]
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise LLMError(
                f"claude CLI exited {proc.returncode}: {stderr.decode(errors='replace')[:500]}"
            )
        # claude --output-format json 결과 envelope: { "result": "...", "session_id": ..., ... }
        try:
            envelope = json.loads(stdout.decode())
            text = envelope.get("result") or envelope.get("response") or stdout.decode()
        except json.JSONDecodeError:
            text = stdout.decode()
        data = extract_json(text)
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
