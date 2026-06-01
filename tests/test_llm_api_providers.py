"""HTTP/SDK LLM 프로바이더 테스트 — 실제 네트워크/SDK 의존 없음.

대상:
- ollama.OllamaProvider        : httpx.AsyncClient -> OLLAMA_HOST. respx 로 트랜스포트 모킹.
- anthropic_api.AnthropicAPIProvider : `anthropic` SDK 를 import. 미설치이므로 가짜 모듈 주입.
- openai_api.OpenAIAPIProvider : `openai` SDK 를 import. 미설치이므로 가짜 모듈 주입.

각 프로바이더 공통 검증: propose_structured() 가 PROPOSAL_SCHEMA 를 통과한 LLMResponse 반환,
정상/오류 응답 파싱, malformed/비-JSON -> LLMError, healthcheck() true/false, .name/.model 노출.
"""

from __future__ import annotations

import importlib
import json
import sys
import types
from typing import Any

import httpx
import pytest
import respx

from kr_ai_trader.agents.schemas import PROPOSAL_SCHEMA
from kr_ai_trader.llm.base import LLMError, LLMResponse

# 스키마를 통과하는 정상 매매 제안 페이로드 (모든 프로바이더 공용).
VALID_PROPOSAL: dict[str, Any] = {
    "ticker": "005930",
    "side": "buy",
    "conviction": 0.72,
    "size_pct": 2.5,
    "thesis": "메모리 업황 반등 + 밸류에이션 매력.",
    "risks": ["업황 회복 지연", "환율 급등"],
    "stop_loss_pct": 7.0,
}

# 스키마 위반 페이로드: conviction 범위 초과(1.0 < 1.5) -> validate_against_schema 가 LLMError.
SCHEMA_VIOLATING_PROPOSAL: dict[str, Any] = {**VALID_PROPOSAL, "conviction": 1.5}


# --------------------------------------------------------------------------- #
# Ollama (httpx 트랜스포트) — respx 로 OLLAMA_HOST 호출 모킹
# --------------------------------------------------------------------------- #
OLLAMA_HOST = "http://localhost:11434"


def _make_ollama() -> Any:
    from kr_ai_trader.llm.ollama import OllamaProvider

    return OllamaProvider(host=OLLAMA_HOST, model="qwen2.5:7b")


async def test_ollama_name_and_model() -> None:
    p = _make_ollama()
    try:
        assert p.name == "ollama"
        assert p.model == "qwen2.5:7b"
    finally:
        await p.aclose()


@respx.mock
async def test_ollama_propose_structured_happy_path() -> None:
    """정상 JSON content -> 스키마 통과한 LLMResponse."""
    route = respx.post(f"{OLLAMA_HOST}/api/chat").mock(
        return_value=httpx.Response(
            200,
            json={
                "message": {"role": "assistant", "content": json.dumps(VALID_PROPOSAL)},
                "prompt_eval_count": 120,
                "eval_count": 64,
            },
        )
    )
    p = _make_ollama()
    try:
        resp = await p.propose_structured(
            system="sys", user="usr", schema=PROPOSAL_SCHEMA
        )
    finally:
        await p.aclose()

    assert route.called
    assert isinstance(resp, LLMResponse)
    assert resp.data == VALID_PROPOSAL
    assert resp.provider == "ollama"
    assert resp.model == "qwen2.5:7b"
    assert resp.usage == {"prompt_tokens": 120, "completion_tokens": 64}


@respx.mock
async def test_ollama_malformed_non_json_raises() -> None:
    """JSON 도, 균형잡힌 {…} 객체도 없는 본문 -> LLMError."""
    respx.post(f"{OLLAMA_HOST}/api/chat").mock(
        return_value=httpx.Response(
            200, json={"message": {"content": "sorry, I cannot answer"}}
        )
    )
    p = _make_ollama()
    try:
        with pytest.raises(LLMError):
            await p.propose_structured(system="s", user="u", schema=PROPOSAL_SCHEMA)
    finally:
        await p.aclose()


@respx.mock
async def test_ollama_empty_content_raises() -> None:
    """content 가 빈 문자열이면 LLMError (빈 응답 경로)."""
    respx.post(f"{OLLAMA_HOST}/api/chat").mock(
        return_value=httpx.Response(200, json={"message": {"content": ""}})
    )
    p = _make_ollama()
    try:
        with pytest.raises(LLMError, match="empty response"):
            await p.propose_structured(system="s", user="u", schema=PROPOSAL_SCHEMA)
    finally:
        await p.aclose()


@respx.mock
async def test_ollama_http_error_raises() -> None:
    """비정상 HTTP status -> LLMError."""
    respx.post(f"{OLLAMA_HOST}/api/chat").mock(
        return_value=httpx.Response(500, text="boom")
    )
    p = _make_ollama()
    try:
        with pytest.raises(LLMError, match="HTTP 500"):
            await p.propose_structured(system="s", user="u", schema=PROPOSAL_SCHEMA)
    finally:
        await p.aclose()


@respx.mock
async def test_ollama_schema_violation_raises() -> None:
    """잘 형성된 JSON 이지만 스키마 위반 -> LLMError."""
    respx.post(f"{OLLAMA_HOST}/api/chat").mock(
        return_value=httpx.Response(
            200,
            json={"message": {"content": json.dumps(SCHEMA_VIOLATING_PROPOSAL)}},
        )
    )
    p = _make_ollama()
    try:
        with pytest.raises(LLMError):
            await p.propose_structured(system="s", user="u", schema=PROPOSAL_SCHEMA)
    finally:
        await p.aclose()


@respx.mock
async def test_ollama_healthcheck_true() -> None:
    respx.get(f"{OLLAMA_HOST}/api/tags").mock(
        return_value=httpx.Response(200, json={"models": []})
    )
    p = _make_ollama()
    try:
        assert await p.healthcheck() is True
    finally:
        await p.aclose()


@respx.mock
async def test_ollama_healthcheck_false_on_error() -> None:
    """연결 실패(트랜스포트 예외) -> healthcheck False (graceful degrade)."""
    respx.get(f"{OLLAMA_HOST}/api/tags").mock(
        side_effect=httpx.ConnectError("refused")
    )
    p = _make_ollama()
    try:
        assert await p.healthcheck() is False
    finally:
        await p.aclose()


# --------------------------------------------------------------------------- #
# Anthropic SDK — 가짜 `anthropic` / `anthropic.types` 모듈 주입
# --------------------------------------------------------------------------- #
class _FakeBlock:
    """Anthropic content block (tool_use 또는 text) 모사."""

    def __init__(self, *, type: str, name: str = "", input: Any = None) -> None:
        self.type = type
        self.name = name
        self.input = input


class _FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeMessage:
    def __init__(self, content: list[_FakeBlock]) -> None:
        self.content = content
        self.usage = _FakeUsage(40, 25)


class _FakeMessages:
    """messages.create 의 동작을 인스턴스 단위로 제어."""

    def __init__(self, owner: _FakeAsyncAnthropic) -> None:
        self._owner = owner

    async def create(self, **kwargs: Any) -> _FakeMessage:
        self._owner.last_kwargs = kwargs
        behavior = self._owner.behavior
        if callable(behavior):
            return behavior(kwargs)
        return behavior


class _FakeAsyncAnthropic:
    def __init__(self, *, api_key: str, **_: Any) -> None:
        self.api_key = api_key
        self.behavior: Any = None
        self.last_kwargs: dict[str, Any] = {}
        self.messages = _FakeMessages(self)


def _install_fake_anthropic(
    monkeypatch: pytest.MonkeyPatch,
) -> type[_FakeAsyncAnthropic]:
    """`from anthropic import AsyncAnthropic` / `from anthropic.types import ToolParam`
    가 가짜를 받도록 sys.modules 에 주입 후 프로바이더 모듈을 재로딩."""
    anthropic_mod = types.ModuleType("anthropic")
    anthropic_mod.AsyncAnthropic = _FakeAsyncAnthropic  # type: ignore[attr-defined]
    types_mod = types.ModuleType("anthropic.types")
    types_mod.ToolParam = dict  # type: ignore[attr-defined]
    anthropic_mod.types = types_mod  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", anthropic_mod)
    monkeypatch.setitem(sys.modules, "anthropic.types", types_mod)
    monkeypatch.delitem(sys.modules, "kr_ai_trader.llm.anthropic_api", raising=False)
    return _FakeAsyncAnthropic


def _make_anthropic(monkeypatch: pytest.MonkeyPatch, behavior: Any) -> Any:
    _install_fake_anthropic(monkeypatch)
    mod = importlib.import_module("kr_ai_trader.llm.anthropic_api")
    provider = mod.AnthropicAPIProvider(api_key="sk-test", model="claude-x")
    provider._client.behavior = behavior  # type: ignore[attr-defined]
    return provider


def test_anthropic_missing_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_anthropic(monkeypatch)
    mod = importlib.import_module("kr_ai_trader.llm.anthropic_api")
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY missing"):
        mod.AnthropicAPIProvider(api_key="", model="claude-x")


def test_anthropic_name_and_model(monkeypatch: pytest.MonkeyPatch) -> None:
    p = _make_anthropic(monkeypatch, behavior=None)
    assert p.name == "anthropic_api"
    assert p.model == "claude-x"


async def test_anthropic_propose_structured_happy_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """tool_use 블록의 input 이 스키마 통과 -> LLMResponse."""
    msg = _FakeMessage(
        [_FakeBlock(type="tool_use", name="submit_proposal", input=dict(VALID_PROPOSAL))]
    )
    p = _make_anthropic(monkeypatch, behavior=msg)
    resp = await p.propose_structured(system="sys", user="usr", schema=PROPOSAL_SCHEMA)

    assert isinstance(resp, LLMResponse)
    assert resp.data == VALID_PROPOSAL
    assert resp.provider == "anthropic_api"
    assert resp.model == "claude-x"
    assert resp.usage == {"input_tokens": 40, "output_tokens": 25}
    # tool_choice 가 submit_proposal 로 강제됐는지 확인.
    assert p._client.last_kwargs["tool_choice"]["name"] == "submit_proposal"


async def test_anthropic_no_tool_use_block_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """tool_use 블록이 없으면(예: text 만) LLMError (빈/누락 경로)."""
    msg = _FakeMessage([_FakeBlock(type="text", input="just text")])
    p = _make_anthropic(monkeypatch, behavior=msg)
    with pytest.raises(LLMError, match="no tool_use block"):
        await p.propose_structured(system="s", user="u", schema=PROPOSAL_SCHEMA)


async def test_anthropic_non_dict_input_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """tool_use input 이 dict 가 아니면(malformed) LLMError."""
    msg = _FakeMessage(
        [_FakeBlock(type="tool_use", name="submit_proposal", input="not-a-dict")]
    )
    p = _make_anthropic(monkeypatch, behavior=msg)
    with pytest.raises(LLMError, match="not a dict"):
        await p.propose_structured(system="s", user="u", schema=PROPOSAL_SCHEMA)


async def test_anthropic_schema_violation_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    msg = _FakeMessage(
        [
            _FakeBlock(
                type="tool_use",
                name="submit_proposal",
                input=dict(SCHEMA_VIOLATING_PROPOSAL),
            )
        ]
    )
    p = _make_anthropic(monkeypatch, behavior=msg)
    with pytest.raises(LLMError):
        await p.propose_structured(system="s", user="u", schema=PROPOSAL_SCHEMA)


async def test_anthropic_healthcheck_true(monkeypatch: pytest.MonkeyPatch) -> None:
    p = _make_anthropic(monkeypatch, behavior=_FakeMessage([]))
    assert await p.healthcheck() is True


async def test_anthropic_healthcheck_false_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create 가 예외를 던지면 healthcheck False (graceful degrade)."""

    def boom(_kwargs: dict[str, Any]) -> _FakeMessage:
        raise RuntimeError("api down")

    p = _make_anthropic(monkeypatch, behavior=boom)
    assert await p.healthcheck() is False


# --------------------------------------------------------------------------- #
# OpenAI SDK — 가짜 `openai` 모듈 주입
# --------------------------------------------------------------------------- #
class _FakeChatMessage:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str | None) -> None:
        self.message = _FakeChatMessage(content)


class _FakeCompletionUsage:
    def __init__(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _FakeCompletion:
    def __init__(self, content: str | None, usage: _FakeCompletionUsage | None) -> None:
        self.choices = [_FakeChoice(content)]
        self.usage = usage


class _FakeCompletions:
    def __init__(self, owner: _FakeAsyncOpenAI) -> None:
        self._owner = owner

    async def create(self, **kwargs: Any) -> _FakeCompletion:
        self._owner.last_kwargs = kwargs
        behavior = self._owner.behavior
        if callable(behavior):
            return behavior(kwargs)
        return behavior


class _FakeChat:
    def __init__(self, owner: _FakeAsyncOpenAI) -> None:
        self.completions = _FakeCompletions(owner)


class _FakeAsyncOpenAI:
    def __init__(self, *, api_key: str, **_: Any) -> None:
        self.api_key = api_key
        self.behavior: Any = None
        self.last_kwargs: dict[str, Any] = {}
        self.chat = _FakeChat(self)


def _install_fake_openai(monkeypatch: pytest.MonkeyPatch) -> type[_FakeAsyncOpenAI]:
    openai_mod = types.ModuleType("openai")
    openai_mod.AsyncOpenAI = _FakeAsyncOpenAI  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", openai_mod)
    monkeypatch.delitem(sys.modules, "kr_ai_trader.llm.openai_api", raising=False)
    return _FakeAsyncOpenAI


def _make_openai(monkeypatch: pytest.MonkeyPatch, behavior: Any) -> Any:
    _install_fake_openai(monkeypatch)
    mod = importlib.import_module("kr_ai_trader.llm.openai_api")
    provider = mod.OpenAIAPIProvider(api_key="sk-test", model="gpt-5")
    provider._client.behavior = behavior  # type: ignore[attr-defined]
    return provider


def test_openai_missing_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_openai(monkeypatch)
    mod = importlib.import_module("kr_ai_trader.llm.openai_api")
    with pytest.raises(LLMError, match="OPENAI_API_KEY missing"):
        mod.OpenAIAPIProvider(api_key="", model="gpt-5")


def test_openai_name_and_model(monkeypatch: pytest.MonkeyPatch) -> None:
    p = _make_openai(monkeypatch, behavior=None)
    assert p.name == "openai_api"
    assert p.model == "gpt-5"


async def test_openai_propose_structured_happy_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """response_format JSON content -> extract_json -> 스키마 통과 LLMResponse."""
    completion = _FakeCompletion(
        content=json.dumps(VALID_PROPOSAL),
        usage=_FakeCompletionUsage(prompt_tokens=200, completion_tokens=80),
    )
    p = _make_openai(monkeypatch, behavior=completion)
    resp = await p.propose_structured(system="sys", user="usr", schema=PROPOSAL_SCHEMA)

    assert isinstance(resp, LLMResponse)
    assert resp.data == VALID_PROPOSAL
    assert resp.provider == "openai_api"
    assert resp.model == "gpt-5"
    assert resp.usage == {"prompt_tokens": 200, "completion_tokens": 80}
    # json_schema 강제 전송 확인.
    assert p._client.last_kwargs["response_format"]["type"] == "json_schema"


async def test_openai_usage_none_yields_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """usage 가 None 이어도 0 으로 안전 처리 (누락 데이터 경로)."""
    completion = _FakeCompletion(content=json.dumps(VALID_PROPOSAL), usage=None)
    p = _make_openai(monkeypatch, behavior=completion)
    resp = await p.propose_structured(system="s", user="u", schema=PROPOSAL_SCHEMA)
    assert resp.usage == {"prompt_tokens": 0, "completion_tokens": 0}


async def test_openai_empty_content_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """content 가 None -> 빈 문자열 -> extract_json 이 JSON 없음으로 LLMError."""
    completion = _FakeCompletion(content=None, usage=None)
    p = _make_openai(monkeypatch, behavior=completion)
    with pytest.raises(LLMError, match="no JSON object"):
        await p.propose_structured(system="s", user="u", schema=PROPOSAL_SCHEMA)


async def test_openai_malformed_non_json_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """비-JSON 텍스트 -> LLMError."""
    completion = _FakeCompletion(content="not json at all", usage=None)
    p = _make_openai(monkeypatch, behavior=completion)
    with pytest.raises(LLMError):
        await p.propose_structured(system="s", user="u", schema=PROPOSAL_SCHEMA)


async def test_openai_schema_violation_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completion = _FakeCompletion(
        content=json.dumps(SCHEMA_VIOLATING_PROPOSAL), usage=None
    )
    p = _make_openai(monkeypatch, behavior=completion)
    with pytest.raises(LLMError):
        await p.propose_structured(system="s", user="u", schema=PROPOSAL_SCHEMA)


async def test_openai_healthcheck_true(monkeypatch: pytest.MonkeyPatch) -> None:
    p = _make_openai(monkeypatch, behavior=_FakeCompletion("ok", None))
    assert await p.healthcheck() is True


async def test_openai_healthcheck_false_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(_kwargs: dict[str, Any]) -> _FakeCompletion:
        raise RuntimeError("api down")

    p = _make_openai(monkeypatch, behavior=boom)
    assert await p.healthcheck() is False
