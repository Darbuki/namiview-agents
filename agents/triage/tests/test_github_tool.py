"""Tests for the GitHub issue tool. httpx is mocked via a fake GitHubClient."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from namiview_triage.tools.github import GitHubClient, build_github_tools


@dataclass
class _FakeGH:
    """Stand-in for GitHubClient with a recorded call log."""

    repo: str = "Darbuki/namiview"
    token: str = "fake"
    calls: list[dict] = field(default_factory=list)
    return_value: dict = field(
        default_factory=lambda: {
            "number": 999999,
            "html_url": "https://github.com/Darbuki/namiview/issues/999999",
        }
    )
    raises: Exception | None = None

    def create_issue(self, title: str, body: str, labels: list[str]) -> dict:
        self.calls.append({"title": title, "body": body, "labels": labels})
        if self.raises:
            raise self.raises
        return self.return_value


def test_open_issue_happy_path() -> None:
    gh = _FakeGH()
    tool = build_github_tools(gh)[0]  # type: ignore[arg-type]

    out = tool.invoke(
        {
            "title": "Pod foo crashlooping",
            "body": "Evidence: CrashLoopBackOff seen 12x in last hour. Hypothesis: bad config.",
            "extra_labels": ["urgent"],
        }
    )

    assert "#999999" in out
    assert "https://github.com/Darbuki/namiview/issues/999999" in out
    assert len(gh.calls) == 1
    call = gh.calls[0]
    assert call["labels"] == ["triage", "urgent"]


def test_triage_label_always_applied_even_without_extras() -> None:
    gh = _FakeGH()
    tool = build_github_tools(gh)[0]  # type: ignore[arg-type]

    tool.invoke(
        {
            "title": "Small issue",
            "body": "Something might be off in namespace foo.",
        }
    )
    assert gh.calls[0]["labels"] == ["triage"]


def test_duplicate_triage_label_deduplicated() -> None:
    gh = _FakeGH()
    tool = build_github_tools(gh)[0]  # type: ignore[arg-type]

    tool.invoke(
        {
            "title": "Dedup test",
            "body": "Body long enough to pass validation of min length.",
            "extra_labels": ["triage", "bug", "triage"],
        }
    )
    # Only one "triage", preserved order.
    assert gh.calls[0]["labels"] == ["triage", "bug"]


def test_api_error_propagates() -> None:
    gh = _FakeGH(raises=RuntimeError("GitHub API 401: bad creds"))
    tool = build_github_tools(gh)[0]  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="401"):
        tool.invoke(
            {
                "title": "Will fail",
                "body": "This call should raise because auth is broken.",
            }
        )


def test_real_client_posts_and_handles_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Smoke test the real GitHubClient using a stubbed httpx.Client."""
    import httpx

    class _StubResp:
        status_code = 201
        text = ""

        def json(self) -> dict:
            return {"number": 7, "html_url": "https://example/7"}

    class _StubClient:
        def __init__(self, *a, **kw) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers, json):
            assert "/repos/Darbuki/namiview/issues" in url
            assert headers["Authorization"] == "Bearer test-token"
            assert json["labels"] == ["triage"]
            return _StubResp()

    monkeypatch.setattr(httpx, "Client", _StubClient)
    client = GitHubClient(token="test-token")
    result = client.create_issue(title="t", body="b", labels=["triage"])
    assert result["number"] == 7
