# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

**incident-pilot** is an AI-powered incident-response copilot for on-call SRE engineers. It uses RAG over runbooks and postmortems; queries live Prometheus metrics and Loki logs (with automatic static fallback); runs structured log analysis (level breakdown, pattern grouping, error cluster detection); and returns cited triage summaries — but it **never executes deploys, rollbacks, or any production-mutating action** without explicit human approval.

Tech stack: **Python + LangChain + Groq LLM + ChromaDB + Gradio UI + Docker Compose (Prometheus/Loki/Grafana/Flask generator)**.

## Quick start

```bash
# 1. Build vector store (one-time)
.venv/bin/python src/ingestion.py

# 2. Start monitoring stack
docker compose up -d

# 3. Trigger an incident
curl -X POST http://localhost:5001/api/incidents/pool/trigger

# 4. Run the agent (queries live Prometheus + RAG)
.venv/bin/python src/incident_pilot.py

# Or launch the UI
cd src && TOKENIZERS_PARALLELISM=false ../.venv/bin/python app.py
```

## Source files

### `src/ingestion.py`
Builds ChromaDB vector store from runbooks/postmortems. Splits on `##` headers, embeds with `all-MiniLM-L6-v2`, persists to `synthetic-data/vectorstore/`. Only needs re-running when the corpus changes.

### `src/incident_pilot.py`
Core `IncidentPilot` class. Key methods:
- `retrieve(query)` — RAG search over ChromaDB, returns top-k chunks with source/section
- `query_logs(timeframe)` — queries Prometheus + Loki (delegates to `query_logs.py`)
- `_format_live_data(log_result)` — structured summary of metrics + log analysis
- `query(user_input)` — full pipeline: RAG + live data + LLM → cited triage summary

### `src/query_logs.py`
Data layer with four data sources (live + fallback for both metrics and logs):
- `query_prometheus()` — GET `localhost:9090/api/v1/query_range`
- `query_loki()` — GET `localhost:3100/loki/api/v1/query_range`
- `_load_metrics_fallback()` — reads `synthetic-data/metrics/*.json`
- `_load_logs_fallback()` — reads `synthetic-data/logs/*.jsonl`

Plus log analysis:
- `analyze_logs()` — extracts levels, groups patterns via `_normalize_message()`, detects error clusters (bursts within 30s)
- `_extract_level()`, `_extract_message()`, `_normalize_message()`, `_timestamp_diff()`, `_try_parse_timestamp()`

### `src/app.py`
Gradio UI. Shows data source badge (🟢 Live / 🟡 Fallback / 🔴 Unavailable). Submits query with cached live data to avoid double-querying Prometheus/Loki.

### `flask-generator/`
Docker Flask app that simulates production incidents in real-time:
- `app.py` — Flask server with background tick loop
- `incident_scenarios.py` — state machine for pool/cache/fraud scenarios
- `config.py` — Pydantic models, timing budgets, metric baselines
- `metrics_exporter.py` — Prometheus client registry
- `log_generator.py` — log line emitter (stdout → Loki via logging driver)

## Monitoring stack (Docker Compose)

4 services on `monitoring` network:

| Service | Image | Port | Purpose |
|---|---|---|---|
| flask-generator | built from `flask-generator/Dockerfile` | 5001 | Incident simulator |
| prometheus | prom/prometheus:v2.55.0 | 9090 | Metrics store |
| loki | grafana/loki:3.0.0 | 3100 | Log aggregation |
| grafana | grafana/grafana:11.2.0 | 3000 | Dashboards (admin/admin) |

## Running tests

```bash
# All tests
.venv/bin/python -m pytest tests/ -v

# Specific suites
.venv/bin/python -m pytest tests/test_query_logs.py -v    # 43 tests
.venv/bin/python -m pytest tests/test_incident_pilot.py -v # 4 tests (2 call real Groq)
```

`test_query_logs.py` mocks all network calls — no live stack needed.  
`test_incident_pilot.py` calls real Groq API — `GROQ_API_KEY` must be set.

## Incident scenarios

Trigger via API while Docker stack is running:

```bash
# Pool exhaustion (p99 climbs, connections saturate, errors appear)
curl -X POST http://localhost:5001/api/incidents/pool/trigger

# Cache failover (cache_hit drops, latency rises, errors stay at baseline)
curl -X POST http://localhost:5001/api/incidents/cache/trigger

# Fraud outage (error rate spikes to 10-15%)
curl -X POST http://localhost:5001/api/incidents/fraud/trigger

# Check current state
curl http://localhost:5001/api/incidents/state
```

Lifecycle durations (accelerated mode, 1s = 1 simulated minute):
- **Pool**: 15s climbing + 15s plateau + 10s recovery = ~40s total
- **Cache**: 6s failover + 12s warming = ~18s total
- **Fraud**: 20s active

## Data flow (query → response)

```
User query
  │
  ├─ 1. retrieve() → ChromaDB similarity search → top-3 RAG chunks
  │
  ├─ 2. query_logs() → Prometheus + Loki (or static files)
  │      └─ analyze_logs() → structured summary (levels, patterns, clusters)
  │
  ├─ 3. Build prompt:
  │      ## Retrieved context (RAG)
  │      ## Live metrics & logs [source: live|static_fallback]
  │      Engineer's description
  │
  ├─ 4. LLM invoke() → Groq llama-3.3-70b-versatile → cited response
  │
  └─ 5. UI: badge + response displayed
```

## Key architectural constraints

1. **Guardrails are prompt-level** — The system prompt `prompts/system_prompt.md` defines the "no autonomous production actions" rule. This runs before any RAG or tool calls. Guardrails work even if vector store or live stack is unavailable.

2. **RAG is pre-computed** — `ingestion.py` chunks and embeds all documents offline. At query time, only the user's query is embedded (~100ms) and searched (~50ms).

3. **Log analysis is structured** — `analyze_logs()` runs before the LLM prompt is built, producing a concise structured summary instead of raw log line dumps.

4. **Data source fallback is automatic** — `query_logs()` tries live Prometheus/Loki first, then falls back to static JSON/JSONL files. The UI badge shows the source.

5. **The `phase` field in synthetic metrics** (`baseline`/`climbing`/`plateau`/`recovering`) is a dataset-layer annotation only — it is stripped before returning data to the agent so the agent doesn't see ground-truth labels it should be inferring.

## Corpus layout

| Directory | Contents |
|---|---|
| `synthetic-data/runbooks/` | Service runbooks for RAG indexing |
| `synthetic-data/postmorterms/` | Past-incident postmortems for RAG indexing |
| `synthetic-data/metrics/` | JSON time-series metrics for fallback |
| `synthetic-data/logs/` | JSONL application logs for fallback |
| `synthetic-data/vectorstore/` | ChromaDB (built by `ingestion.py`, not committed) |
| `flask-generator/` | Docker Flask incident simulator |
| `docs/` | Generation prompts and team context |
| `prompts/` | System prompt and generation prompts |

## Conventions for runbooks and postmortems

See `docs/RUNBOOK_GENERATION_PROMPT.md` and `docs/POSTMORTEM_GENERATION_PROMPT.md` for full generation guidelines.

Key constraints:
- Frontmatter: only `service` and `doc_type` fields — no extras.
- Separate triage paths for `p99-latency-high` vs `error-rate-high`.
- No rollback/deploy instructions in the runbook.
- Every `##` section must be self-contained (file is chunked on headers).
- Postmortem titles by symptom/impact, not root cause.
- Action items table: max 2 rows, independently verifiable.
