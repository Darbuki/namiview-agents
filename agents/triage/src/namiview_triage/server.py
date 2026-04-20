"""FastAPI server entrypoint.

Exposes the triage agent as an HTTP service so Alertmanager (or any other
webhook source) can trigger investigations. Same agent code as the CLI —
this module is a thin HTTP shell around `build_triage_agent`.

Endpoints:
    POST /triage    Run an investigation. Accepts either a plain
                    `{"description": "..."}` body or an Alertmanager v4
                    webhook payload (dispatch on presence of `alerts`).
                    Returns 202 immediately; the agent runs in the
                    background.
    GET  /healthz   Liveness. Cheap — returns 200 as long as the process
                    is up. Does not call out to k8s or Anthropic.

Concurrency:
    Investigations are CPU-light but network-heavy (k8s reads + Anthropic
    calls). We cap in-flight runs with a semaphore — a burst of alerts
    (e.g. node goes down, 20 pods alert at once) shouldn't fork 20 agent
    runs; they queue.

Auth:
    None. The Service is ClusterIP-only and intended to be reached from
    inside the cluster (Alertmanager). Network-layer auth lives in the
    NetworkPolicy, not here.
"""

from __future__ import annotations

import os
import threading
import uuid
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from namiview_shared import configure_logging, get_logger

from .agent import build_triage_agent
from .tools import load_k8s_client
from .tools.github import from_env as github_from_env

if TYPE_CHECKING:
    from namiview_shared import Agent


# Max concurrent investigations. Low on purpose — each run burns Anthropic
# tokens and makes real API calls; an alert storm shouldn't fan out freely.
MAX_CONCURRENT_RUNS = int(os.environ.get("TRIAGE_MAX_CONCURRENT", "3"))


class TriageRequest(BaseModel):
    """Manual/curl payload shape."""

    description: str = Field(min_length=1, max_length=4000)


def _require_env(name: str) -> None:
    if not os.environ.get(name):
        raise RuntimeError(f"{name} is not set")


def _description_from_alert(alert: dict[str, Any]) -> str:
    """Render one Alertmanager alert into a natural-language description.

    We don't try to be clever — just hand the model the alert name, the
    target pod/namespace/container labels, and any annotation the alert
    author wrote. The agent figures out the investigation path.
    """
    labels = alert.get("labels", {}) or {}
    annotations = alert.get("annotations", {}) or {}
    alertname = labels.get("alertname", "<unknown alert>")
    namespace = labels.get("namespace", "<unknown namespace>")
    pod = labels.get("pod") or labels.get("container") or "<unknown pod>"
    summary = annotations.get("summary") or annotations.get("description") or ""

    parts = [
        f"Alert `{alertname}` is firing for pod `{pod}` in namespace `{namespace}`.",
    ]
    if summary:
        parts.append(f"Alert summary: {summary}")
    parts.append("Investigate the root cause and file a triage issue.")
    return " ".join(parts)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build long-lived clients once at startup."""
    configure_logging(level=os.environ.get("LOG_LEVEL", "INFO"))
    log = get_logger("namiview_triage.server")

    _require_env("ANTHROPIC_API_KEY")
    _require_env("GITHUB_TOKEN")

    repo = os.environ.get("GITHUB_REPO", "Darbuki/namiview")
    core = load_k8s_client()
    gh = github_from_env(repo=repo)
    agent = build_triage_agent(core=core, gh=gh)

    # Bounded concurrency — a semaphore, not a queue, because we WANT
    # excess alerts to block briefly rather than pile up unboundedly.
    sem = threading.Semaphore(MAX_CONCURRENT_RUNS)

    app.state.agent = agent
    app.state.log = log
    app.state.sem = sem
    app.state.repo = repo

    log.info("server.start", repo=repo, max_concurrent=MAX_CONCURRENT_RUNS)
    yield
    log.info("server.stop")


app = FastAPI(title="namiview-triage", lifespan=lifespan)


def _run_investigation(
    agent: Agent,
    description: str,
    run_id: str,
    log,
    sem: threading.Semaphore,
) -> None:
    """Executed in a background thread — hold the semaphore for the full run."""
    with sem:
        log.info("triage.run.start", run_id=run_id, description=description)
        try:
            run = agent.run(description)
            log.info(
                "triage.run.done",
                run_id=run_id,
                iterations=run.iterations,
                stop_reason=run.stop_reason,
            )
        except Exception as e:
            log.exception("triage.run.failed", run_id=run_id, error=str(e))


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/triage", status_code=202)
async def triage(request: Request, background: BackgroundTasks) -> dict[str, Any]:
    """Kick off one or more investigations and return 202 immediately.

    Response body lists the run_ids spawned so callers can correlate.
    """
    body = await request.json()
    agent: Agent = request.app.state.agent
    log = request.app.state.log
    sem: threading.Semaphore = request.app.state.sem

    descriptions: list[str] = []

    # Alertmanager v4 webhook shape has top-level `alerts` list.
    if isinstance(body, dict) and isinstance(body.get("alerts"), list):
        for alert in body["alerts"]:
            if alert.get("status") != "firing":
                continue  # Skip resolved alerts — we don't un-file issues.
            descriptions.append(_description_from_alert(alert))
        if not descriptions:
            log.info("triage.noop", reason="no firing alerts in payload")
            return {"run_ids": [], "reason": "no firing alerts"}
    else:
        # Manual/curl path — validate the simple shape.
        try:
            parsed = TriageRequest.model_validate(body)
        except Exception as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        descriptions.append(parsed.description)

    run_ids: list[str] = []
    for description in descriptions:
        run_id = uuid.uuid4().hex[:12]
        run_ids.append(run_id)
        background.add_task(_run_investigation, agent, description, run_id, log, sem)

    log.info("triage.accepted", run_ids=run_ids, count=len(run_ids))
    return {"run_ids": run_ids}


def main() -> None:
    """Console-script entrypoint: `namiview-triage-server`.

    Keep this thin — all the real wiring happens in `lifespan()` so tests
    can instantiate `app` without going through uvicorn.
    """
    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(
        "namiview_triage.server:app",
        host=host,
        port=port,
        log_config=None,  # Let structlog handle logging; don't let uvicorn install its own.
    )


if __name__ == "__main__":
    main()
