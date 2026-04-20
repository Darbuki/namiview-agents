"""Unit tests for the hand-rolled tool-use loop.

We mock the Anthropic client so no network calls happen. Each mocked
response is a `types.SimpleNamespace` that quacks like an `anthropic.types.Message`
for the fields we read (`content`, `stop_reason`, `usage`).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from namiview_shared.agent import Agent, AgentError
from namiview_shared.tool import make_tool


def _usage(in_tokens: int = 10, out_tokens: int = 5) -> SimpleNamespace:
    return SimpleNamespace(
        input_tokens=in_tokens,
        output_tokens=out_tokens,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )


def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(name: str, tool_id: str, args: dict) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=tool_id, name=name, input=args)


def _make_response(content: list, stop_reason: str) -> SimpleNamespace:
    return SimpleNamespace(content=content, stop_reason=stop_reason, usage=_usage())


class _AddArgs(BaseModel):
    a: int
    b: int


@make_tool("add", "Add two integers.", _AddArgs)
def _add(args: _AddArgs) -> str:
    return str(args.a + args.b)


class _BoomArgs(BaseModel):
    pass


@make_tool("boom", "Always raises.", _BoomArgs)
def _boom(_: _BoomArgs) -> str:
    raise RuntimeError("kaboom")


def _agent_with(responses: list, tools: list | None = None) -> tuple[Agent, MagicMock]:
    client = MagicMock()
    client.messages.create.side_effect = responses
    agent = Agent(
        system_prompt="test system",
        tools=tools if tools is not None else [_add],
        client=client,
        max_iterations=5,
    )
    return agent, client


def test_single_turn_no_tools() -> None:
    resp = _make_response([_text_block("hello")], stop_reason="end_turn")
    agent, client = _agent_with([resp])

    run = agent.run("say hi")

    assert run.iterations == 1
    assert run.stop_reason == "end_turn"
    assert client.messages.create.call_count == 1
    # First (and only) call should carry the system + tools + initial user message.
    call = client.messages.create.call_args
    assert call.kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert call.kwargs["tools"][0]["name"] == "add"
    assert call.kwargs["messages"] == [{"role": "user", "content": "say hi"}]


def test_tool_use_then_end_turn() -> None:
    tool_turn = _make_response(
        [_tool_use_block("add", "toolu_1", {"a": 2, "b": 3})],
        stop_reason="tool_use",
    )
    final_turn = _make_response(
        [_text_block("result is 5")], stop_reason="end_turn"
    )
    agent, client = _agent_with([tool_turn, final_turn])

    run = agent.run("what is 2+3?")

    assert run.iterations == 2
    assert run.stop_reason == "end_turn"
    assert client.messages.create.call_count == 2

    # Second call's messages should contain: user prompt, assistant tool_use,
    # user tool_result (content=5).
    second_call_messages = client.messages.create.call_args_list[1].kwargs["messages"]
    assert len(second_call_messages) == 3
    tool_result_msg = second_call_messages[2]
    assert tool_result_msg["role"] == "user"
    assert tool_result_msg["content"][0]["type"] == "tool_result"
    assert tool_result_msg["content"][0]["tool_use_id"] == "toolu_1"
    assert tool_result_msg["content"][0]["content"] == "5"
    assert "is_error" not in tool_result_msg["content"][0]


def test_tool_error_propagates_as_is_error() -> None:
    tool_turn = _make_response(
        [_tool_use_block("boom", "toolu_x", {})],
        stop_reason="tool_use",
    )
    final_turn = _make_response(
        [_text_block("acknowledged failure")], stop_reason="end_turn"
    )
    agent, client = _agent_with([tool_turn, final_turn], tools=[_boom])

    run = agent.run("trigger boom")

    assert run.stop_reason == "end_turn"
    second_messages = client.messages.create.call_args_list[1].kwargs["messages"]
    tool_result = second_messages[2]["content"][0]
    assert tool_result["is_error"] is True
    assert "kaboom" in tool_result["content"]


def test_unknown_tool_is_error() -> None:
    tool_turn = _make_response(
        [_tool_use_block("nonesuch", "toolu_y", {})],
        stop_reason="tool_use",
    )
    final_turn = _make_response([_text_block("ok")], stop_reason="end_turn")
    agent, client = _agent_with([tool_turn, final_turn])

    agent.run("call a missing tool")

    second_messages = client.messages.create.call_args_list[1].kwargs["messages"]
    tool_result = second_messages[2]["content"][0]
    assert tool_result["is_error"] is True
    assert "not registered" in tool_result["content"]


def test_max_iterations_exhausted_raises() -> None:
    # Infinite tool_use responses — loop should bail after max_iterations.
    responses = [
        _make_response(
            [_tool_use_block("add", f"toolu_{i}", {"a": 1, "b": 1})],
            stop_reason="tool_use",
        )
        for i in range(10)
    ]
    agent, _ = _agent_with(responses)
    agent.max_iterations = 3

    with pytest.raises(AgentError, match="max_iterations"):
        agent.run("loop forever")
