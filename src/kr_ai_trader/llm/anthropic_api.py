"""Anthropic Claude API 백엔드. ANTHROPIC_API_KEY 필요."""

from __future__ import annotations

from typing import Any

from anthropic import AsyncAnthropic
from anthropic.types import ToolParam

from .base import LLMError, LLMResponse, validate_against_schema


class AnthropicAPIProvider:
    name = "anthropic_api"

    def __init__(self, *, api_key: str, model: str) -> None:
        if not api_key:
            raise LLMError("ANTHROPIC_API_KEY missing")
        self.model = model
        self._client = AsyncAnthropic(api_key=api_key)

    async def propose_structured(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> LLMResponse:
        tool: ToolParam = {
            "name": "submit_proposal",
            "description": "Submit the trading proposal in the required JSON schema.",
            "input_schema": schema,
        }
        resp = await self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            tools=[tool],
            tool_choice={"type": "tool", "name": "submit_proposal"},
            messages=[{"role": "user", "content": user}],
        )
        for block in resp.content:
            if block.type == "tool_use" and block.name == "submit_proposal":
                raw = block.input
                if not isinstance(raw, dict):
                    raise LLMError(f"tool_use input not a dict: {type(raw).__name__}")
                data: dict[str, Any] = raw
                validate_against_schema(data, schema)
                return LLMResponse(
                    raw_text=str(block.input),
                    data=data,
                    model=self.model,
                    provider=self.name,
                    usage={
                        "input_tokens": resp.usage.input_tokens,
                        "output_tokens": resp.usage.output_tokens,
                    },
                )
        raise LLMError("Anthropic returned no tool_use block")

    async def healthcheck(self) -> bool:
        try:
            await self._client.messages.create(
                model=self.model,
                max_tokens=8,
                messages=[{"role": "user", "content": "ping"}],
            )
            return True
        except Exception:
            return False
