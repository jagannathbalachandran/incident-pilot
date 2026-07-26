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
Builds ChromaDB vector store from `synthetic-data/runbooks/` + `synthetic-data/postmorterms/`
(both markdown; YAML frontmatter stripped). Chunked with `SemanticChunker` — embeds sentences
and splits where meaning shifts significantly (95th percentile breakpoint), not a `##`-header
split. Any chunk exceeding `MAX_CHUNK_CHARS` (1500) gets a secondary
`RecursiveCharacterTextSplitter` pass. Format-agnostic PDF/DOCX extractors
(`extract_pdf_text`/`extract_docx_text`) for `synthetic-data/real-runbooks/` are defined but
currently unused — that source was swapped out in favor of the markdown runbooks. Only needs
re-running when the corpus changes; the vector store is volume-mounted into the `incident-pilot`
container, but a container **restart** is still needed after re-ingesting (its in-process Chroma
client holds a connection to the old sqlite files, which ingestion deletes and recreates).

### `src/incident_pilot.py`
Core `IncidentPilot` class. Key methods:
- `_expand_query(user_input)` — HyDE: sends the query to the LLM (plain `self.model.invoke`, no
  tools bound), which returns 5 targeted search queries in runbook vocabulary; the original query
  is prepended, capped at 6 total (`HYDE_PROMPT`)
- `_retrieve_with_queries(queries)` — runs `similarity_search_with_score` for every one of the 6
  queries (`CHUNKS_PER_QUERY=3` each, up to 18 raw hits), deduplicates by content keeping each
  chunk's best (lowest-distance) score across whichever queries matched it, ranks the deduplicated
  set by that score, and returns only the top `MAX_RETRIEVED_CHUNKS` (6) — capped because an
  uncapped block (commonly 12-15 unique chunks) plus the system prompt and tool schemas exceeds
  `llama-3.1-8b-instant`'s 6000 tokens-per-minute ceiling on the very first call
- `retrieve(query)` — convenience wrapper: `_expand_query` then `_retrieve_with_queries` (always runs)
- `query(user_input, service=None)` — full pipeline: HyDE + RAG retrieval, then a bounded
  tool-calling loop (`model.bind_tools([query_metrics, query_logs]).invoke(...)`) where **the LLM
  decides** whether/which tool to call, executes real MCP round trips for whatever it asks for,
  folds the results back in as `ToolMessage`s, then produces a cited answer. Every phase (HyDE,
  retrieval, each LLM/tool-call round, contradiction check) is timed with `time.perf_counter()`
  and logged
- `_looks_like_action_request(text)` / `MAX_TOOL_ROUNDS` — code-level guardrail backstop: messages
  matching deploy/rollback/hotfix/config-change verbs never get tools bound at all for that call,
  so a tool call is impossible (not just prompted-against) on those messages
- `_detect_contradictions(...)` — only meaningful if `query_metrics` was actually called this turn
- `get_trace()` — HyDE queries, RAG chunks, which tool(s) were called with what args/results, data
  source (`live`/`unavailable`/`not_queried`), and per-phase `timings`, for the UI trace panel

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
(including which tool(s) the agent actually called, and a per-phase timing breakdown from
`trace["timings"]`) from `pilot.get_trace()` afterward. `triage()` wraps the `pilot.query()` call
in `try/except`: without it, an exception (e.g. a Groq 429/413) left Gradio showing the
*previous* successful response with just a generic error toast — easy to misread as "this
response is wrong" rather than "this attempt failed." Failures now return a clear error message
instead.

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
uv run python -m pytest tests/test_incident_pilot.py -v     # 33 tests (incl. TestHydeQueryExpansion)
uv run python -m pytest tests/test_mcp_server.py -v         # 6 tests
uv run python -m pytest tests/test_fastapi_generator.py -v  # 83 tests
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

Lifecycle durations (accelerated mode, 1s = 1 simulated minute). Each kind's steady-state phase
(pool's `plateau`, cache's `failover`, fraud's `active`) was extended from its original
15s/6s/20s to 120s (2 real minutes) so there's actually enough time to run a triage query against
a live incident before it moves into recovery:
- **Pool**: 15s climbing + 120s plateau + 10s recovery = ~145s total
- **Cache**: 120s failover + 12s warming = ~132s total
- **Fraud**: 120s active

## Data flow (query → response)

```
User query
  │
  ├─ 1. HyDE expansion (_expand_query) -- self.model.invoke(), no tools bound:
  │      engineer's raw description → 5 LLM-generated runbook-vocabulary
  │      search queries + the original = 6 queries total (1 LLM call)
  │
  ├─ 2. RAG retrieval (_retrieve_with_queries) -- always runs:
  │      similarity_search_with_score(query, k=3) for each of the 6 queries
  │      → dedupe by content, keeping each chunk's best score
  │      → rank by score → top MAX_RETRIEVED_CHUNKS (6) sent to the LLM
  │
  ├─ 3. Build initial prompt: ## Retrieved context (RAG) + engineer's description
  │
  ├─ 4. Action-request check (code-level, not just prompt-level):
  │      matches "roll back / deploy / hotfix / restart / ..."?
  │        yes → model.invoke() with NO tools bound (tool call impossible)
  │        no  → model.bind_tools([query_metrics, query_logs]).invoke()
  │
  ├─ 5. If the model requested tool(s): execute each via a real MCP round
  │      trip to mcp_server/server.py (stdio, one long-lived session),
  │      append ToolMessage(s), invoke again -- repeat up to MAX_TOOL_ROUNDS.
  │      If MAX_TOOL_ROUNDS is hit with calls still pending, force a final
  │      textual answer from whatever was gathered (self.model.invoke(),
  │      no tools) instead of looping further.
  │
  ├─ 6. Contradiction check -- only if query_metrics was actually called;
  │      folds a [Contradiction] flag back in for one more invoke if found
  │
  ├─ 7. Final LLM response → Groq (GROQ_MODEL) → cited answer. Citation rules
  │      (prompts/system_prompt.md) require translating each RAG block's
  │      `[Source: <file> | Section: <section>]` tag into `[Runbook: ...]` /
  │      `[Postmortem: ...]` in the answer -- weaker models are prone to
  │      dropping this and answering from tool results alone.
  │
  └─ 8. UI (app.py `triage()`, wrapped in try/except so a failure returns a
         clear error instead of leaving a stale prior response on screen):
         badge (live|unavailable|not_queried) + trace panel (HyDE queries,
         RAG chunks, which tool(s) were called with what args/results, and a
         per-phase timing breakdown from get_trace()["timings"]) + response
```

## Key architectural constraints

1. **Guardrails are both prompt- and code-level** — `prompts/system_prompt.md` Priority 1 tells
   the model to refuse deploy/rollback/hotfix requests without calling a tool, but prompting
   alone isn't reliable enough (observed the model attempt a tool call against this instruction
   during testing) — so `incident_pilot._looks_like_action_request()` also skips binding tools
   entirely for matching messages, making a tool call structurally impossible for them, not just
   discouraged. Guardrails work even if the vector store or MCP server is unavailable.

2. **RAG is pre-computed and always-on, but query-time retrieval is HyDE-driven, not a single
   search** — `ingestion.py` chunks and embeds all documents offline. At query time, the
   engineer's raw query is first expanded by the LLM into 6 targeted queries (HyDE), each
   searched independently, then deduplicated/ranked/capped (see `src/incident_pilot.py` above).
   This costs one extra LLM call and up to 18 vector searches per turn, versus a single
   `similarity_search()` — a deliberate accuracy-for-latency/token-budget tradeoff. Unlike the
   two telemetry tools, RAG retrieval itself is not agent-decided — it always runs.

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

6. **Citation labels require an explicit translation step the model must perform** — the RAG
   block presents chunks as `[Source: <filename> | Section: <section>]`, but
   `prompts/system_prompt.md` requires the final answer to cite `[Runbook: ...]` /
   `[Postmortem: ...]` instead. The system prompt now spells out that mapping and a worked
   example (previously it didn't, and models — especially smaller ones — would often paraphrase
   the runbook in prose without the bracket tag, or ignore RAG entirely in favor of live tool
   results). Still worth re-verifying whenever the prompt or a stronger model is available.

7. **`GROQ_MODEL` has two real options with separate daily (TPD) budgets, and both are easy to
   exhaust during iterative testing**: `llama-3.3-70b-versatile` (better instruction-following,
   100k TPD) and `llama-3.1-8b-instant` (weaker, but its own separate budget — and only a 6000
   tokens-per-minute *per-request* ceiling, which the capped/ranked RAG block above exists to fit
   under). Set in `.env`; check `docker exec incident-pilot printenv GROQ_MODEL` to confirm what
   the running container actually has, and rebuild+restart after changing it.

## Corpus layout

| Directory | Contents |
|---|---|
| `synthetic-data/runbooks/` | Service runbooks (markdown) — indexed for RAG |
| `synthetic-data/postmorterms/` | Past-incident postmortems (markdown) — indexed for RAG |
| `synthetic-data/real-runbooks/` | PDF/DOCX runbooks — extractors exist in `ingestion.py` but this source is currently unused |
| `synthetic-data/vectorstore/` | ChromaDB (built by `ingestion.py`, not committed) |
| `flask-generator/` | Docker FastAPI incident simulator |
| `docs/` | Generation prompts, team context, and design notes (incl. `hyde_semantic_chunking_design.md`, `rag_chunking_retrieval_design.md`) |
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
- Every `##` section should still be self-contained — `SemanticChunker` splits on meaning shifts rather than headers, but well-scoped sections make for cleaner chunk boundaries either way.
- Postmortem titles by symptom/impact, not root cause.
- Action items table: max 2 rows, independently verifiable.
