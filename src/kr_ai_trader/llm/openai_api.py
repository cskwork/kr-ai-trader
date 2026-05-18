"""OpenAI API 백엔드. OPENAI_API_KEY 필요. GPT-5 계열은 response_format JSON schema 지원."""

from __future__ import annotations

from typing import Any

from openai import AsyncOpenAI

from .base import LLMError, LLMResponse, extract_json, validate_against_schema


class OpenAIAPIProvider:
    name = "openai_api"

    def __init__(self, *, api_key: str, model: str) -> None:
        if not api_key:
            raise LLMError("OPENAI_API_KEY missing")
        self.model = model
        self._client = AsyncOpenAI(api_key=api_key)

    async def propose_structured(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> LLMResponse:
        resp = await self._client.chat.completions.create(
            model=self.model,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "trade_proposal",
                    "strict": True,
                    "schema": schema,
                },
            },
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        text = resp.choices[0].message.content or ""
        data = extract_json(text)
        validate_against_schema(data, schema)
        return LLMResponse(
            raw_text=text,
            data=data,
            model=self.model,
            provider=self.name,
            usage={
                "prompt_tokens": resp.usage.prompt_tokens if resp.usage else 0,
                "completion_tokens": resp.usage.completion_tokens if resp.usage else 0,
            },
        )

    async def healthcheck(self) -> bool:
        try:
            await self._client.chat.completions.create(
                model=self.model,
                max_tokens=8,
                messages=[{"role": "user", "content": "ping"}],
            )
            return True
        except Exception:
            return False
