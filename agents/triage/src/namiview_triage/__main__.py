"""CLI entrypoint for the triage agent.

Usage:
    namiview-triage "<description of the problem>"

Environment:
    ANTHROPIC_API_KEY   required
    GITHUB_TOKEN        required (PAT with Issues:RW on Darbuki/namiview)
    KUBECONFIG          optional; in-cluster SA is used when unset

Exit codes:
    0  issue filed (or agent completed cleanly)
    1  unexpected error
    2  bad invocation
"""

from __future__ import annotations

import argparse
import os
import sys

from namiview_shared import configure_logging, get_logger

from .agent import build_triage_agent
from .tools import load_k8s_client
from .tools.github import from_env as github_from_env


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="namiview-triage",
        description="Investigate a namiview cluster incident and file a GitHub issue.",
    )
    parser.add_argument(
        "description",
        help="Natural-language description of the problem to investigate.",
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("LOG_LEVEL", "INFO"),
        help="Log level (DEBUG/INFO/WARNING/ERROR). Default: INFO.",
    )
    parser.add_argument(
        "--repo",
        default="Darbuki/namiview",
        help="owner/name of the repo to open the issue against.",
    )
    return parser.parse_args(argv)


def _require_env(name: str) -> None:
    if not os.environ.get(name):
        print(f"error: {name} is not set.", file=sys.stderr)
        sys.exit(2)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    configure_logging(level=args.log_level)
    log = get_logger("namiview_triage")

    _require_env("ANTHROPIC_API_KEY")
    _require_env("GITHUB_TOKEN")

    try:
        core = load_k8s_client()
    except Exception as e:  # noqa: BLE001
        log.error("k8s.load.failed", error=str(e))
        print(f"error: could not load Kubernetes config: {e}", file=sys.stderr)
        return 1

    gh = github_from_env(repo=args.repo)
    agent = build_triage_agent(core=core, gh=gh)

    log.info("triage.start", description=args.description, repo=args.repo)
    try:
        run = agent.run(args.description)
    except Exception as e:  # noqa: BLE001
        log.exception("triage.failed")
        print(f"error: agent run failed: {e}", file=sys.stderr)
        return 1

    log.info(
        "triage.done",
        iterations=run.iterations,
        stop_reason=run.stop_reason,
    )
    for block in run.final_message.content:
        if getattr(block, "type", None) == "text":
            print(block.text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
