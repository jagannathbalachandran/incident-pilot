# IncidentPilot

AI-powered incident-response copilot for on-call SRE engineers. Uses RAG over runbooks/postmortems, queries live Prometheus/Loki metrics and logs, analyzes log patterns, detects contradictions between live data and engineer's description, and returns cited triage summaries — all while **refusing to execute any deploy, rollback, or production-mutating action** without explicit human approval.

> **📖 Single reference:** See [`docs/walkthrough-log.md`](docs/walkthrough-log.md) for the complete E2E walkthrough, LLM integration guide, log tracing, and step-by-step usage — all in one document.

---

## Key Features

- **RAG-grounded triage** — retrieves relevant runbook/postmortem sections from ChromaDB
- **Live data** — queries Prometheus metrics + Loki logs (with automatic static-file fallback)
- **Log analysis** — structured summaries (levels, patterns, error clusters), not raw line dumps
- **Contradiction detection** — code-level + prompt-level checks that flag when live data contradicts the engineer's description
- **Request-ID tracing** — every query and API call gets a unique ID that flows through all logs (Gradio, Docker, Loki)
- **Guardrails** — unconditional refusal of deploy/rollback/hotfix requests, even if RAG or data sources fail
- **Trace panel** — expandable UI panel showing exactly what the agent saw (RAG chunks, metrics, log analysis, full prompt)
- **Three incident scenarios** — pool exhaustion, cache failover, fraud outage (real-time simulation)

---

## Architecture Overview

```
┌─ Host Machine ───────────────────────────────┐
│                                                │
│  Gradio UI (:7860) → IncidentPilot Agent       │
│                          ├─ ChromaDB (RAG)     │
│                          ├─ Prometheus (:9090) │
│                          └─ Loki (:3100)       │
│                                                │
└──────────────────┬─────────────────────────────┘
                   │ HTTP
┌─ Docker Stack ───▼─────────────────────────────┐
│                                                │
│  FastAPI Generator (:5001) ──→ Prometheus (:9090) │
│       │ stdout / HTTP push ──→ Loki (:3100)    │
│                                                │
│  Grafana (:3000) ←── Prometheus + Loki         │
│                                                │
└────────────────────────────────────────────────┘
```

---

## Stack

| Component | Technology | Port |
|---|---|---|
| AI Agent | Python + LangChain + ChatGroq | — |
| Embeddings | HuggingFace `all-MiniLM-L6-v2` | — |
| Vector Store | ChromaDB | — |
| UI | Gradio 4.x | `7860` |
| Incident Simulator | FastAPI (Docker) | `5001` |
| Metrics | Prometheus (Docker) | `9090` |
| Logs | Loki (Docker) | `3100` |
| Dashboards | Grafana (Docker) | `3000` |

---

## Quick Start

```bash
# 1. Install uv (if needed)
# curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Setup (create .venv, install deps)
uv venv
uv sync --group test
uv pip install torch --index-url https://download.pytorch.org/whl/cpu

# 3. API key
cp .env.example .env   # Add GROQ_API_KEY=your_key_here

# 4. Build RAG vector store
uv run python src/ingestion.py

# 5. Start monitoring stack
docker compose up -d

# 6. Trigger an incident and test the agent
curl -X POST http://localhost:5001/api/incidents/pool/trigger
uv run python src/incident_pilot.py

# 7. Launch the Gradio UI
cd src && TOKENIZERS_PARALLELISM=false uv run python app.py
# Open http://127.0.0.1:7860
```

---

## Testing

```bash
uv run python -m pytest tests/ -v
# 131 tests: guardrails (2 real LLM) + structure (5) + contradiction detection (17) + data layer (43) + FastAPI (64)
```

---

## Incident Scenarios

| Scenario | Trigger | Signature |
|---|---|---|
| Pool Exhaustion | `POST /api/incidents/pool/trigger` | Latency climbs gradually, connections hit 200, errors appear |
| Cache Failover | `POST /api/incidents/cache/trigger` | Cache hit drops to 0.41, latency rises, errors stay flat |
| Fraud Outage | `POST /api/incidents/fraud/trigger` | Error rate spikes to 10-15%, connections normal |

---

## Documentation Map

| Document | What it covers |
|---|---|
| [`docs/walkthrough-log.md`](docs/walkthrough-log.md) | **Complete reference** — E2E walkthrough, LLM integration, log tracing, user guide — all merged into one document |
| [`docs/architecture/high-level-design.md`](docs/architecture/high-level-design.md) | System architecture, design decisions, API spec |
| [`docs/architecture/low-level-design.md`](docs/architecture/low-level-design.md) | Class diagrams, metric formulas, code structure |
| [`docs/postman/IncidentPilot.postman_collection.json`](docs/postman/IncidentPilot.postman_collection.json) | Postman collection for all APIs |
