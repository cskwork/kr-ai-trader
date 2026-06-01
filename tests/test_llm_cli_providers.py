"""CLI 기반 LLM 프로바이더 테스트 — 실제 subprocess/네트워크 없음.

대상: ClaudeCodeCLIProvider (envelope.structured_output 파싱),
      CodexCLIProvider (NDJSON 파싱).
경계 모킹: shutil.which (PATH 확인) + asyncio.create_subprocess_exec (프로세스 기동).
가짜 프로세스가 crafted stdout/stderr/returncode 를 돌려주도록 주입한다.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from kr_ai_trader.llm import claude_code_cli, codex_cli
from kr_ai_trader.llm.base import LLMError, LLMResponse

# 두 프로바이더가 공통으로 쓰는 단순 스키마.
_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["buy", "sell", "hold"]},
        "confidence": {"type": "number"},
    },
    "required": ["action", "confidence"],
    "additionalProperties": False,
}


class _FakeProc:
    """asyncio.create_subprocess_exec 가 돌려줄 가짜 프로세스.

    communicate() 가 미리 정한 stdout/stderr 를 돌려주고, returncode 를 노출한다.
    timeout 테스트를 위해 communicate 를 무한 대기시키는 옵션도 둔다.
    """

    def __init__(
        self,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
        hang: bool = False,
    ) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self._hang = hang
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        if self._hang:
            await asyncio.sleep(3600)  # wait_for 가 먼저 타임아웃
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True


def _patch_subprocess(
    monkeypatch: pytest.MonkeyPatch, module: Any, proc: _FakeProc
) -> dict[str, Any]:
    """대상 모듈의 asyncio.create_subprocess_exec 를 가짜 프로세스로 대체.

    호출 시 전달된 argv 를 captured["args"] 에 기록해 인자 구성 검증에 쓴다.
    """
    captured: dict[str, Any] = {}

    async def _fake_exec(*args: Any, **kwargs: Any) -> _FakeProc:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return proc

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", _fake_exec)
    return captured


@pytest.fixture
def _which_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """양쪽 모듈의 shutil.which 가 항상 바이너리를 찾은 것으로 만든다."""
    monkeypatch.setattr(claude_code_cli.shutil, "which", lambda _b: "/usr/bin/fake")
    monkeypatch.setattr(codex_cli.shutil, "which", lambda _b: "/usr/bin/fake")


# --------------------------------------------------------------------------- #
# 생성자 — PATH 미존재 시 즉시 LLMError
# --------------------------------------------------------------------------- #
def test_claude_ctor_raises_when_bin_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claude_code_cli.shutil, "which", lambda _b: None)
    with pytest.raises(LLMError, match="not found in PATH"):
        claude_code_cli.ClaudeCodeCLIProvider(bin_path="claude")


def test_codex_ctor_raises_when_bin_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex_cli.shutil, "which", lambda _b: None)
    with pytest.raises(LLMError, match="not found in PATH"):
        codex_cli.CodexCLIProvider(bin_path="codex")


# --------------------------------------------------------------------------- #
# ClaudeCodeCLIProvider — happy / fallback / empty / error
# --------------------------------------------------------------------------- #
async def test_claude_envelope_structured_output(
    monkeypatch: pytest.MonkeyPatch, _which_ok: None
) -> None:
    """envelope.structured_output 가 dict 면 그대로 채택, modelUsage 첫 키를 모델로."""
    envelope = {
        "result": "irrelevant prose",
        "structured_output": {"action": "buy", "confidence": 0.8},
        "modelUsage": {"claude-3-5-haiku": {"input": 10}},
    }
    captured = _patch_subprocess(
        monkeypatch,
        claude_code_cli,
        _FakeProc(stdout=json.dumps(envelope).encode()),
    )
    provider = claude_code_cli.ClaudeCodeCLIProvider()

    resp = await provider.propose_structured(system="sys", user="usr", schema=_SCHEMA)

    assert isinstance(resp, LLMResponse)
    assert resp.data == {"action": "buy", "confidence": 0.8}
    assert resp.provider == "claude_code_cli"
    assert resp.model == "claude-3-5-haiku"  # modelUsage 첫 키
    # argv 가 --json-schema 와 프롬프트를 포함하는지 확인
    assert "--json-schema" in captured["args"]
    assert captured["args"][-1].startswith("sys")


async def test_claude_falls_back_to_result_text_json(
    monkeypatch: pytest.MonkeyPatch, _which_ok: None
) -> None:
    """structured_output 가 없으면 result 텍스트에서 JSON 추출."""
    envelope = {
        "result": 'prose then {"action": "hold", "confidence": 0.1} tail',
        "modelUsage": {},
    }
    _patch_subprocess(
        monkeypatch,
        claude_code_cli,
        _FakeProc(stdout=json.dumps(envelope).encode()),
    )
    provider = claude_code_cli.ClaudeCodeCLIProvider(model="haiku")

    resp = await provider.propose_structured(system="s", user="u", schema=_SCHEMA)

    assert resp.data == {"action": "hold", "confidence": 0.1}
    assert resp.model == "haiku"  # modelUsage 비어 self.model 사용


async def test_claude_non_json_envelope_raises(
    monkeypatch: pytest.MonkeyPatch, _which_ok: None
) -> None:
    """stdout 가 JSON 이 아니면 LLMError (envelope 파싱 실패)."""
    _patch_subprocess(
        monkeypatch, claude_code_cli, _FakeProc(stdout=b"not json at all <<<")
    )
    provider = claude_code_cli.ClaudeCodeCLIProvider()

    with pytest.raises(LLMError, match="non-JSON envelope"):
        await provider.propose_structured(system="s", user="u", schema=_SCHEMA)


async def test_claude_schema_violation_raises(
    monkeypatch: pytest.MonkeyPatch, _which_ok: None
) -> None:
    """structured_output 가 스키마 위반(enum 밖)이면 LLMError."""
    envelope = {"structured_output": {"action": "explode", "confidence": 0.5}}
    _patch_subprocess(
        monkeypatch, claude_code_cli, _FakeProc(stdout=json.dumps(envelope).encode())
    )
    provider = claude_code_cli.ClaudeCodeCLIProvider()

    with pytest.raises(LLMError, match="schema violation"):
        await provider.propose_structured(system="s", user="u", schema=_SCHEMA)


async def test_claude_nonzero_exit_raises(
    monkeypatch: pytest.MonkeyPatch, _which_ok: None
) -> None:
    """프로세스가 비정상 종료하면 stderr 를 담아 LLMError."""
    _patch_subprocess(
        monkeypatch,
        claude_code_cli,
        _FakeProc(stderr=b"boom: not logged in", returncode=1),
    )
    provider = claude_code_cli.ClaudeCodeCLIProvider()

    with pytest.raises(LLMError, match="exited 1"):
        await provider.propose_structured(system="s", user="u", schema=_SCHEMA)


async def test_claude_timeout_raises_and_kills(
    monkeypatch: pytest.MonkeyPatch, _which_ok: None
) -> None:
    """communicate 가 응답 안 하면 wait_for 타임아웃 → kill + LLMError."""
    proc = _FakeProc(hang=True)
    _patch_subprocess(monkeypatch, claude_code_cli, proc)
    provider = claude_code_cli.ClaudeCodeCLIProvider(timeout_seconds=0.01)

    with pytest.raises(LLMError, match="timed out"):
        await provider.propose_structured(system="s", user="u", schema=_SCHEMA)
    assert proc.killed is True


async def test_claude_healthcheck_true_on_zero_exit(
    monkeypatch: pytest.MonkeyPatch, _which_ok: None
) -> None:
    _patch_subprocess(monkeypatch, claude_code_cli, _FakeProc(returncode=0))
    provider = claude_code_cli.ClaudeCodeCLIProvider()
    assert await provider.healthcheck() is True


async def test_claude_healthcheck_false_on_error(
    monkeypatch: pytest.MonkeyPatch, _which_ok: None
) -> None:
    async def _explode(*_a: Any, **_k: Any) -> _FakeProc:
        raise OSError("cannot spawn")

    monkeypatch.setattr(claude_code_cli.asyncio, "create_subprocess_exec", _explode)
    provider = claude_code_cli.ClaudeCodeCLIProvider()
    assert await provider.healthcheck() is False


# --------------------------------------------------------------------------- #
# CodexCLIProvider — NDJSON happy / mid-event skip / garbage / empty
# --------------------------------------------------------------------------- #
async def test_codex_ndjson_last_message_parsed(
    monkeypatch: pytest.MonkeyPatch, _which_ok: None
) -> None:
    """NDJSON 에서 중간 이벤트는 건너뛰고 마지막 message 의 JSON 본문 채택."""
    inner = {"action": "sell", "confidence": 0.42}
    lines = [
        json.dumps({"type": "thought", "content": "thinking..."}),
        json.dumps({"type": "tool_call", "content": "ignored"}),
        json.dumps({"type": "message", "message": json.dumps(inner)}),
    ]
    captured = _patch_subprocess(
        monkeypatch,
        codex_cli,
        _FakeProc(stdout=("\n".join(lines) + "\n").encode()),
    )
    provider = codex_cli.CodexCLIProvider()

    resp = await provider.propose_structured(system="sys", user="usr", schema=_SCHEMA)

    assert resp.data == inner
    assert resp.provider == "codex_cli"
    assert resp.model == "gpt-5"
    assert "exec" in captured["args"]
    assert "--json" in captured["args"]


async def test_codex_response_field_as_str(
    monkeypatch: pytest.MonkeyPatch, _which_ok: None
) -> None:
    """payload 가 response 키의 문자열이면 그 안에서 JSON 추출."""
    line = json.dumps(
        {"type": "message", "response": 'final: {"action": "buy", "confidence": 1.0}'}
    )
    _patch_subprocess(monkeypatch, codex_cli, _FakeProc(stdout=(line + "\n").encode()))
    provider = codex_cli.CodexCLIProvider()

    resp = await provider.propose_structured(system="s", user="u", schema=_SCHEMA)

    assert resp.data == {"action": "buy", "confidence": 1.0}


async def test_codex_garbage_lines_raise(
    monkeypatch: pytest.MonkeyPatch, _which_ok: None
) -> None:
    """모든 줄이 비 JSON 이면 last_text=원문 → extract_json 실패 → LLMError."""
    _patch_subprocess(
        monkeypatch,
        codex_cli,
        _FakeProc(stdout=b"garbage line one\nstill garbage two\n"),
    )
    provider = codex_cli.CodexCLIProvider()

    with pytest.raises(LLMError, match="no JSON object"):
        await provider.propose_structured(system="s", user="u", schema=_SCHEMA)


async def test_codex_schema_violation_raises(
    monkeypatch: pytest.MonkeyPatch, _which_ok: None
) -> None:
    """파싱은 됐지만 required 필드 누락 → 스키마 위반 LLMError."""
    line = json.dumps({"type": "message", "message": json.dumps({"action": "buy"})})
    _patch_subprocess(monkeypatch, codex_cli, _FakeProc(stdout=(line + "\n").encode()))
    provider = codex_cli.CodexCLIProvider()

    with pytest.raises(LLMError, match="schema violation"):
        await provider.propose_structured(system="s", user="u", schema=_SCHEMA)


async def test_codex_nonzero_exit_raises(
    monkeypatch: pytest.MonkeyPatch, _which_ok: None
) -> None:
    _patch_subprocess(
        monkeypatch, codex_cli, _FakeProc(stderr=b"auth required", returncode=2)
    )
    provider = codex_cli.CodexCLIProvider()

    with pytest.raises(LLMError, match="exited 2"):
        await provider.propose_structured(system="s", user="u", schema=_SCHEMA)


async def test_codex_prompt_too_large_raises(
    monkeypatch: pytest.MonkeyPatch, _which_ok: None
) -> None:
    """프롬프트가 argv 한계(32KB)를 넘으면 subprocess 기동 전에 LLMError."""
    spawned = {"called": False}

    async def _should_not_spawn(*_a: Any, **_k: Any) -> _FakeProc:
        spawned["called"] = True
        return _FakeProc()

    monkeypatch.setattr(codex_cli.asyncio, "create_subprocess_exec", _should_not_spawn)
    provider = codex_cli.CodexCLIProvider()

    with pytest.raises(LLMError, match="too large"):
        await provider.propose_structured(
            system="x" * 33_000, user="u", schema=_SCHEMA
        )
    assert spawned["called"] is False


async def test_codex_timeout_raises_and_kills(
    monkeypatch: pytest.MonkeyPatch, _which_ok: None
) -> None:
    proc = _FakeProc(hang=True)
    _patch_subprocess(monkeypatch, codex_cli, proc)
    # timeout_seconds 는 int 시그니처지만 실제 wait_for 에 float 로 전달되므로 짧게 둔다.
    provider = codex_cli.CodexCLIProvider(timeout_seconds=0)

    with pytest.raises(LLMError, match="timed out"):
        await provider.propose_structured(system="s", user="u", schema=_SCHEMA)
    assert proc.killed is True


async def test_codex_healthcheck_true_on_zero_exit(
    monkeypatch: pytest.MonkeyPatch, _which_ok: None
) -> None:
    _patch_subprocess(monkeypatch, codex_cli, _FakeProc(returncode=0))
    provider = codex_cli.CodexCLIProvider()
    assert await provider.healthcheck() is True


async def test_codex_healthcheck_false_on_error(
    monkeypatch: pytest.MonkeyPatch, _which_ok: None
) -> None:
    async def _explode(*_a: Any, **_k: Any) -> _FakeProc:
        raise OSError("cannot spawn")

    monkeypatch.setattr(codex_cli.asyncio, "create_subprocess_exec", _explode)
    provider = codex_cli.CodexCLIProvider()
    assert await provider.healthcheck() is False
