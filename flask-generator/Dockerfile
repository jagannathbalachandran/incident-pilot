# flask-generator/Dockerfile
FROM python:3.11-slim AS builder

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency manifests
COPY pyproject.toml .

# Install dependencies (no dev dependencies in production)
RUN uv sync --no-dev

# Copy application code
COPY config.py .
COPY topology.py .
COPY incident_scenarios.py .
COPY traffic.py .
COPY metrics_exporter.py .
COPY log_generator.py .
COPY app.py .

EXPOSE 5000

CMD [".venv/bin/python", "app.py"]
