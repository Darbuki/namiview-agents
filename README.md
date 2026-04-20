# namiview-agents

Monorepo for Namiview AI agents. Hand-rolled tool-use loops on top of the
Anthropic API (Claude Haiku).

## Agents

- **[triage](agents/triage/)** — incident-triage agent for the namiview EKS
  cluster. Investigates pod failures, gathers evidence from Kubernetes,
  opens GitHub issues with findings. Epic: [Darbuki/namiview#72][epic].

[epic]: https://github.com/Darbuki/namiview/issues/72

## Layout

```
shared/                # common primitives: Claude client, tool-use loop, logging
agents/
  triage/              # the triage agent (deployable)
```

`shared/` is a uv workspace member — each agent imports it as
`namiview_shared` rather than copying code. Deployable units live in
`agents/<name>/` with their own `Dockerfile` and (later) Helm chart.

## Prereqs

- Python 3.13+
- [`uv`](https://docs.astral.sh/uv/) — installs everything else

## Development

```
uv sync                 # install workspace + dev deps
uv run pytest           # run all tests
uv run ruff check       # lint
uv run ruff format      # format
```

Run the triage agent locally (requires `ANTHROPIC_API_KEY`, optionally
`GITHUB_TOKEN`, and a reachable kubeconfig):

```
uv run namiview-triage "pods in namiview are crash-looping"
```

## Status

Phase 0: scaffold. No agent logic yet — see the `agents/triage/` README and
the epic above for the roadmap.
