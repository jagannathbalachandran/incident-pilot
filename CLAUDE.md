# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

**incident-pilot** is an AI-powered incident-response copilot for on-call SRE engineers. It uses RAG over runbooks and postmortems (always retrieved); lets the LLM itself decide, per query, whether to call one or both of two MCP-backed tools — `query_metrics` (live Prometheus) and `query_logs` (live Loki, returned as a structured analysis — level breakdown, pattern grouping, error clusters, reconstructed journeys — not raw lines); and returns cited triage summaries — but it **never executes deploys, rollbacks, or any production-mutating action** without explicit human approval, and a code-level guard prevents it from even calling a telemetry tool on messages that look like a deploy/rollback/hotfix request. There is no fallback data source: if Prometheus/Loki can't be reached, the tool reports `source: "unavailable"` and the agent must tell the engineer plainly rather than substituting stale data.

Tech stack: **Python + LangChain + Groq LLM + ChromaDB + Gradio UI + Docker Compose (Prometheus/Loki/Grafana/FastAPI generator)**.

Package manager: **uv** (https://docs.astral.sh/uv/)

## Quick start

```bash
# 1. Install uv (if not already installed)
# curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Create venv and install dependencies
uv venv
uv sync --group test
uv pip install torch --index-url https://download.pytorch.org/whl/cpu

# 3. Build vector store (one-time)
uv run python src/ingestion.py

# 4. Start monitoring stack
docker compose up -d

# 5. Trigger an incident
curl -X POST http://localhost:5001/api/incidents/pool/trigger

# 6. Run the agent (queries live Prometheus + RAG)
uv run python src/incident_pilot.py

# Or launch the UI
cd src && TOKENIZERS_PARALLELISM=false uv run python app.py
```

## Source files

### `src/ingestion.py`
Builds ChromaDB vector store from runbooks/postmortems. Splits on `##` headers, embeds with `all-MiniLM-L6-v2`, persists to `synthetic-data/vectorstore/`. Only needs re-running when the corpus changes.

### `src/incident_pilot.py`
Core `IncidentPilot` class. Key methods:
- `retrieve(query)` — RAG search over ChromaDB, returns top-k chunks with source/section (always runs)
- `query(user_input, service=None)` — full pipeline: RAG retrieval, then a bounded tool-calling
  loop (`model.bind_tools([query_metrics, query_logs]).invoke(...)`) where **the LLM decides**
  whether/which tool to call, executes real MCP round trips for whatever it asks for, folds the
  results back in as `ToolMessage`s, then produces a cited answer
- `_looks_like_action_request(text)` / `MAX_TOOL_ROUNDS` — code-level guardrail backstop: messages
  matching deploy/rollback/hotfix/config-change verbs never get tools bound at all for that call,
  so a tool call is impossible (not just prompted-against) on those messages
- `_detect_contradictions(...)` — only meaningful if `query_metrics` was actually called this turn
- `get_trace()` — RAG chunks, which tool(s) were called with what args/results, data source
  (`live`/`unavailable`/`not_queried`), for the UI trace panel

### `src/mcp_server/server.py` + `src/mcp_client.py`
The MCP integration. `mcp_server/server.py` is a standalone `FastMCP` server exposing
`query_metrics`/`query_logs` as MCP tools — thin wrappers around `query_logs.py`'s existing
query functions, condensing Prometheus's raw time-series to latest-value-only
(`_condense_metrics`) and running log analysis server-side so the model gets a compact,
structured result instead of a raw dump. If Prometheus/Loki is unreachable, the tool returns
`source: "unavailable"` with no data and a `message` explaining the service couldn't be
reached — there is no fallback. `mcp_client.py` is a sync wrapper (background asyncio
loop + long-lived stdio session) so the rest of the codebase — ChatGroq, Gradio — doesn't need
to be async-native; `IncidentPilot` spawns this once and reuses the session across every query.

### `src/query_logs.py`
Data layer for live metrics/logs — used by `mcp_server/server.py`, not called directly by
`incident_pilot.py` anymore:
- `query_prometheus()` — GET `localhost:9090/api/v1/query_range`, returns `None` if unreachable
- `query_loki()` — GET `localhost:3100/loki/api/v1/query_range`, returns `None` if unreachable

Plus log analysis:
- `analyze_logs()` — extracts levels, groups patterns via `_normalize_message()`, detects error clusters (bursts within 30s)
- `analyze_traces()` — groups by `trace_id`, reconstructs journeys, finds break points
- `_extract_level()`, `_extract_message()`, `_normalize_message()`, `_timestamp_diff()`, `_try_parse_timestamp()`

### `src/app.py`
Gradio UI. Shows data source badge (🟢 Live / 🔴 Unavailable / ⚪ Not queried this
turn). Calls `pilot.query()` once — no pre-fetch — then derives the badge and trace panel
(including which tool(s) the agent actually called) from `pilot.get_trace()` afterward.

### `flask-generator/`
Docker FastAPI app that simulates production incidents in real-time:
- `app.py` — FastAPI server with background tick loop
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
# All tests via uv
uv run python -m pytest tests/ -v

# Specific suites
uv run python -m pytest tests/test_query_logs.py -v         # 41 tests
uv run python -m pytest tests/test_incident_pilot.py -v     # 27 tests
uv run python -m pytest tests/test_mcp_server.py -v         # 6 tests
uv run python -m pytest tests/test_fastapi_generator.py -v  # 64 tests
```

`test_query_logs.py` and `test_mcp_server.py` mock all network calls — no live stack needed.
`test_incident_pilot.py`'s `TestGuardrailBehaviour` calls the real Groq API (with tools bound) —
`GROQ_API_KEY` must be set; the rest of that file (`TestAgentStructure`, `TestContradictionDetection`)
mocks `ChatGroq` and `MCPClient`, no network needed.

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
  ├─ 1. retrieve() → ChromaDB similarity search → top-3 RAG chunks (always)
  │
  ├─ 2. Build initial prompt: ## Retrieved context (RAG) + engineer's description
  │
  ├─ 3. Action-request check (code-level, not just prompt-level):
  │      matches "roll back / deploy / hotfix / restart / ..."?
  │        yes → model.invoke() with NO tools bound (tool call impossible)
  │        no  → model.bind_tools([query_metrics, query_logs]).invoke()
  │
  ├─ 4. If the model requested tool(s): execute each via a real MCP round
  │      trip to mcp_server/server.py (stdio, one long-lived session),
  │      append ToolMessage(s), invoke again -- repeat up to MAX_TOOL_ROUNDS
  │
  ├─ 5. Contradiction check -- only if query_metrics was actually called;
  │      folds a [Contradiction] flag back in for one more invoke if found
  │
  ├─ 6. Final LLM response → Groq (GROQ_MODEL, default llama-3.1-8b-instant) → cited answer
  │
  └─ 7. UI: badge (live|unavailable|not_queried) + trace
         panel (which tool(s) were called, with what args/results) + response
```

## Key architectural constraints

1. **Guardrails are both prompt- and code-level** — `prompts/system_prompt.md` Priority 1 tells
   the model to refuse deploy/rollback/hotfix requests without calling a tool, but prompting
   alone isn't reliable enough (observed the model attempt a tool call against this instruction
   during testing) — so `incident_pilot._looks_like_action_request()` also skips binding tools
   entirely for matching messages, making a tool call structurally impossible for them, not just
   discouraged. Guardrails work even if the vector store or MCP server is unavailable.

2. **RAG is pre-computed and always-on** — `ingestion.py` chunks and embeds all documents
   offline. At query time, only the user's query is embedded (~100ms) and searched (~50ms).
   Unlike the two telemetry tools, RAG retrieval is not agent-decided — it runs on every query.

3. **Telemetry is agent-decided, via MCP** — the LLM itself decides whether/which of
   `query_metrics`/`query_logs` to call, based on tool docstrings + system-prompt guidance (see
   `prompts/system_prompt.md`, "Deciding whether to call a telemetry tool"). Log analysis
   (`analyze_logs()`/`analyze_traces()`) and metrics condensing (`_condense_metrics()`) both
   happen server-side in `mcp_server/server.py`, so the model receives a structured, compact
   result — never a raw log/time-series dump.

4. **There is no fallback data source** — each MCP tool call talks to live Prometheus/Loki only;
   if the live endpoint is unreachable, the tool returns `source: "unavailable"` with no data
   (and a `message` explaining why) instead of substituting stale synthetic data. The system
   prompt requires the agent to tell the engineer plainly when this happens rather than present
   anything as a live-data diagnosis. The UI badge reflects whichever tool(s) were actually
   called this turn — `not_queried` if the agent judged neither was needed.

5. **The `phase` field in synthetic metrics** (`baseline`/`climbing`/`plateau`/`recovering`) is a dataset-layer annotation only — it is stripped before returning data to the agent so the agent doesn't see ground-truth labels it should be inferring.

## Corpus layout

| Directory | Contents |
|---|---|
| `synthetic-data/runbooks/` | Service runbooks for RAG indexing |
| `synthetic-data/postmorterms/` | Past-incident postmortems for RAG indexing |
| `synthetic-data/vectorstore/` | ChromaDB (built by `ingestion.py`, not committed) |
| `flask-generator/` | Docker FastAPI incident simulator |
| `docs/` | Generation prompts and team context |
| `prompts/` | System prompt and generation prompts |

## Dependency management

Dependencies are declared in `pyproject.toml` (root) and `flask-generator/pyproject.toml`.

- `uv sync` — install all project dependencies into `.venv`
- `uv sync --group test` — include test dependencies (pytest, httpx)
- `uv sync --no-dev` — production install (no test/dev groups)
- `uv lock` — update `uv.lock` after changing dependencies
- `uv pip install torch --index-url https://download.pytorch.org/whl/cpu` — torch must use a special index

## Conventions for runbooks and postmortems

See `docs/RUNBOOK_GENERATION_PROMPT.md` and `docs/POSTMORTEM_GENERATION_PROMPT.md` for full generation guidelines.

Key constraints:
- Frontmatter: only `service` and `doc_type` fields — no extras.
- Separate triage paths for `p99-latency-high` vs `error-rate-high`.
- No rollback/deploy instructions in the runbook.
- Every `##` section must be self-contained (file is chunked on headers).
- Postmortem titles by symptom/impact, not root cause.
- Action items table: max 2 rows, independently verifiable.
