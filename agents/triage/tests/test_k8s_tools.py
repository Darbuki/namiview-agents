"""Tests for k8s tools — all k8s API calls mocked, no real cluster hit."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from kubernetes.client.rest import ApiException

from namiview_triage.tools.k8s import build_k8s_tools

# Helpers to build fake CoreV1 responses in the shape the kubernetes client returns.


def _ns(name: str) -> SimpleNamespace:
    return SimpleNamespace(metadata=SimpleNamespace(name=name))


def _pod(
    name: str,
    phase: str = "Running",
    restarts: int = 0,
    ready: bool = True,
    node: str = "ip-10-0-1-1",
    containers: int = 1,
    reason: str | None = None,
) -> SimpleNamespace:
    container_statuses = [
        SimpleNamespace(
            name=f"c{i}",
            ready=ready,
            restart_count=restarts,
            image="nginx:1.25",
            state=SimpleNamespace(
                running=SimpleNamespace(started_at=datetime(2026, 4, 20, tzinfo=UTC)),
                waiting=None,
                terminated=None,
            ),
        )
        for i in range(containers)
    ]
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=name, creation_timestamp=datetime(2026, 4, 20, tzinfo=UTC)
        ),
        spec=SimpleNamespace(node_name=node),
        status=SimpleNamespace(
            phase=phase,
            reason=reason,
            message=None,
            container_statuses=container_statuses,
            conditions=[
                SimpleNamespace(
                    type="Ready",
                    status="True" if ready else "False",
                    reason=None,
                    message=None,
                )
            ],
        ),
    )


def _event(
    type_: str,
    reason: str,
    msg: str,
    minutes_ago: int,
    kind: str = "Pod",
    name: str = "mypod",
) -> SimpleNamespace:
    ts = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    return SimpleNamespace(
        type=type_,
        reason=reason,
        message=msg,
        last_timestamp=ts,
        event_time=None,
        metadata=SimpleNamespace(creation_timestamp=ts),
        involved_object=SimpleNamespace(kind=kind, name=name),
    )


def _tools_map(core: MagicMock) -> dict:
    return {t.name: t for t in build_k8s_tools(core)}


def test_list_pods_happy() -> None:
    core = MagicMock()
    core.list_namespaced_pod.return_value = SimpleNamespace(
        items=[_pod("web-1"), _pod("web-2", restarts=3, ready=False)]
    )
    tools = _tools_map(core)

    out = tools["list_pods"].invoke({"namespace": "default"})
    assert "2 pods in namespace default" in out
    assert "web-1" in out
    assert "restarts=3" in out


def test_list_pods_empty() -> None:
    core = MagicMock()
    core.list_namespaced_pod.return_value = SimpleNamespace(items=[])
    tools = _tools_map(core)
    assert "No pods" in tools["list_pods"].invoke({"namespace": "empty-ns"})


def test_list_pods_api_error_raises() -> None:
    core = MagicMock()
    core.list_namespaced_pod.side_effect = ApiException(status=403, reason="Forbidden")
    tools = _tools_map(core)
    with pytest.raises(RuntimeError, match="403"):
        tools["list_pods"].invoke({"namespace": "kube-system"})


def test_describe_pod_not_found() -> None:
    core = MagicMock()
    core.read_namespaced_pod.side_effect = ApiException(status=404, reason="Not Found")
    tools = _tools_map(core)
    out = tools["describe_pod"].invoke({"namespace": "default", "name": "ghost"})
    assert "not found" in out


def test_describe_pod_happy() -> None:
    core = MagicMock()
    core.read_namespaced_pod.return_value = _pod("web-1", phase="Running")
    tools = _tools_map(core)
    out = tools["describe_pod"].invoke({"namespace": "default", "name": "web-1"})
    assert "Pod default/web-1" in out
    assert "phase: Running" in out
    assert "containers:" in out


def test_pod_logs_happy() -> None:
    core = MagicMock()
    core.read_namespaced_pod_log.return_value = "2026-04-20 line 1\n2026-04-20 line 2\n"
    tools = _tools_map(core)
    out = tools["get_pod_logs"].invoke(
        {"namespace": "default", "name": "web-1", "tail_lines": 50}
    )
    assert "line 1" in out
    # Confirm kwargs went through correctly.
    kwargs = core.read_namespaced_pod_log.call_args.kwargs
    assert kwargs["tail_lines"] == 50
    assert kwargs["previous"] is False
    assert kwargs["timestamps"] is True


def test_pod_logs_empty() -> None:
    core = MagicMock()
    core.read_namespaced_pod_log.return_value = ""
    tools = _tools_map(core)
    out = tools["get_pod_logs"].invoke({"namespace": "default", "name": "web-1"})
    assert "no log output" in out


def test_list_events_filters_old_and_by_type() -> None:
    core = MagicMock()
    core.list_namespaced_event.return_value = SimpleNamespace(
        items=[
            _event("Warning", "BackOff", "CrashLoopBackOff: ...", minutes_ago=10),
            _event("Normal", "Pulled", "Pulled image", minutes_ago=10),
            _event("Warning", "OldOne", "way back", minutes_ago=999),
        ]
    )
    tools = _tools_map(core)
    out = tools["list_events"].invoke(
        {"namespace": "default", "since_minutes": 60, "types": ["Warning"]}
    )
    assert "BackOff" in out
    assert "Pulled" not in out  # filtered by type
    assert "OldOne" not in out  # filtered by age


def test_list_namespaces() -> None:
    core = MagicMock()
    core.list_namespace.return_value = SimpleNamespace(
        items=[_ns("default"), _ns("kube-system"), _ns("argocd")]
    )
    tools = _tools_map(core)
    out = tools["list_namespaces"].invoke({})
    assert "3 namespaces" in out
    assert "argocd" in out
    assert "default" in out
