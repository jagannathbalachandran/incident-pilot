# Dockerfile
# IncidentPilot — AI-powered incident-response copilot
#
# Multi-stage build:
#   builder  — installs Python deps via uv (including torch)
#   runtime  — minimal image with only what's needed at runtime

FROM python:3.11-slim AS builder

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Install system dependencies for building torch-dependent packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    make \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency manifests -- uv.lock is required so the container gets the
# exact host-verified versions (e.g. chromadb), not an independent re-resolve
# that can drift and silently produce an incompatible version against the
# persisted vector store's on-disk schema.
COPY pyproject.toml uv.lock .

# Install production dependencies only (exclude test/dev groups), frozen to
# the lockfile -- no re-resolution.
RUN uv sync --frozen --no-dev && \
    uv pip install torch --index-url https://download.pytorch.org/whl/cpu --quiet

# =========================================================================
FROM python:3.11-slim AS runtime

WORKDIR /app

# Install curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy the venv from builder
COPY --from=builder /app/.venv /app/.venv

# Phoenix's ThreadServer.run_in_thread() hardcodes a 5-second window for its
# uvicorn+gRPC server thread to bind and signal ready (see
# phoenix/server/thread_server.py) -- not configurable via any PHOENIX_* env
# var. On this project's slower/virtualized Docker hosts that's consistently
# too short (observed: RuntimeError: server took too long to start, every
# attempt, even after DB migrations already completed), so raise it in the
# installed package directly rather than relying on host performance.
RUN sed -i 's/time_limit = time() + 5/time_limit = time() + 30/' \
    /app/.venv/lib/python3.11/site-packages/phoenix/server/thread_server.py

# Copy application source code
COPY src/ ./src/
COPY synthetic-data/ ./synthetic-data/
COPY prompts/ ./prompts/

# Environment
ENV PATH="/app/.venv/bin:$PATH"
ENV TOKENIZERS_PARALLELISM=false
ENV PYTHONUNBUFFERED=1

EXPOSE 7860 6006

# Gradio (7860) is always required. Phoenix (6006) is opt-in via
# PHOENIX_TRACING -- only require it when that's actually set, otherwise a
# normal PHOENIX_TRACING-off run would be reported unhealthy for a service
# it never started.
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=15s \
    CMD sh -c 'curl -f http://localhost:7860/ >/dev/null 2>&1 || exit 1; \
        if [ "$PHOENIX_TRACING" = "true" ]; then \
            curl -f http://localhost:6006/ >/dev/null 2>&1 || exit 1; \
        fi'

CMD ["python", "src/app.py"]
