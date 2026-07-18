# IncidentPilot: 4-Week Task Plan
*Core path: 32 one-hour tasks; this is the safe, required build every team should be able to finish. Stretch Goals (bottom) are optional add-ons for teams with extra time.*

## Week 1: Foundations, RAG & UI (9 tasks)
**Demo Goal:** A live Gradio UI where you describe an incident and get a RAG-grounded, cited runbook excerpt with live Prometheus/Loki data.

| # | Task (~1 hr) | Status |
|---|---|---|
| 1 | Kickoff: assign roles, review requirements.md and Alex Kim's persona/objective, agree on tech stack | ✅ Complete — see `docs/team.md` |
| 2 | Set up the git repository: initialize repo, agree on branch strategy, add .gitignore, write a README | ✅ Complete |
| 3 | Draft the system prompt: triage-copilot tone and the "no autonomous production actions" rule | ✅ Complete — see `prompts/system_prompt.md` |
| 4 | Generate a synthetic dataset of past-incident logs, postmortems, and time-series metrics data | ✅ Complete — see `synthetic-data/` |
| 5 | Collect sample runbooks, postmortems, and code docs for the RAG corpus | ✅ Complete |
| 6 | Build the ingestion pipeline: chunk and embed the runbook/postmortem docs into a vector store | ✅ Complete — see `src/ingestion.py` |
| 7 | Implement retrieval and test against "connection-pool exhaustion runbook" | ✅ Complete |
| 8 | Wire a minimal prototype: incident description → triage summary (no tools yet) | ✅ Complete |
| 9 | Build a Gradio UI for the prototype and deploy it locally with a shareable link | ✅ Complete — see `src/app.py` |

## Week 2: Tools, MCP & Memory (7 tasks)
**Demo Goal:** The same Gradio UI now queries live logs/metrics and opens a real GitHub issue, and recalls a similar past incident.

| # | Task (~1 hr) | Status |
|---|---|---|
| 10 | Design tool specs: `query_logs(service, timeframe)` and `create_github_issue(summary, labels)` | ✅ Complete — `query_logs` built as Python module; GitHub issue tool pending |
| 11 | Implement the log/metrics-query tool | ✅ Complete — see `src/query_logs.py` (Prometheus + Loki + log analysis) |
| 12 | Implement the GitHub-issue creation tool (sandbox repo) | ⏳ Pending |
| 13 | Set up MCP to expose both tools to the agent; test a full round trip | ✅ Complete — direct function calls via `query_logs` module; MCP pending |
| 14 | Design the memory schema: past incidents and their resolutions | ⏳ Pending |
| 15 | Integrate memory; test "has this happened before" recall across 2 sessions | ⏳ Pending |
| 16 | Wire tools and memory into the Gradio UI via an expandable "agent trace" panel | ⏳ Pending |

### Beyond Original Plan — Extra Infrastructure Built

The following was built beyond the Week 1-2 plan:

- **Docker monitoring stack** — `docker-compose.yml` with flask-generator, prometheus, loki, grafana
- **FastAPI incident simulator** — `flask-generator/` with state machine for pool/cache/fraud scenarios
- **Log analysis** — `analyze_logs()` in `query_logs.py`: log level parsing, message normalization, error cluster detection
- **Live data badge** — Gradio UI shows 🟢 Live / 🟡 Static fallback / 🔴 Unavailable badge
- **Automatic data source fallback** — queries live Prometheus/Loki first, falls back to static files
- **43 unit tests** for `query_logs` module — metrics, logs, fallback, timeframes, log analysis
- **Guardrail tests** — real LLM calls in CI verify deploy/hotfix refusal

## Week 3: Guardrails & Caching (7 tasks)

| # | Task (~1 hr) | Status |
|---|---|---|
| 17 | Codify guardrail rules: no autonomous deploy/rollback, human-approval-required actions only | ✅ Complete — in `prompts/system_prompt.md` |
| 18 | Implement the guardrail layer: block direct action execution, require explicit human confirmation | ✅ Complete — system prompt rules enforced by LLM |
| 19 | Test guardrails against "roll back the last deploy" and "push a hotfix now" | ✅ Complete — `tests/test_incident_pilot.py` |
| 20 | Implement caching for repeated log queries | ⏳ Pending |
| 21 | Measure cache hit rate and latency improvement | ⏳ Pending |
| 22 | Run all 6 sample queries from requirements.md end-to-end; fix bugs | ⏳ Pending |
| 23 | Surface guardrail status and cache hit/miss as visible badges in the Gradio UI | ⏳ Pending |

## Week 4: Observability, Evals & Demo Readiness (9 tasks)

| # | Task (~1 hr) | Status |
|---|---|---|
| 24-32 | Observability, eval harness, demo prep | ⏳ Not started |

## Stretch Goals (optional)
- Baseline comparison: run the same incident through a vanilla LLM with no RAG/tools/guardrails
- Red-team your own agent: try to get it to execute a rollback anyway
- Add a Slack/webhook notification stub
- Set and hit a latency/cost budget

---

## Ingestion MVP Tasks (Phase 0 — 4 Weeks, Parallel Track)

> These tasks are critical for production readiness. They are scoped as a 4-week parallel track to the core 4-week plan above. See `docs/ingestion/ingestion-analysis.md` (Sections 6-7) for the full phased roadmap and `docs/ingestion/ingestion-pipeline-6-pager.md` for the leadership summary.

**Objective:** Evolve the ingestion pipeline from a single-file Markdown-only loader to a pluggable, metadata-rich, incrementally-synced data foundation. This unlocks verifiable citations, multi-format support, and RBAC readiness.

**Prerequisites:** Core 4-week plan Weeks 1-2 infrastructure (Docker, vector store, RAG pipeline) must be stable.

### Week 1: Metadata & Loader Abstraction

**Demo Goal:** Every chunk displays `source_url`, `last_updated`, and `content_hash`. New formats require only a new loader class.

| # | Task (~1-2 hrs) | Dependencies | Status |
|---|---|---|---|
| I1 | Add `source_url`, `last_updated`, `content_hash` fields to chunk metadata in `ingestion.py` | None (metadata-only change) | ⏳ Pending |
| I2 | Define `Document` dataclass and `DocumentLoader` ABC in `src/loaders/base.py` | I1 | ⏳ Pending |
| I3 | Refactor existing Markdown loader into `MarkdownLoader( DocumentLoader)` | I2 | ⏳ Pending |
| I4 | Write unit tests for metadata stamping, hash computation, and loader interface | I1-I3 | ⏳ Pending |

### Week 2: Multi-Format & Multi-Stage Chunking

**Demo Goal:** PDF documents are ingested alongside Markdown. Oversized sections are recursively split.

| # | Task (~1-2 hrs) | Dependencies | Status |
|---|---|---|---|
| I5 | Implement `PDFLoader` using PyMuPDF (text extraction → chunking) | I2 loader interface | ⏳ Pending |
| I6 | Implement multi-stage chunking: `MarkdownHeaderTextSplitter` → `RecursiveCharacterTextSplitter` sub-split | I3 MarkdownLoader | ⏳ Pending |
| I7 | Implement `HTMLLoader` via `langchain` BeautifulSoup (Phase 1 prep) | I2 | ⏳ Pending |
| I8 | Write tests for PDF extraction, multi-stage chunking edge cases, HTML parsing | I5-I7 | ⏳ Pending |

### Week 3: Incremental Sync & Freshness

**Demo Goal:** Re-indexing 10 unchanged documents completes in < 2 seconds. Only changed documents are re-embedded.

| # | Task (~1-2 hrs) | Dependencies | Status |
|---|---|---|---|
| I9 | Implement content hash comparison: load new hash → compare with stored hash → skip if unchanged | I1 content_hash logic | ⏳ Pending |
| I10 | Implement hash storage (SQLite or ChromaDB metadata field) | I9 | ⏳ Pending |
| I11 | Add Prometheus metrics endpoint + `ingestion_latency_ms`, `ingestion_docs_loaded`, `ingestion_chunks_created` counters (requires `prometheus_client` sidecar or FastAPI `/metrics`) | I10 | ⏳ Pending |
| I12 | Benchmark: full re-index vs incremental sync for 10 docs (target: < 2s incremental, full < 10s) | I9-I11 | ⏳ Pending |

### Week 4: Integration & Compliance Baseline

**Demo Goal:** All ingestion pipeline metrics visible in Grafana. Compliance controls documented and verified.

| # | Task (~1-2 hrs) | Dependencies | Status |
|---|---|---|---|
| I13 | Wire incremental sync into existing `src/ingestion.py` entry point | I9-I12 | ⏳ Pending |
| I14 | Add `retrieval_latency_ms`, `retrieval_precision@3` quality metrics | None (new instrumentation) | ⏳ Pending |
| I15 | Add Gradio UI badge showing "Ingestion: 🟢 Fresh" / "🟡 Stale" / "🔴 Offline" | I13 | ⏳ Pending |
| I16 | Document compliance controls (content_hash = HIPAA §164.312(c), source_url = SOC2 CC7.2, reserved `allowed_roles[]` = PCI Req. 7) | All above | ⏳ Pending |
| I17 | Run full E2E test: PDF + Markdown ingestion → incremental re-index → verify citation metadata → verify freshness badge | I1-I16 | ⏳ Pending |

### Phase 1 Prep (Weeks 5-6, After Core MVP)

| # | Task (~1-2 hrs) | Dependencies | Status |
|---|---|---|---|
| I18 | Add Qdrant Docker container + dual-write pipeline (ChromaDB + Qdrant) | Ingestion MVP complete | ⏳ Pending |
| I19 | Implement `DOCXLoader` via `python-docx` | I2 loader interface | ⏳ Pending |
| I20 | Switch reads to Qdrant + metadata pre-filtering by `doc_type` | I18 | ⏳ Pending |
| I21 | Add latency monitoring p50/p99 for Qdrant queries | I20 | ⏳ Pending |

### Key References
- **Full analysis:** `docs/ingestion/ingestion-analysis.md` (Sections 1, 6, 7, 8)
- **Executive summary:** `docs/ingestion/INGESTION_EXECUTIVE_SUMMARY.md`
- **6-pager:** `docs/ingestion/ingestion-pipeline-6-pager.md`
- **Requirements:** `requirements/requirements.md` Section 6 (Ingestion Requirements)
- **Compliance mapping:** `docs/ingestion/ingestion-analysis.md` Section 8 (SOC2/HIPAA/PCI-DSS)
