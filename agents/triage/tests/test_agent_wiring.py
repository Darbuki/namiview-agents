"""End-to-end wiring test: system prompt + all 6 tools registered correctly.

No network, no cluster — both dependencies are mocked.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from namiview_triage.agent import build_triage_agent
from namiview_triage.tools.github import GitHubClient


def test_triage_agent_has_six_tools() -> None:
    agent = build_triage_agent(
        core=MagicMock(),
        gh=GitHubClient(token="fake"),
        client=MagicMock(),
    )
    names = {t.name for t in agent.tools}
    assert names == {
        "list_namespaces",
        "list_pods",
        "describe_pod",
        "get_pod_logs",
        "list_events",
        "open_triage_issue",
    }


def test_triage_agent_system_prompt_is_present() -> None:
    agent = build_triage_agent(
        core=MagicMock(),
        gh=GitHubClient(token="fake"),
        client=MagicMock(),
    )
    assert "namiview incident-triage agent" in agent.system_prompt
    assert "read-only" in agent.system_prompt
