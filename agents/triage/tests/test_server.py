"""Tests for the FastAPI server.

We override the FastAPI lifespan to skip building real k8s/Anthropic/GitHub
clients and inject a fake agent. Real network calls never happen.
"""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from namiview_triage import server as server_mod


class _FakeAgent:
    """Stand-in Agent whose .run(description) is recorded."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.raises: Exception | None = None

    def run(self, description: str):
        self.calls.append(description)
        if self.raises:
            raise self.raises
        return SimpleNamespace(iterations=1, stop_reason="end_turn")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    """TestClient with a fake agent injected via a replacement lifespan."""
    fake = _FakeAgent()

    @asynccontextmanager
    async def fake_lifespan(app):
        app.state.agent = fake
        app.state.log = SimpleNamespace(
            info=lambda *a, **k: None,
            exception=lambda *a, **k: None,
        )
        app.state.sem = threading.Semaphore(3)
        app.state.repo = "Darbuki/namiview"
        yield

    monkeypatch.setattr(server_mod, "lifespan", fake_lifespan)
    # Rebuild the app with the patched lifespan.
    from fastapi import FastAPI

    app = FastAPI(lifespan=fake_lifespan)
    # Reattach the real routes.
    for route in server_mod.app.router.routes:
        app.router.routes.append(route)

    with TestClient(app) as c:
        c.fake_agent = fake  # type: ignore[attr-defined]
        yield c


def test_healthz(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_triage_manual_shape(client: TestClient) -> None:
    r = client.post("/triage", json={"description": "argocd is crashing"})
    assert r.status_code == 202
    body = r.json()
    assert len(body["run_ids"]) == 1
    # BackgroundTasks run synchronously after response in TestClient.
    assert client.fake_agent.calls == ["argocd is crashing"]  # type: ignore[attr-defined]


def test_triage_rejects_empty_description(client: TestClient) -> None:
    r = client.post("/triage", json={"description": ""})
    assert r.status_code == 422


def test_triage_alertmanager_shape(client: TestClient) -> None:
    payload = {
        "version": "4",
        "status": "firing",
        "receiver": "triage-agent",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "KubePodCrashLooping",
                    "namespace": "argocd",
                    "pod": "argocd-application-controller-0",
                },
                "annotations": {"summary": "Pod is crash-looping"},
            },
            {
                "status": "resolved",  # should be skipped
                "labels": {"alertname": "KubePodNotReady"},
            },
            {
                "status": "firing",
                "labels": {
                    "alertname": "KubePodOOMKilled",
                    "namespace": "namiview",
                    "pod": "namiview-api-5fd",
                },
                "annotations": {},
            },
        ],
    }
    r = client.post("/triage", json=payload)
    assert r.status_code == 202
    body = r.json()
    assert len(body["run_ids"]) == 2  # resolved alert skipped

    calls = client.fake_agent.calls  # type: ignore[attr-defined]
    assert len(calls) == 2
    assert "KubePodCrashLooping" in calls[0]
    assert "argocd-application-controller-0" in calls[0]
    assert "KubePodOOMKilled" in calls[1]


def test_triage_alertmanager_all_resolved(client: TestClient) -> None:
    payload = {
        "alerts": [
            {"status": "resolved", "labels": {"alertname": "X"}},
        ]
    }
    r = client.post("/triage", json=payload)
    assert r.status_code == 202
    body = r.json()
    assert body["run_ids"] == []
    assert client.fake_agent.calls == []  # type: ignore[attr-defined]


def test_description_from_alert_fallbacks() -> None:
    """Sparse alerts still render a usable description."""
    desc = server_mod._description_from_alert({"labels": {}, "annotations": {}})
    assert "unknown alert" in desc
    assert "unknown namespace" in desc


def test_agent_exception_is_swallowed(client: TestClient) -> None:
    """A failing run should not crash the server or surface to the caller.

    The 202 was already returned before the background task ran; we just
    want to confirm the task's exception doesn't propagate.
    """
    client.fake_agent.raises = RuntimeError("anthropic 500")  # type: ignore[attr-defined]
    r = client.post("/triage", json={"description": "boom"})
    assert r.status_code == 202
