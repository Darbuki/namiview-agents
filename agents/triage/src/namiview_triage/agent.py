"""Triage agent wiring."""

from __future__ import annotations

from typing import TYPE_CHECKING

from namiview_shared import Agent, build_client

from .prompt import SYSTEM_PROMPT
from .tools import build_github_tools, build_k8s_tools

if TYPE_CHECKING:
    from anthropic import Anthropic
    from kubernetes.client import CoreV1Api

    from .tools.github import GitHubClient


def build_triage_agent(
    core: CoreV1Api,
    gh: GitHubClient,
    client: Anthropic | None = None,
) -> Agent:
    tools = [*build_k8s_tools(core), *build_github_tools(gh)]
    return Agent(
        system_prompt=SYSTEM_PROMPT,
        tools=tools,
        client=client if client is not None else build_client(),
    )
