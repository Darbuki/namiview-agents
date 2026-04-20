# syntax=docker/dockerfile:1.7
#
# Build from the repo root:
#   docker build -f agents/triage/Dockerfile -t namiview-triage:dev .
#
# The build context must include both `shared/` and `agents/triage/` because
# the triage package depends on the `namiview-shared` workspace member.
#
# Multi-stage:
#   1. `builder` — uses `uv` to install deps + the project into a venv.
#      Split into two COPY+RUN pairs so dependency resolution caches
#      independently of source edits.
#   2. Runtime — slim `python:3.13-slim`, non-root user, venv on PATH.

# ---------- 1. Builder ----------
FROM python:3.13-slim AS builder

# Pin uv to a specific minor version so the image is reproducible.
# Copy the static `uv` binary from its official image — no pip install, no
# compilation.
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON=python3.13

WORKDIR /app

# Step 1: install only the dependency closure. This layer only invalidates
# when pyproject.toml / uv.lock / workspace manifests change — NOT when
# application source changes. Huge cache win on iterative builds.
COPY pyproject.toml uv.lock ./
COPY shared/pyproject.toml ./shared/
COPY agents/triage/pyproject.toml ./agents/triage/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Step 2: copy source and install the workspace packages themselves. This
# layer invalidates on any source change, but deps above are already cached.
COPY shared ./shared
COPY agents/triage ./agents/triage
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---------- 2. Runtime ----------
FROM python:3.13-slim AS runtime

# Drop privileges. The agent only ever makes outbound API calls; it has no
# business running as root. UID 1000 is arbitrary but stable.
RUN useradd --create-home --uid 1000 --shell /bin/bash agent

WORKDIR /app

# Copy the built venv + application from the builder stage. Owned by the
# unprivileged user so the container can write to /app/... if needed.
COPY --from=builder --chown=agent:agent /app /app

# Activate the venv by prepending it to PATH. Avoids `source activate` nonsense
# and means `namiview-triage` (the console script) resolves directly.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER agent

# Default entrypoint is the HTTP server — this is how the agent runs in-cluster
# (fronted by a k8s Service, triggered by Alertmanager webhooks).
#
# The one-shot CLI is still shipped in the image as `namiview-triage`; override
# the entrypoint to use it, e.g. for manual Job-style investigations:
#   docker run --entrypoint namiview-triage <image> "describe the problem"
EXPOSE 8080
ENTRYPOINT ["namiview-triage-server"]
