"""Typed tool definitions for Claude's tool-use protocol.

`Tool.to_anthropic()` renders the dict the Messages API expects.
`Tool.invoke(raw_args)` validates against the Pydantic input model and
dispatches. Tool return values must be strings (tool_result content).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    func: Callable[[BaseModel], str]

    def to_anthropic(self) -> dict[str, Any]:
        """Render this tool as an Anthropic `tools` list entry."""
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": schema,
        }

    def invoke(self, raw_args: dict[str, Any]) -> str:
        """Validate raw tool args against the input model, then run."""
        try:
            args = self.input_model.model_validate(raw_args)
        except ValidationError as e:
            raise ToolInputError(str(e)) from e
        return self.func(args)


class ToolInputError(Exception):
    """Raised when tool args fail Pydantic validation."""


def make_tool(
    name: str,
    description: str,
    input_model: type[BaseModel],
) -> Callable[[Callable[[BaseModel], str]], Tool]:
    """Decorator: wrap a function as a `Tool`."""

    def _wrap(func: Callable[[BaseModel], str]) -> Tool:
        return Tool(
            name=name,
            description=description,
            input_model=input_model,
            func=func,
        )

    return _wrap
