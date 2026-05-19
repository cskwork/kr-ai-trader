"""LLM JSON 추출·검증 — provider 독립."""

from __future__ import annotations

import pytest

from kr_ai_trader.llm.base import LLMError, extract_json, validate_against_schema


def test_extract_plain_json() -> None:
    assert extract_json('{"a": 1, "b": "x"}') == {"a": 1, "b": "x"}


def test_extract_json_inside_code_fence() -> None:
    text = "Sure!\n```json\n{\"x\": 42}\n```\n"
    assert extract_json(text) == {"x": 42}


def test_extract_json_inside_generic_fence() -> None:
    text = "```\n{\"y\": [1,2,3]}\n```"
    assert extract_json(text) == {"y": [1, 2, 3]}


def test_extract_failure_when_no_object() -> None:
    with pytest.raises(LLMError):
        extract_json("nothing useful here")


def test_validate_required_fields_present() -> None:
    schema = {
        "type": "object",
        "required": ["a"],
        "properties": {"a": {"type": "string"}},
    }
    validate_against_schema({"a": "hello"}, schema)


def test_validate_missing_required_raises() -> None:
    schema = {
        "type": "object",
        "required": ["a"],
        "properties": {"a": {"type": "string"}},
    }
    with pytest.raises(LLMError) as exc:
        validate_against_schema({"b": 1}, schema)
    assert "missing required field" in str(exc.value)


def test_validate_wrong_type_raises() -> None:
    schema = {
        "type": "object",
        "required": ["n"],
        "properties": {"n": {"type": "number"}},
    }
    with pytest.raises(LLMError):
        validate_against_schema({"n": "not a number"}, schema)


def test_validate_number_accepts_int_and_float() -> None:
    schema = {
        "type": "object",
        "required": ["n"],
        "properties": {"n": {"type": "number"}},
    }
    validate_against_schema({"n": 1}, schema)
    validate_against_schema({"n": 1.5}, schema)
