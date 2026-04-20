"""Unit tests for the Tool wrapper."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from namiview_shared.tool import ToolInputError, make_tool


class _EchoArgs(BaseModel):
    message: str
    shout: bool = False


@make_tool("echo", "Echo the message back.", _EchoArgs)
def _echo(args: _EchoArgs) -> str:
    return args.message.upper() if args.shout else args.message


def test_to_anthropic_shape() -> None:
    spec = _echo.to_anthropic()
    assert spec["name"] == "echo"
    assert spec["description"] == "Echo the message back."
    schema = spec["input_schema"]
    assert schema["type"] == "object"
    assert "message" in schema["properties"]
    assert "shout" in schema["properties"]
    assert schema["required"] == ["message"]
    assert "title" not in schema


def test_invoke_valid() -> None:
    assert _echo.invoke({"message": "hi"}) == "hi"
    assert _echo.invoke({"message": "hi", "shout": True}) == "HI"


def test_invoke_rejects_bad_args() -> None:
    with pytest.raises(ToolInputError):
        _echo.invoke({})  # missing required `message`
