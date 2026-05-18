"""Ollama 로컬 LLM 백엔드.

`docker compose up -d ollama && ollama pull <model>` 후 사용.
format=json 옵션으로 strict JSON 출력 강제.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from .base import LLMError, LLMResponse, extract_json, validate_against_schema


class OllamaProvider:
    name = "ollama"

    def __init__(self, *, host: str = "http://localhost:11434", model: str) -> None:
        self.host = host.rstrip("/")
        self.model = model
        self._client = httpx.AsyncClient(base_url=self.host, timeout=120.0)

    async def propose_structured(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> LLMResponse:
        # Ollama 0.5+ 는 format 에 JSON schema 직접 지원
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "format": schema,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        r = await self._client.post("/api/chat", json=payload)
        if r.status_code != 200:
            raise LLMError(f"ollama HTTP {r.status_code}: {r.text[:500]}")
        body = r.json()
        text = body.get("message", {}).get("content", "")
        if not text:
            raise LLMError(f"ollama empty response: {body}")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = extract_json(text)
        validate_against_schema(data, schema)
        return LLMResponse(
            raw_text=text,
            data=data,
            model=self.model,
            provider=self.name,
            usage={
                "prompt_tokens": body.get("prompt_eval_count", 0),
                "completion_tokens": body.get("eval_count", 0),
            },
        )

    async def healthcheck(self) -> bool:
        try:
            r = await self._client.get("/api/tags")
            return r.status_code == 200
        except Exception:
            return False

    async def aclose(self) -> None:
        await self._client.aclose()
