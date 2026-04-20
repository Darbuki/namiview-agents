"""Read-only Kubernetes tools for the triage agent.

Auth: in-cluster SA token when running in a pod, `~/.kube/config` locally.
Outputs are bounded plain text with ISO-8601 UTC timestamps.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from kubernetes import client as k8s_client
from kubernetes import config as k8s_config
from kubernetes.client.rest import ApiException
from pydantic import BaseModel, Field

from namiview_shared import Tool, make_tool

if TYPE_CHECKING:
    from kubernetes.client import CoreV1Api

# Per-tool output caps to keep tool_result content from ballooning context.
MAX_PODS_LISTED = 80
MAX_EVENTS_LISTED = 60
MAX_LOG_TAIL_LINES = 500


def load_k8s_client() -> CoreV1Api:
    """Load kube config (in-cluster first, then local) and return a CoreV1 client."""
    try:
        k8s_config.load_incluster_config()
    except k8s_config.ConfigException:
        k8s_config.load_kube_config()
    return k8s_client.CoreV1Api()


def _iso(ts: datetime | None) -> str:
    if ts is None:
        return "-"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC).isoformat()


# ---------------------------------------------------------------------------
# Arg models
# ---------------------------------------------------------------------------


class _ListPodsArgs(BaseModel):
    namespace: str = Field(description="Kubernetes namespace to list pods from.")


class _DescribePodArgs(BaseModel):
    namespace: str
    name: str = Field(description="Pod name.")


class _PodLogsArgs(BaseModel):
    namespace: str
    name: str
    container: str | None = Field(
        default=None,
        description="Container name; omit for single-container pods.",
    )
    tail_lines: int = Field(
        default=200,
        ge=1,
        le=MAX_LOG_TAIL_LINES,
        description=f"Number of trailing log lines to return (1-{MAX_LOG_TAIL_LINES}).",
    )
    previous: bool = Field(
        default=False,
        description="If true, fetch logs from the previously-terminated container instance.",
    )


class _ListEventsArgs(BaseModel):
    namespace: str
    since_minutes: int = Field(
        default=60,
        ge=1,
        le=24 * 60,
        description="Only include events with lastTimestamp within this many minutes.",
    )
    types: list[str] | None = Field(
        default=None,
        description="Optional filter: e.g. ['Warning']. Defaults to all types.",
    )


class _NoArgs(BaseModel):
    pass


# ---------------------------------------------------------------------------
# Implementations
# ---------------------------------------------------------------------------


def _list_pods(core: CoreV1Api, args: _ListPodsArgs) -> str:
    try:
        resp = core.list_namespaced_pod(namespace=args.namespace)
    except ApiException as e:
        raise RuntimeError(f"list_pods({args.namespace}) failed: {e.status} {e.reason}") from e

    pods = resp.items
    total = len(pods)
    if total == 0:
        return f"No pods in namespace {args.namespace}."

    lines = [f"{total} pods in namespace {args.namespace}:"]
    for p in pods[:MAX_PODS_LISTED]:
        status = p.status.phase or "Unknown"
        restarts = sum(
            (cs.restart_count or 0) for cs in (p.status.container_statuses or [])
        )
        ready = sum(
            1 for cs in (p.status.container_statuses or []) if cs.ready
        )
        total_containers = len(p.status.container_statuses or [])
        node = p.spec.node_name or "-"
        reason = p.status.reason or ""
        lines.append(
            f"  {p.metadata.name}  phase={status}  ready={ready}/{total_containers}  "
            f"restarts={restarts}  node={node}  reason={reason}"
        )
    if total > MAX_PODS_LISTED:
        lines.append(f"  … {total - MAX_PODS_LISTED} more pods truncated.")
    return "\n".join(lines)


def _describe_pod(core: CoreV1Api, args: _DescribePodArgs) -> str:
    try:
        p = core.read_namespaced_pod(name=args.name, namespace=args.namespace)
    except ApiException as e:
        if e.status == 404:
            return f"Pod {args.namespace}/{args.name} not found."
        raise RuntimeError(
            f"describe_pod({args.namespace}/{args.name}) failed: {e.status} {e.reason}"
        ) from e

    lines = [
        f"Pod {args.namespace}/{args.name}",
        f"  node: {p.spec.node_name or '-'}",
        f"  phase: {p.status.phase}",
        f"  created: {_iso(p.metadata.creation_timestamp)}",
        f"  message: {p.status.message or '-'}",
        f"  reason: {p.status.reason or '-'}",
    ]

    if p.status.conditions:
        lines.append("  conditions:")
        for c in p.status.conditions:
            lines.append(
                f"    - type={c.type} status={c.status} "
                f"reason={c.reason or '-'} message={(c.message or '').strip()}"
            )

    if p.status.container_statuses:
        lines.append("  containers:")
        for cs in p.status.container_statuses:
            state = cs.state
            state_desc = "unknown"
            if state:
                if state.running:
                    state_desc = f"running (started {_iso(state.running.started_at)})"
                elif state.waiting:
                    state_desc = (
                        f"waiting ({state.waiting.reason}: "
                        f"{(state.waiting.message or '').strip()})"
                    )
                elif state.terminated:
                    state_desc = (
                        f"terminated ({state.terminated.reason}: "
                        f"exit={state.terminated.exit_code})"
                    )
            lines.append(
                f"    - {cs.name}  ready={cs.ready}  restarts={cs.restart_count}  "
                f"state={state_desc}  image={cs.image}"
            )

    return "\n".join(lines)


def _pod_logs(core: CoreV1Api, args: _PodLogsArgs) -> str:
    try:
        logs = core.read_namespaced_pod_log(
            name=args.name,
            namespace=args.namespace,
            container=args.container,
            tail_lines=args.tail_lines,
            previous=args.previous,
            timestamps=True,
        )
    except ApiException as e:
        if e.status == 404:
            return f"Pod {args.namespace}/{args.name} not found."
        raise RuntimeError(
            f"pod_logs({args.namespace}/{args.name}) failed: {e.status} {e.reason}"
        ) from e

    if not logs or not logs.strip():
        return f"(no log output for {args.namespace}/{args.name})"
    return logs


def _list_events(core: CoreV1Api, args: _ListEventsArgs) -> str:
    try:
        resp = core.list_namespaced_event(namespace=args.namespace)
    except ApiException as e:
        raise RuntimeError(
            f"list_events({args.namespace}) failed: {e.status} {e.reason}"
        ) from e

    now = datetime.now(UTC)
    cutoff_seconds = args.since_minutes * 60
    types_filter = {t.lower() for t in args.types} if args.types else None

    filtered = []
    for ev in resp.items:
        ts = ev.last_timestamp or ev.event_time or ev.metadata.creation_timestamp
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if (now - ts).total_seconds() > cutoff_seconds:
            continue
        if types_filter and (ev.type or "").lower() not in types_filter:
            continue
        filtered.append((ts, ev))

    if not filtered:
        return f"No matching events in {args.namespace} within last {args.since_minutes}m."

    filtered.sort(key=lambda t: t[0], reverse=True)
    lines = [f"{len(filtered)} events in {args.namespace} (last {args.since_minutes}m):"]
    for ts, ev in filtered[:MAX_EVENTS_LISTED]:
        obj = ev.involved_object
        target = f"{obj.kind}/{obj.name}" if obj else "-"
        msg = (ev.message or "").strip().replace("\n", " ")
        lines.append(
            f"  [{_iso(ts)}] {ev.type}  {ev.reason}  {target}  {msg}"
        )
    if len(filtered) > MAX_EVENTS_LISTED:
        lines.append(f"  … {len(filtered) - MAX_EVENTS_LISTED} more truncated.")
    return "\n".join(lines)


def _list_namespaces(core: CoreV1Api, _args: _NoArgs) -> str:
    try:
        resp = core.list_namespace()
    except ApiException as e:
        raise RuntimeError(f"list_namespaces failed: {e.status} {e.reason}") from e
    names = sorted(ns.metadata.name for ns in resp.items)
    if not names:
        return "(no namespaces visible)"
    return f"{len(names)} namespaces:\n  " + "\n  ".join(names)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_k8s_tools(core: CoreV1Api) -> list[Tool]:
    """Build the k8s tool list bound to a specific CoreV1Api instance."""

    @make_tool(
        "list_pods",
        "List pods in a namespace with phase, readiness, restart counts, and node.",
        _ListPodsArgs,
    )
    def list_pods(args: _ListPodsArgs) -> str:
        return _list_pods(core, args)

    @make_tool(
        "describe_pod",
        "Describe a specific pod: conditions, container states, last reason/message.",
        _DescribePodArgs,
    )
    def describe_pod(args: _DescribePodArgs) -> str:
        return _describe_pod(core, args)

    @make_tool(
        "get_pod_logs",
        "Return the tail of logs for a pod's container. Optionally from the previous instance.",
        _PodLogsArgs,
    )
    def get_pod_logs(args: _PodLogsArgs) -> str:
        return _pod_logs(core, args)

    @make_tool(
        "list_events",
        "List recent events in a namespace, newest first. Optional type filter (e.g. Warning).",
        _ListEventsArgs,
    )
    def list_events(args: _ListEventsArgs) -> str:
        return _list_events(core, args)

    @make_tool(
        "list_namespaces",
        "List all namespaces visible to the agent. Use to orient before other queries.",
        _NoArgs,
    )
    def list_namespaces(args: _NoArgs) -> str:
        return _list_namespaces(core, args)

    return [list_pods, describe_pod, get_pod_logs, list_events, list_namespaces]
