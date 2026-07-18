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

# Copy dependency manifests
COPY pyproject.toml .

# Install production dependencies only (exclude test/dev groups)
RUN uv sync --no-dev && \
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

# Copy application source code
COPY src/ ./src/
COPY synthetic-data/ ./synthetic-data/
COPY prompts/ ./prompts/

# Environment
ENV PATH="/app/.venv/bin:$PATH"
ENV TOKENIZERS_PARALLELISM=false
ENV PYTHONUNBUFFERED=1

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=15s \
    CMD curl -f http://localhost:7860/ || exit 1

CMD ["python", "src/app.py"]
