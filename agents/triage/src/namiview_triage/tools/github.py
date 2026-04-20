"""GitHub issue-creation tool — the agent's only write path.

Auth via `GITHUB_TOKEN`. The `triage` label is always applied.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, Field

from namiview_shared import Tool, make_tool

DEFAULT_REPO = "Darbuki/namiview"
REQUIRED_LABEL = "triage"
TIMEOUT_SECONDS = 30.0


@dataclass
class GitHubClient:
    """Repo-scoped GitHub REST client. Tests inject fakes with the same shape."""

    token: str
    repo: str = DEFAULT_REPO
    base_url: str = "https://api.github.com"
    timeout: float = TIMEOUT_SECONDS

    def create_issue(
        self, title: str, body: str, labels: list[str]
    ) -> dict:
        url = f"{self.base_url}/repos/{self.repo}/issues"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "namiview-triage-agent",
        }
        payload = {"title": title, "body": body, "labels": labels}
        with httpx.Client(timeout=self.timeout) as c:
            r = c.post(url, headers=headers, json=payload)
        if r.status_code >= 400:
            raise RuntimeError(
                f"GitHub API {r.status_code}: {r.text[:500]}"
            )
        return r.json()


def from_env(repo: str = DEFAULT_REPO) -> GitHubClient:
    """Build a GitHubClient from `$GITHUB_TOKEN`."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is not set.")
    return GitHubClient(token=token, repo=repo)


class _OpenIssueArgs(BaseModel):
    title: str = Field(
        description="One-line issue title summarising the incident.",
        min_length=5,
        max_length=200,
    )
    body: str = Field(
        description=(
            "Full issue body in GitHub-flavoured markdown. Include: symptoms, "
            "evidence gathered (kubectl output snippets), hypothesis, and any "
            "remediation the on-call should consider. Do NOT claim to have "
            "fixed anything — this agent is read-only against the cluster."
        ),
        min_length=20,
    )
    extra_labels: list[str] = Field(
        default_factory=list,
        description="Optional labels to add in addition to 'triage' (always applied).",
    )


def build_github_tools(gh: GitHubClient) -> list[Tool]:
    """Build the GitHub tool list bound to a specific GitHubClient."""

    @make_tool(
        "open_triage_issue",
        (
            f"Open a GitHub issue on {gh.repo} with the triage label. "
            "Use this exactly ONCE at the end of triage, summarising findings. "
            "Returns the issue URL."
        ),
        _OpenIssueArgs,
    )
    def open_triage_issue(args: _OpenIssueArgs) -> str:
        labels = [REQUIRED_LABEL, *args.extra_labels]
        seen: set[str] = set()
        deduped = [lab for lab in labels if not (lab in seen or seen.add(lab))]
        issue = gh.create_issue(title=args.title, body=args.body, labels=deduped)
        url = issue.get("html_url", "(no URL returned)")
        number = issue.get("number", "?")
        return f"Opened issue #{number}: {url}"

    return [open_triage_issue]
