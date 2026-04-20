# triage

Incident-triage AI agent for the namiview EKS cluster.

Epic: [Darbuki/namiview#72](https://github.com/Darbuki/namiview/issues/72).

## What it does (when finished)

Given a natural-language description of a problem (or a namespace to
investigate), the agent:

1. Queries Kubernetes for relevant pods, events, logs.
2. Forms a hypothesis about the root cause using Claude Haiku.
3. Opens a GitHub issue on `Darbuki/namiview` with evidence and the
   hypothesis, tagged `triage`.

No destructive actions. Read-only against the cluster.

## Run locally

```
uv run namiview-triage "<description of the problem>"
```

Requires:

| Env var              | What for                              | Source                                            |
|----------------------|---------------------------------------|---------------------------------------------------|
| `ANTHROPIC_API_KEY`  | Talk to Claude                        | [console.anthropic.com](https://console.anthropic.com) |
| `GITHUB_TOKEN`       | Open issues on `Darbuki/namiview`     | Fine-grained PAT, repo-scoped                     |
| `KUBECONFIG` *(opt)* | Reach the cluster from outside        | Defaults to in-cluster SA token when deployed     |

In production both tokens come from AWS Secrets Manager via ExternalSecrets.
Locally you set them as env vars.

## Status

Phase 0: scaffolding only. `uv run namiview-triage` prints a placeholder.
Real logic lands in phase 3.
