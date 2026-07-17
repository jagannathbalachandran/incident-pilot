# IncidentPilot — Team & Project Status

## Tech Stack
- Python 3.11
- LangChain + ChatGroq (LLM)
- HuggingFace all-MiniLM-L6-v2 (embeddings)
- ChromaDB (vector store)
- Gradio 4.x (UI)
- Flask (incident simulator)
- Docker Compose (Prometheus + Loki + Grafana)

## Roles
- **All:** AI Engineer + DevOps roles
- **Prabhat:** SDET (testing, quality)
- **Jagannath:** PM (project management, requirements)

## Features Status

| Feature | Status | Notes |
|---|---|---|
| Prompt / RAG | ✅ **Done** | System prompt with guardrails, priority rules, contradiction detection. ChromaDB vector store with `##`-header chunking. |
| Tools / MCP | ✅ **Done** | `query_logs()` — Prometheus + Loki with static fallback. `analyze_logs()` — structured log analysis. |
| Guardrails | ✅ **Done** | Prompt-level safety rules (Priority 1 — unconditional refusal of deploy/rollback/hotfix). Tested with real LLM. |
| Contradiction Detection | ✅ **Done** | Code-level (4 static methods with metric thresholds) + prompt-level (data-first principle). 17 unit tests. |
| Trace Panel | ✅ **Done** | Expandable Gradio accordion showing RAG chunks, metrics, log analysis, full prompt, request ID. |
| Request-ID Tracing | ✅ **Done** | `setLogRecordFactory()` on Gradio side, `contextvars` + `_json_resp()` on Flask side. Logs show `[req=...]`. |
| Observability | ✅ **Done** | Grafana dashboards (Incidents folder), Prometheus metrics, Loki log aggregation. |
| Memory | ⏳ **Pending** | Cross-session recall of past incidents. |
| Caching | ⏳ **Pending** | Cache layer for repeated queries. |
| GitHub Issues | ⏳ **Pending** | `create_github_issue()` tool for tracking incidents. |
