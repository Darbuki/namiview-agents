"""Tool-use loop for Claude.

Keep `system_prompt` and `tools` stable across turns to hit the prompt
cache — `cache_control: ephemeral` is attached to the last system block.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from anthropic import Anthropic
from anthropic.types import Message

from .claude import DEFAULT_MAX_TOKENS, DEFAULT_MODEL, build_client
from .logging import get_logger
from .tool import Tool, ToolInputError

log = get_logger(__name__)


class AgentError(Exception):
    """Raised when the agent loop can't make progress."""


@dataclass
class AgentRun:
    """Record of a single agent run, for inspection/testing."""

    final_message: Message
    iterations: int
    messages: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str | None = None


@dataclass
class Agent:
    """A Claude agent that iterates a tool-use loop to completion.

    Not thread-safe. Create a new Agent per concurrent run.
    """

    system_prompt: str
    tools: list[Tool]
    client: Anthropic = field(default_factory=build_client)
    model: str = DEFAULT_MODEL
    max_tokens: int = DEFAULT_MAX_TOKENS
    max_iterations: int = 25

    def _tool_by_name(self, name: str) -> Tool | None:
        for t in self.tools:
            if t.name == name:
                return t
        return None

    def _system_blocks(self) -> list[dict[str, Any]]:
        """System prompt as a single cached block."""
        return [
            {
                "type": "text",
                "text": self.system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    def _tool_defs(self) -> list[dict[str, Any]]:
        return [t.to_anthropic() for t in self.tools]

    def run(self, user_prompt: str) -> AgentRun:
        """Run the loop until Claude returns `end_turn` (or we hit a limit)."""
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": user_prompt},
        ]

        last_response: Message | None = None
        for iteration in range(1, self.max_iterations + 1):
            log.info("agent.turn.request", iteration=iteration, messages=len(messages))
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=self._system_blocks(),
                tools=self._tool_defs(),
                # Shallow copy — `messages` gets mutated below.
                messages=list(messages),
            )
            last_response = response
            log.info(
                "agent.turn.response",
                iteration=iteration,
                stop_reason=response.stop_reason,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                cache_read=getattr(response.usage, "cache_read_input_tokens", 0),
                cache_write=getattr(response.usage, "cache_creation_input_tokens", 0),
            )

            # Preserve the full content list; tool_use blocks must round-trip
            # untouched or the next request will 400.
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                return AgentRun(
                    final_message=response,
                    iterations=iteration,
                    messages=messages,
                    stop_reason=response.stop_reason,
                )

            if response.stop_reason == "pause_turn":
                continue

            if response.stop_reason == "tool_use":
                tool_results = self._execute_tool_calls(response)
                messages.append({"role": "user", "content": tool_results})
                continue

            # max_tokens / refusal / stop_sequence — terminal.
            log.warning(
                "agent.turn.unexpected_stop",
                stop_reason=response.stop_reason,
            )
            return AgentRun(
                final_message=response,
                iterations=iteration,
                messages=messages,
                stop_reason=response.stop_reason,
            )

        assert last_response is not None
        raise AgentError(
            f"agent hit max_iterations={self.max_iterations} without end_turn"
        )

    def _execute_tool_calls(self, response: Message) -> list[dict[str, Any]]:
        """Run every `tool_use` block in the response, return `tool_result` blocks."""
        results: list[dict[str, Any]] = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            result = self._execute_one(block.name, block.id, block.input)
            results.append(result)
        return results

    def _execute_one(
        self,
        tool_name: str,
        tool_use_id: str,
        raw_args: Any,
    ) -> dict[str, Any]:
        tool = self._tool_by_name(tool_name)
        if tool is None:
            log.error("agent.tool.unknown", tool=tool_name)
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": f"Error: tool '{tool_name}' is not registered.",
                "is_error": True,
            }

        args = raw_args if isinstance(raw_args, dict) else {}
        try:
            output = tool.invoke(args)
        except ToolInputError as e:
            log.warning("agent.tool.bad_input", tool=tool_name, error=str(e))
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": f"Invalid tool input: {e}",
                "is_error": True,
            }
        except Exception as e:  # noqa: BLE001
            log.exception("agent.tool.error", tool=tool_name)
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": f"Tool raised {type(e).__name__}: {e}",
                "is_error": True,
            }

        log.info("agent.tool.ok", tool=tool_name, output_len=len(output))
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": output,
        }
