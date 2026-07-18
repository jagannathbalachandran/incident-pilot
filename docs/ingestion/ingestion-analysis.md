# Ingestion Pipeline: Codebase-Grounded Analysis & Phased Roadmap

> **Analysis date:** 2026-07-17  
> **Project:** IncidentPilot — AI-powered incident-response copilot  
> **Methodology:** All findings derived from direct examination of `src/ingestion.py`, `src/incident_pilot.py`, `src/query_logs.py`, `flask-generator/`, `synthetic-data/`, `prompts/`, and `docs/`.  
> **Maturity model reference:** L0–L6 Enterprise Agent Framework (see `docs/incident-pilot-6-pager.md`)

---

## Table of Contents

1. [Codebase-Derived Problem Statement](#1-codebase-derived-problem-statement)
   - 1.5 [Data Types in Incident Copilot](#15-data-types-in-incident-copilot)
       - 1.5.1 [Knowledge Documents](#151-knowledge-documents-rag-corpus)
       - 1.5.2 [Time-Series Metrics](#152-time-series-metrics-prometheus)
       - 1.5.3 [Application Logs](#153-application-logs-loki)
       - 1.5.4 [Incident Simulator Data](#154-incident-simulator-data)
       - 1.5.5 [Episodic Memory](#155-episodic-memory-future--mnemon)
       - 1.5.6 [Service Topology](#156-service-topology-future--graphify--neo4j)
       - 1.5.7 [System Prompts & Config](#157-system-prompts--configuration)
       - 1.5.8 [Data Type Summary Matrix](#158-data-type-summary-matrix)
2. [Enterprise Agent Context](#2-enterprise-agent-context)
3. [Current Implementation Assessment](#3-current-implementation-assessment)
4. [Tool Analysis & Comparison](#4-tool-analysis--comparison)
   - 4.1 [RAG Frameworks: LangChain vs LlamaIndex](#41-rag-frameworks-langchain-vs-llamaindex)
   - 4.2 [Chunking Strategies](#42-chunking-strategies)
   - 4.3 [Embedding Models](#43-embedding-models)
   - 4.4 [Vector Databases with RBAC](#44-vector-databases-with-rbac)
   - 4.5 [Episodic Memory](#45-episodic-memory)
   - 4.6 [Knowledge Graph for Service Topology](#46-knowledge-graph-for-service-topology)
5. [Enterprise Multi-Agent Architecture](#5-enterprise-multi-agent-architecture)
6. [Phased Implementation Plan (L0–L6 Maturity)](#6-phased-implementation-plan-l0l6-maturity)
7. [Ingestion Pipeline Phased Input Complexity Plan](#7-ingestion-pipeline--phased-input-complexity-plan)
   - 7.0 [Phase 0 — Foundation](#phase-0--foundation-l0--l1-weeks-1-4)
   - 7.1 [Phase 1 — Vector DB & RBAC](#phase-1--vector-db--rbac-foundation-l1-weeks-5-6)
   - 7.2 [Phase 2 — Service Topology](#phase-2--service-topology--monitoring-data-l2-weeks-7-10)
   - 7.3 [Phase 3 — Diagnostic & MCP](#phase-3--diagnostic-data--mcp-tools-l3-weeks-11-14)
   - 7.4 [Phase 4 — RBAC, Memory & Connectors](#phase-4--rbac-episodic-memory--external-source-connectors-l4--l5-weeks-15-20)
   - 7.5 [Phase 5 — Autonomous Ops](#phase-5--autonomous-operations-l6-future)
8. [Compliance Mapping: SOC2, HIPAA, PCI-DSS](#8-compliance-mapping-soc2-hipaa-pci-dss)
9. [Decision Matrix](#9-decision-matrix)
10. [References & Sources](#10-references--sources)

---

## 1. Codebase-Derived Problem Statement

This analysis is grounded entirely in the existing codebase. Every finding below was identified by tracing the actual data flow through `src/ingestion.py`, `src/incident_pilot.py`, `src/query_logs.py`, and the supporting files.

### Background: How the Current System Works

The current `src/ingestion.py` pipeline:
1. Reads `.md` files from `synthetic-data/runbooks/` and `synthetic-data/postmorterms/`
2. Strips YAML frontmatter using regex
3. Splits on `##` headers via `MarkdownHeaderTextSplitter`
4. Embeds chunks with `all-MiniLM-L6-v2` (384-dim, CPU)
5. Stores in local ChromaDB at `synthetic-data/vectorstore/`
6. On every run, **deletes and recreates** the entire vector store

For live data (`src/query_logs.py`), the system:
- Queries Prometheus (`localhost:9090`) for metrics with `__name__=~"checkout_.*"`
- Queries Loki (`localhost:3100`) for logs with `{service="checkout-api"}`
- Falls back to static JSON/JSONL files when live endpoints are unreachable
- Runs `analyze_logs()` to produce a structured summary (never sends raw logs to the LLM)

### Identified Gaps (Derived from Codebase)

#### Gap 1: Single-Format, Single-Source Ingestion

**Evidence in code:** `src/ingestion.py` lines 47-53 — `load_documents()` only globs `*.md` files from two hardcoded directories. The `MarkdownHeaderTextSplitter` is the only chunker. Adding a PDF would require modifying `load_documents()`.

```python
# Line 51-52 of ingestion.py — only .md files, hardcoded paths
for path in sorted(directory.glob("*.md")):
    text = path.read_text()
```

**Impact:** Only `.md` runbooks and postmortems from local filesystem are ingested. Confluence pages, PDF postmortems, Slack threads, Jira tickets — none of these can enter the pipeline without code changes.

#### Gap 2: No Access Control on Chunks

**Evidence in code:** `src/ingestion.py` line 83 — the only metadata stored is `source` (filename) and `section` (header name). There is no `allowed_roles` field, no RBAC filtering, no mechanism to restrict chunk access per user role.

```python
# Line 83 — metadata has no security fields
chunk.metadata["source"] = source  # filename only
```

**Impact:** Every user query sees every document. Sensitive runbooks containing network topology or PII are accessible to anyone who can query the vector store.

#### Gap 3: No Traceability or Freshness Tracking

**Evidence in code:** `src/ingestion.py` line 89 — the vector store is **deleted and recreated** on every run (`shutil.rmtree` + `mkdir`). There is no content hash, no last-updated timestamp, no version tracking.

```python
# Line 89 — full rebuild every time, no tracking
if VECTORSTORE_DIR.exists():
    shutil.rmtree(VECTORSTORE_DIR)  # no incremental sync
```

**Impact:** Engineers cannot verify which version of a runbook produced a citation. If a runbook is updated, there is no way to detect staleness. Every re-index is a full rebuild.

#### Gap 4: Single-Service Scope

**Evidence in code:** `src/query_logs.py` line 39 — `DEFAULT_SERVICE = "checkout-api"`. All Prometheus and Loki queries are scoped to a single service. The system cannot reason across services.

```python
# Line 39 — single service hardcoded
DEFAULT_SERVICE = "checkout-api"
```

**Impact:** Cross-service cascading failures (e.g., `checkout-api` failing because `payment-api` is down) cannot be detected or diagnosed. Each runbook is also scoped to a single service.

#### Gap 5: Tightly Coupled Ingestion Pipeline

**Evidence in code:** `src/ingestion.py` — everything happens in a single procedural script: loading, frontmatter stripping, chunking, embedding, and storing. These steps cannot be independently configured, tested, or replaced.

**Impact:** Swapping the embedding model, changing the chunking strategy, or adding a new data source all require modifying the same monolithic file.

#### Gap 6: No Episodic Memory

**Evidence in code:** `src/incident_pilot.py` — the `query()` method runs RAG + live data on every invocation but has no mechanism to store past queries, resolutions, or incident patterns. The agent never learns from previous incidents.

**Impact:** Every incident is handled as if it's the first time. Past resolutions are lost unless manually documented.

#### Gap 7: No Service Topology Knowledge

**Evidence in code:** `src/incident_pilot.py` — the agent has no awareness of which services depend on which. The `_classify_data()` method uses hardcoded metric thresholds for a single service. There is no graph or dependency model.

### System-Level Constraints (Derived from Architecture)

1. **ChromaDB limitation** — no native RBAC, no payload indexing, no JWT support. Metadata filtering is possible via `where` clauses but there are no indexes on filter fields.
2. **all-MiniLM-L6-v2** — 384-dim embedding, CPU-only, lower accuracy than newer open-source alternatives like BGE-Large.
3. **Full rebuild** — ~5-10 seconds for current small corpus (10 docs), but doesn't scale.
4. **Gradio UI** — stateless; no user authentication, no session management, no audit trail.
5. **`analyze_logs()`** — the structured log summary approach is good architectural practice (no raw logs to LLM), but the analysis is limited to level counts and pattern grouping.

---

## 1.5 Data Types in Incident Copilot

IncidentPilot ingests **7 distinct data types**, each with different ingestion characteristics, security requirements, and maturity levels. Understanding this taxonomy is critical — each data type requires a different loader, chunking strategy, and RBAC policy.

| # | Data Type | Source(s) | Format(s) | Current State | Target Maturity | RBAC Required |
|---|---|---|---|---|---|---|
| 1 | **Knowledge Documents** | Runbooks, Postmortems, Architecture docs | `.md` (Markdown) | ✅ `ingestion.py` → ChromaDB | L0→L5+ | ✅ L4+ |
| 2 | **Time-Series Metrics** | Prometheus (live) / JSON files (fallback) | PromQL response / `.json` | ✅ `query_logs.py` → live API | L0→L2 | ❌ Open data |
| 3 | **Application Logs** | Loki (live) / JSONL files (fallback) | LogQL response / `.jsonl` | ✅ `query_logs.py` → live API | L0→L2 | ❌ Open data |
| 4 | **Incident Simulator Data** | Flask generator engine | Runtime state machine | ✅ `flask-generator/` → API | Dev tool | ❌ Dev-only |
| 5 | **Episodic Memory** | Mnemon (future) | Go binary + SQLite | 🔴 Not yet implemented | L5+ | ✅ L5+ |
| 6 | **Service Topology** | Graphify + Neo4j (future) | Cypher graph DB | 🔴 Not yet implemented | L2+ | ✅ L4+ |
| 7 | **System Prompts & Config** | Prompt files, config, thresholds | `.md`, `.yml`, `.env` | ✅ File system (manual) | L0 | ❌ Read-only |

---

### 1.5.1 Knowledge Documents (RAG Corpus)

The primary RAG data type. Currently only Markdown runbooks and postmortems; must expand to PDF, Confluence, Slack, Jira.

| Aspect | Detail |
|---|---|
| **Examples** | `checkout-api-runbook.md`, `2026-05-checkout-outage.md` |
| **Format** | Markdown with YAML frontmatter, `##` section headers, tables, code blocks |
| **Pipeline** | `load_documents()` → `strip_frontmatter()` → `MarkdownHeaderTextSplitter` → `HuggingFaceEmbeddings` → `Chroma.from_documents()` |
| **Chunking** | MarkdownHeaderTextSplitter on `##` headers — produces ~8–15 chunks per document |
| **Metadata** | `source` (filename) + `section` (header name) only — **needs** `source_url`, `last_updated`, `content_hash`, `allowed_roles[]` |
| **Volume** | ~5–10 documents currently (small); target: 100+ across multiple services |
| **RBAC sensitivity** | **HIGH** — runbooks may contain network topology, credentials, PII remediation steps |
| **Staleness risk** | Runbooks average 14 months stale in enterprise (cited in 6-pager) |

**Enterprise expansion needed:**
```
Current:   .md files on local filesystem
Phase 0:   + PDF (PyMuPDF), HTML, DOCX
Phase 4:   + Confluence (LlamaHub), Slack, Jira (API connectors)
```

---

### 1.5.2 Time-Series Metrics (Prometheus)

Live and historical performance data used for anomaly detection and triage context.

| Aspect | Detail |
|---|---|
| **Examples** | `checkout_p99_latency_ms`, `checkout_error_rate_pct`, `checkout_active_connections`, `checkout_cache_hit_ratio`, `checkout_max_connections` |
| **Source** | Prometheus `query_range` API (live) → falls back to `synthetic-data/metrics/*.json` |
| **Format** | Prometheus response JSON: `{status, data: {result: [{metric, values}]}}` |
| **Pipeline** | `query_prometheus()` → `_load_metrics_fallback()` → `_format_live_data()` extracts latest value per series |
| **Volume** | ~5 metric series per service, sampled every 60s (~720 data points / hour / service) |
| **RBAC sensitivity** | **LOW** — metrics are typically open across SRE teams |
| **Freshness requirement** | Near real-time (60s scrape interval) for L2 proactive monitoring |

**Current limitations:**
- Only a single service (`checkout-api`) is instrumented
- No historical baseline store (Phase 2 requirement)
- `phase` label stripped defensively before returning to agent

---

### 1.5.3 Application Logs (Loki)

Structured and unstructured log entries for debugging incident context.

| Aspect | Detail |
|---|---|
| **Examples** | `2026-05-14T13:50:00Z ERROR could not obtain connection` |
| **Source** | Loki `query_range` API (live) → falls back to `synthetic-data/logs/*.jsonl` |
| **Format** | Loki response JSON → normalized to `{timestamp, line, labels}` dicts |
| **Pipeline** | `query_loki()` → `_load_logs_fallback()` → `analyze_logs()` → structured summary (level breakdown, top patterns, error clusters) |
| **Analysis** | `_extract_level()`, `_extract_message()`, `_normalize_message()` (replaces variables with `*`), cluster detection within 30s windows |
| **Volume** | ~50–200 lines per query window (limited by `limit=100` in Loki query) |
| **RBAC sensitivity** | **MEDIUM** — logs may contain PII, customer IDs, or stack traces revealing vulnerabilities |

**Key insight:** Raw log lines are **never sent to the LLM**. Instead, `analyze_logs()` produces a structured summary (level breakdown, top patterns, error clusters). This is a deliberate architectural decision to limit token consumption and prevent prompt injection via log content.

---

### 1.5.4 Incident Simulator Data

Runtime state machine data generated by the Flask-based incident simulator for development and testing.

| Aspect | Detail |
|---|---|
| **Scenarios** | Pool exhaustion, cache failover, fraud outage, random |
| **Phases** | Pool: `baseline→climbing→plateau→recovering`. Cache: `baseline→failover→warming`. Fraud: `baseline→active` |
| **Format** | In-memory state machine (`incident_scenarios.py`) exposed via REST API (`/api/incidents/{kind}/trigger`, `/api/incidents/state`, `/api/incidents/resolve`) |
| **Exported metrics** | `p99_latency_ms`, `error_rate_pct`, `active_connections`, `cache_hit_ratio`, `max_connections` |
| **Exported logs** | JSONL entries to stdout → captured by Loki logging driver in Docker |
| **RBAC sensitivity** | **NONE** — dev-only tool, not ingested into production knowledge base |

---

### 1.5.5 Episodic Memory (Future — Mnemon)

Planned L5 data type for storing past incidents so the agent can recall similar past events.

| Aspect | Detail |
|---|---|
| **Technology** | Mnemon (Apache 2.0, single Go binary with SQLite) |
| **Graph types** | Temporal (when), Entity (services involved), Causal (root→effect), Semantic (similarity) |
| **Data model** | `remember(description, tags)` → `recall(query, k)` → `link(cause, effect, relation)` |
| **Importance decay** | Old memories fade based on access count and age (automated GC) |
| **Deduplication** | Built-in — same incident auto-detected and consolidated |
| **RBAC sensitivity** | **HIGH** — past incident data may contain PII and security details |
| **Target phase** | Phase 4 (Weeks 18-19) |

---

### 1.5.6 Service Topology (Future — Graphify + Neo4j)

Planned L2 data type for understanding cross-service dependencies during cascade analysis.

| Aspect | Detail |
|---|---|
| **Technology** | Graphify (tree-sitter AST) + Neo4j (graph database) |
| **Generation** | `graphify .` on each microservice repo → AST-parsed call graphs → push to Neo4j |
| **Data model** | Nodes: services, APIs, databases. Edges: `calls`, `depends_on`, `connects_to` |
| **Deterministic** | Code parsing via tree-sitter AST — **no LLM involved** for structural mapping |
| **MCP support** | Graphify exposes graph as an MCP tool for the Diagnostic Agent (Phase 3) |
| **RBAC sensitivity** | **MEDIUM** — topology reveals internal network architecture |
| **Target phase** | Phase 2 (Week 7) |

---

### 1.5.7 System Prompts & Configuration

LLM behavior-defining prompts and threshold configuration files.

| Aspect | Detail |
|---|---|
| **Examples** | `prompts/system_prompt.md` (guardrails), `prompts/RUNBOOK_GENERATION_PROMPT.md`, `prompts/POSTMORTEM_GENERATION_PROMPT.md` |
| **Format** | Markdown with structured sections defining agent persona, constraints, and response format |
| **Pipeline** | Read from filesystem at startup — `SYSTEM_PROMPT_PATH.read_text()` → injected into every `ChatGroq` invocation |
| **RBAC sensitivity** | **LOW** — prompts define agent behavior, not sensitive content |
| **Versioning** | Currently none — prompts managed manually in Git; enterprise versioning needed for compliance |
| **Freshness** | Loaded on startup — requires container restart to pick up changes |

---

### 1.5.8 Data Type Summary Matrix

| Data Type | Lifetime | Update Frequency | Freshness SLA | Agent Usage | L-level Gates |
|---|---|---|---|---|---|
| **Knowledge Documents** | Long | Weekly-monthly (manual) | Post-mortem completeness | RAG retrieval (every query) | L0+ |
| **Metrics** | Ephemeral | Every 60s (scrape) | < 5 min | Live context (every query) | L0+ |
| **Logs** | Ephemeral | Continuous stream | < 5 min | Structured analysis (every query) | L0+ |
| **Simulator Data** | Runtime | On demand (trigger) | Instant | Development tests only | Dev |
| **Episodic Memory** | Persistent | Per-incident resolution | < 1 hour | Recall on similar incidents | L5+ |
| **Service Topology** | Semi-persistent | Per-deployment | < 1 day | Cascade analysis, blast radius | L2+ |
| **System Prompts** | Stable | Per-release | Hours | Agent behavior in every query | L0+ |

---

## 2. Enterprise Agent Context

### 2.1 What Makes This an Enterprise Agent Problem?

The ingestion pipeline is not an isolated data processing concern. It is the **foundation** for an enterprise multi-agent incident response system. Every downstream agent depends on ingestion quality:

| Agent | Depends On Ingestion For | If Ingestion Fails |
|---|---|---|
| **Triage Agent** (L0) | Accurate RAG chunks with citations | Hallucinated remediation steps |
| **Proactive Monitor** (L2) | Fresh runbooks with correct thresholds | False positive alerts from stale data |
| **Diagnostic Agent** (L3) | Service topology + dependency maps | Wrong root cause identified |
| **Remediation Agent** (L4) | Approved runbook steps with RBAC | Unauthorized actions on sensitive systems |
| **Learning Agent** (L5) | Episodic memory + feedback loops | Agent never improves |
| **Autonomous Agent** (L6) | All of the above with audit trails | Unreliable self-healing |

### 2.2 Enterprise Architecture Principles

The ingestion pipeline must follow these enterprise-grade principles:

| Principle | What It Means | How It's Enforced |
|---|---|---|
| **Deterministic Reasoning** | Critical logic is in testable code, not LLM prompts | RBAC resolution, chunking, hashing are code-level |
| **Security-First** | Access control at ingestion time, not an afterthought | ACL metadata stamped on every chunk |
| **Observability** | Every ingested document and query is traceable | Audit logging with `request_id` chains |
| **Graduated Autonomy** | Higher agent maturity levels unlock more capabilities | Phased rollout: L0 → L6 |
| **Structured Communication** | Agents pass typed schemas, not raw text | Pydantic models for all inter-agent data |
| **Human-in-the-Loop** | Critical actions require approval | Approval gate for remediation (L4+) |

### 2.3 Maturity Model: L0–L6

| Level | Name | Capability | Current Status |
|---|---|---|---|
| **L0** | Reactive Q&A | RAG + live data queries + citations | ✅ Complete |
| **L1** | Instrumented | Multi-service metrics, per-service runbooks | 🔄 In progress |
| **L2** | Proactive Monitor | Continuous anomaly detection across all services | 🔴 Phase 2 |
| **L3** | Diagnostic Agent | Read-only tool execution (MCP protocol) | 🔴 Phase 3 |
| **L4** | Remediation Agent | Approved tool execution with approval gates | 🔴 Phase 4 |
| **L5** | Learning Agent | Post-incident learning, episodic memory | 🔴 Phase 4 |
| **L6** | Autonomous Agent | Full lifecycle automation for known incidents | 🔴 Future |

The ingestion pipeline must evolve alongside these maturity levels. This document maps each ingestion capability to the maturity level that requires it.

---

## 3. Current Implementation Assessment

| Feature | Current State (`src/ingestion.py`) | Required L-level | Gap |
|---|---|---|---|
| **Document Sources** | Only local `.md` files | L1+ | No Confluence, Slack, Jira, PDF, HTML, DOCX |
| **Chunking** | `MarkdownHeaderTextSplitter` on `##` headers | L0+ | No table extraction, code block preservation, semantic |
| **Embedding** | `all-MiniLM-L6-v2` (384-dim, CPU) | L0+ | Lower accuracy than modern alternatives |
| **Vector Store** | ChromaDB (persistent, local) | L0+ | No RBAC, no metadata pre-filtering |
| **Metadata** | Only `source` + `section` | L1+ | Missing `source_url`, `last_updated`, `content_hash`, `roles` |
| **RBAC** | None | **L4+** | Any user sees all documents |
| **Pipeline** | Single procedural script | L1+ | Tightly coupled; adding format modifies core code |
| **Re-indexing** | Full rebuild every time | L1+ | No incremental sync; expensive at scale |
| **Episodic Memory** | None | **L5+** | Agent doesn't learn from past incidents |
| **Service Topology** | None | **L2+** | No knowledge graph for cross-service reasoning |
| **Audit Trail** | None | **L4+** | No retrieval audit for compliance |

**Verdict:** The current implementation is a solid **L0** MVP. Enterprise readiness requires evolution through L1–L6, with each level building on the ingestion improvements from previous levels.

---

## 4. Tool Analysis & Comparison

### 4.1 RAG Frameworks: LangChain vs LlamaIndex

| Criteria | **LangChain** (current) | **LlamaIndex** | Recommendation |
|---|---|---|---|
| **Document Loaders** | 100+ via `langchain-community` | 150+ via `LlamaHub` (Confluence, Slack, etc.) | **LlamaHub** — richer RAG-native connectors |
| **Chunking** | Modular splitters (manual metadata) | `SemanticSplitterNodeParser`, auto-metadata | **LlamaIndex** — structure-preserving by default |
| **Metadata Extraction** | Must build custom pipeline | Built-in LLM-based enrichment | **LlamaIndex** — batteries-included |
| **Incremental Indexing** | Not built-in | `DocStore` tracks hashes → supports sync | **LlamaIndex** — first-class feature |
| **RBAC Integration** | Must build custom middleware | Metadata filtering on `VectorStoreIndex` | **Tie** — both need custom work |
| **Agent Framework** | LangGraph (agent orchestration, MCP, HITL) | LlamaIndex Agents (simpler, less mature) | **LangChain** — better for multi-agent systems |
| **Current Codebase** | Already used for Chains, Groq, Chroma, splitters | Would require full migration | **LangChain** — less migration risk |

#### Recommendation: LangChain as orchestrator + LlamaHub for loaders

Rather than migrating entirely to LlamaIndex, we build a `DocumentLoader` abstraction layer that wraps both ecosystems:

```
┌─────────────────────────────────────────┐
│        DocumentLoader Interface          │
├─────────────────────────────────────────┤
│ + load(source) -> List[Document]         │
├──────────────────┬──────────────────────┤
│ MarkdownLoader   │ PDFLoader            │
│ (L0 — current)   │ (L1 — PyMuPDF)       │
├──────────────────┼──────────────────────┤
│ ConfluenceLoader │ SlackLoader          │
│ (L4 — LlamaHub)  │ (L4 — Slack API)     │
├──────────────────┼──────────────────────┤
│ JiraLoader       │ HTML/DOCXLoader      │
│ (L4 — LlamaHub)  │ (L1 — langchain)     │
└──────────────────┴──────────────────────┘
```

**Maturity mapping:** Loader abstraction is an **L1** capability. Multi-source connectors (Confluence, Slack) are **L4**.

---

### 4.2 Chunking Strategies

| Strategy | Strengths | Weaknesses | Best For | Maturity |
|---|---|---|---|---|
| **MarkdownHeaderTextSplitter** | Preserves hierarchy; attaches headers as metadata | Large chunks if headers sparse | Runbooks, postmortems | **L0** ✅ |
| **RecursiveCharacterTextSplitter** | Versatile; custom separators; atomic code blocks | Fragments tables | Code-heavy docs | **L1** |
| **Semantic Chunking** | Splits on meaning; coherent chunks | Computationally expensive | Disorganized docs | **L3** |
| **Late Chunking** | Context-aware embeddings | Complex architecture | Long cross-section docs | **L4** |

#### Recommendation: Multi-stage hybrid approach (L0 → L1 → L3)

```
L0 (current):  MarkdownHeaderTextSplitter (## headers)
     ↓
L1 (upgrade):  RecursiveCharacterTextSplitter sub-split
               + table preservation + code block awareness
     ↓
L3 (future):   Semantic Chunking for disorganized docs
```

---

### 4.3 Embedding Models

| Model | Dim. | Type | Cost | Accuracy | Self-Host | Maturity |
|---|---|---|---|---|---|---|
| **all-MiniLM-L6-v2** (current) | 384 | Open | Free | ⭐⭐ | ✅ CPU | **L0** ✅ |
| **BGE-Large-en-v1.5** | 1024 | Open | Free | ⭐⭐⭐⭐ | ✅ GPU rec. | **L1** → upgrade |
| **text-embedding-3-small** | 1536* | API | ~$0.02/1K pages | ⭐⭐⭐⭐ | ❌ | **L2** if acceptable |
| **Instructor-XL** | 768 | Open | Free | ⭐⭐⭐⭐⭐ | ⚠️ 24GB VRAM | **L3** for complex docs |

#### Recommendation: Upgrade path

```
L0 (current):  all-MiniLM-L6-v2 — fast, free, good enough for MVP
L1 (upgrade):  BGE-Large-en-v1.5 — 15-20% better Recall@3 on technical docs
L3 (optional): Instructor-XL — instruction-tuned for domain-specific retrieval
               OR text-embedding-3-small if API dependency is acceptable
```

---

### 4.4 Vector Databases with RBAC

RBAC is identified as a requirement from codebase analysis (Gap 2). Current ChromaDB has no RBAC support. Below is a comparison of available options, each with different trade-offs:

| Database | RBAC Model | Metadata Filtering | Self-Host | Performance | Maturity |
|---|---|---|---|---|---|
| **ChromaDB** (current) | None | ✅ `where` clause (no indexes) | ✅ Simple | ⭐⭐ (>10K docs) | **L0** ✅ |
| **Qdrant** | ✅ JWT-based, collection-scoped | ✅ Payload indexes | ✅ Docker | ⭐⭐⭐⭐⭐ | **L1** upgrade |
| **Weaviate** | ✅ Native RBAC (roles, collections) | ✅ GraphQL-based | ✅ Docker | ⭐⭐⭐⭐ | **L1** alt. |
| **pgvector** | ✅ PostgreSQL Row-Level Security | ✅ SQL WHERE + JOINs | ✅ Docker | ⭐⭐⭐ | **L1** if on PG |
| **Pinecone** | ✅ Project-level IAM | ✅ Strong | ❌ SaaS | ⭐⭐⭐⭐⭐ | **L2** |

#### RBAC Capability Deep-Dive

| RBAC Requirement | ChromaDB | Qdrant | Weaviate | pgvector |
|---|---|---|---|---|
| **Pre-filter by role** | ❌ Must post-filter (leaks) | ✅ JWT claims → filter | ✅ GraphQL filter | ✅ RLS |
| **Multi-tenant isolation** | ❌ Must build | ✅ Collection-level | ✅ Built-in | ✅ Row-level |
| **PII redaction** | ❌ | ✅ Filter-level | ✅ GraphQL-level | ✅ SQL transforms |
| **Audit logging** | ❌ | ✅ | ✅ | ✅ (pgAudit) |

**Trade-off analysis:**
- **Qdrant** offers JWT-based RBAC with payload indexes (fast filtered search), self-hosted via Docker, and LangChain integration. JWT scoping means no extra DB round-trip for permission checks.
- **Weaviate** offers native RBAC with GraphQL filtering but requires learning the Weaviate query syntax (differs from standard vector search).
- **pgvector** leverages PostgreSQL Row-Level Security, which is battle-tested, but approximate nearest-neighbor search combined with RLS requires iterative index scans (pgvector 0.8.0+) to maintain recall.
- **Pinecone** has the strongest managed RBAC but is SaaS-only — introduces vendor dependency and data residency concerns.

**Current stance:** ChromaDB is sufficient for L0. A migration path to an RBAC-capable store should be evaluated when RBAC becomes required.

---

### 4.5 Episodic Memory

Required for **L5 (Learning Agent)** — the agent must remember past incidents and learn from them.

#### Comparison

| Approach | Implementation | Pros | Cons | Maturity |
|---|---|---|---|---|
| **Custom PostgreSQL + vector** | pgvector table | Full control | 3-4 weeks dev | L5 custom |
| **Custom PostgreSQL + vector** | pgvector table | Full control, battle-tested | 3-4 weeks dev effort | L5 |
| **Mnemon** | Go binary, CLI | Single binary, dedup, decay, no API deps | Newer project, less community | L5 |

**Mnemon** (Apache 2.0) is a single-binary episodic memory store. Key differentiators:
- **Four-graph model** — temporal (when), entity (services), causal (cause→effect), semantic (similarity)
- **Importance decay** — old memories fade via access-count boosting + automated GC
- **Deduplication** — auto-detects duplicate incidents and consolidates
- **No external API keys** — zero external dependencies

**Integration:**
```python
class IncidentMemory:
    def remember(self, cascade_summary, resolution, service):
        # Called after every incident resolution
        subprocess.run(["mnemon", "remember", ...])
    
    def recall_similar(self, query, k=3):
        # Called during triage to find past incidents
        return json.loads(subprocess.run(["mnemon", "recall", ...]))
```

---

### 4.6 Knowledge Graph for Service Topology

Required for **L2 (Proactive Monitor)** — understanding cross-service dependencies for cascade detection.

#### Comparison

| Approach | Implementation | Pros | Cons | Maturity |
|---|---|---|---|---|
| **Manual YAML** | Hand-defined service map | Simple, no dependencies | Stale, inaccurate, doesn't scale | L2 baseline |
| **Graphify** | tree-sitter AST parsing | Auto-generates from code (deterministic), MCP-native | Snapshot (not live), Python dep | L2 |
| **Neo4j + OTel** | Service mesh integration | Live topology from traces | Complex setup, heavy infra | L3+ |

**Graphify** (MIT license) auto-generates knowledge graphs from code repositories:
- **tree-sitter AST** — deterministic code parsing across ~40 languages (no LLM cost)
- **Cross-file link resolution** — calls, imports, inheritance chains
- **Neo4j push** — bootstraps graph DB via `graphify push neo4j`
- **MCP server** — exposes graph queries as MCP tools for agent integration

---

## 5. Enterprise Multi-Agent Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                  IncidentPilot Enterprise — Agent Architecture                │
│                                                                               │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                        Agent Orchestrator                              │   │
│  │  (LangGraph: routes queries, manages lifecycle, aggregates results,   │   │
│  │   enforces HITL checkpoints, maintains audit trail)                   │   │
│  └──────┬──────────┬──────────┬──────────┬──────────┬──────────┬─────────┘   │
│         │          │          │          │          │          │             │
│         ▼          ▼          ▼          ▼          ▼          ▼             │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐│
│  │ Triage   │ │Proactive │ │Diagnostic│ │Remediation│ │ Learning │ │ Document ││
│  │ Agent    │ │ Monitor  │ │ Agent    │ │  Agent   │ │  Agent   │ │  Agent   ││
│  │ (L0)     │ │ (L2)     │ │ (L3)     │ │ (L4)     │ │ (L5)     │ │ (L0-L6)  ││
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘│
│       │            │            │            │            │            │      │
│       ▼            ▼            ▼            ▼            ▼            ▼      │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                      Shared Ingestion Layer                           │   │
│  │                                                                       │   │
│  │  ┌──────────┐  ┌──────────────┐  ┌──────────┐  ┌─────────────────┐  │   │
│  │  │ Document │  │ Multi-Stage  │  │ BGE-Lg /│  │ Qdrant +        │  │   │
│  │  │ Loaders  │─▶│  Chunker     │─▶│ MiniLM  │─▶│ ChromaDB        │  │   │
│  │  │ (plugbl) │  │ (header+rec) │  │ Embedder│  │ (RBAC filtered) │  │   │
│  │  └──────────┘  └──────────────┘  └──────────┘  └─────────────────┘  │   │
│  │                                                                       │   │
│  │  ┌──────────┐  ┌──────────────┐  ┌─────────────┐  ┌──────────────┐ │   │
│  │  │ Mnemon   │  │  Graphify +  │  │ Metadata    │  │ Audit Store  │ │   │
│  │  │ Episodic │  │  Neo4j       │  │ Stamping    │  │ (PostgreSQL) │ │   │
│  │  │ Memory   │  │  (Topology)  │  │ (source_url,│  │ (every query  │ │   │
│  │  │          │  │              │  │  checksum,  │  │  + ingestion  │ │   │
│  │  │          │  │              │  │  roles[])   │  │  logged)      │ │   │
│  │  └──────────┘  └──────────────┘  └─────────────┘  └──────────────┘ │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                               │                                              │
│                               ▼                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                      Enterprise Data Layer                             │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────┐  │   │
│  │  │Prometheus│ │  Loki    │ │  SigNoz/ │ │  Neo4j   │ │  OneUptime│  │   │
│  │  │(metrics) │ │  (logs)  │ │  Tempo   │ │  (graph) │ │ (incident)│  │   │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └───────────┘  │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Agent Descriptions

| Agent | Level | Responsibility | Input | Output |
|---|---|---|---|---|
| **Document Agent** | L0–L6 | Manage ingestion pipeline, document freshness, RBAC stamps | Raw documents | Stamped, embedded chunks |
| **Triage Agent** | L0 | Answer engineer queries with RAG + live data | Engineer query | Cited triage summary |
| **Proactive Monitor** | L2 | Watch ALL services continuously, detect anomalies | All service metrics | Anomaly alerts |
| **Diagnostic Agent** | L3 | Execute read-only diagnostic commands | Anomaly + service graph | Root cause hypothesis |
| **Remediation Agent** | L4 | Execute approved remediation steps | Diagnosis + runbook | Remediation result |
| **Learning Agent** | L5 | Post-incident learning, episodic memory | Incident + feedback | Updated baselines + memory |

---

## 6. Phased Implementation Plan (L0–L6 Maturity)

### Phase 0: Foundation (L0 → L1) — Weeks 1-4

> **Objective:** Stabilize the current ingestion pipeline, add citation metadata, and prepare for multi-format support.

| Week | Deliverable | Dependencies | Agent Impact |
|---|---|---|---|
| W1 | Add `source_url`, `last_updated`, `content_hash` to chunk metadata | None | Triage Agent gets verifiable citations |
| W2 | `DocumentLoader` abstraction; refactor `.md` loader into interface | W1 metadata | Foundation for all future loaders |
| W3 | `PDFLoader` using PyMuPDF + multi-stage chunking (Header → Recursive) | W2 loader | Document Agent handles PDFs |
| W4 | Incremental sync via content hash comparison | W1 metadata | Re-indexing drops from minutes to seconds |

**Key tools:** LangChain (existing), PyMuPDF, LlamaHub (evaluate)

**Enterprise value:** Engineers can trust citations. Adding PDFs doesn't require code changes. Re-indexing is fast.

---

### Phase 1: Vector DB Upgrade (L1) — Weeks 5-6

> **Objective:** Migrate from ChromaDB to Qdrant for RBAC-ready vector storage.

| Week | Deliverable | Dependencies | Agent Impact |
|---|---|---|---|
| W5 | Add Qdrant to Docker stack; dual-write to ChromaDB + Qdrant | Phase 0 | No agent impact (transparent) |
| W6 | Switch reads to Qdrant; add metadata pre-filtering; benchmark vs ChromaDB | W5 | Document Agent filters by metadata |

**Key tools:** Qdrant, `qdrant-client`, LangChain Qdrant wrapper

**Enterprise value:** Foundation for RBAC. Metadata-filtered queries run at full speed.

---

### Phase 2: Proactive Monitoring + Service Topology (L2) — Weeks 7-10

> **Objective:** Build the service knowledge graph and continuous anomaly detection.

| Week | Deliverable | Dependencies | Agent Impact |
|---|---|---|---|
| W7 | Run `graphify .` on each microservice repo → bootstrap Neo4j | None | New: Proactive Monitor Agent |
| W8 | Baseline store (PostgreSQL) + statistical anomaly detector | W7 topology | Monitor detects anomalies |
| W9 | Proactive monitor daemon — polls all services every 30s | W8 detector | Agent watches continuously |
| W10 | Slack/PagerDuty notification integration | W9 daemon | Alerts without manual query |

**Key tools:** Graphify, Neo4j, PostgreSQL, Prometheus

**Enterprise value:** MTTD drops from 15-45 minutes to < 60 seconds.

---

### Phase 3: Diagnostic Agent + Enhanced Chunking (L3) — Weeks 11-14

> **Objective:** Enable read-only diagnostic tool execution and improve RAG quality.

| Week | Deliverable | Dependencies | Agent Impact |
|---|---|---|---|
| W11 | MCP tool protocol — read-only diagnostic commands | Phase 1 | New: Diagnostic Agent |
| W12 | Semantic chunking for disorganized docs | Phase 0 | Better retrieval for complex docs |
| W13 | BGE-Large-en-v1.5 embedding upgrade (configurable) | Phase 1 | Higher accuracy on technical queries |
| W14 | Cascade reasoner — DAG walker for cross-service faults | Phase 2 | Agent traces root cause chains |

**Key tools:** LangGraph MCP, BGE-Large, Neo4j Cypher

**Enterprise value:** Agent runs real diagnostic commands. Root cause identification becomes systematic.

---

### Phase 4: Remediation + Learning Agents (L4 → L5) — Weeks 15-20

> **Objective:** Build the remediation agent with approval gates and the learning agent with episodic memory.

| Week | Deliverable | Dependencies | Agent Impact |
|---|---|---|---|
| W15 | RBAC stamping pipeline — `allowed_roles` on every chunk | Phase 1 | New: Remediation Agent |
| W16 | AD/LDAP role resolution service + JWT token issuance | W15 RBAC | Role-based access control |
| W17 | Remediation agent with 4-tier approval gate (LOW auto → CRITICAL manual) | Phase 3 | Agent executes approved actions |
| W18 | Mnemon episodic memory integration | None | New: Learning Agent |
| W19 | Post-incident learning loop — anomaly threshold adjustment, memory storage | W18 memory | Agent improves from every incident |
| W20 | Enterprise hardening — audit trail, SSO/SAML, compliance docs | All | Production readiness |

**Key tools:** Mnemon, OpenFGA, LDAP/AD, Okta/Auth0

**Enterprise value:** Agent learns from every incident. Remediation actions are safe and approved. Full audit trail for compliance.

---

### Phase 5: Autonomous Agent (L6) — Future

> **Objective:** Full lifecycle automation for known incident types.

| Capability | Description | Prerequisites |
|---|---|---|
| **Auto-detect** | Monitor finds anomaly | Phase 2 |
| **Auto-diagnose** | Diagnostic Agent confirms root cause | Phase 3 |
| **Auto-remediate** (LOW risk) | Agent executes approved steps | Phase 4 |
| **Auto-verify** | Agent checks metrics return to baseline | Phase 4 |
| **Auto-document** | Agent writes postmortem | Phase 4 |
| **Auto-learn** | Agent updates baselines + runbooks | Phase 4 |
| **Page human** (HIGH risk) | Agent pages on-call with pre-populated diagnosis | Phase 3+ |

---

### Phase Summary

```
Phase 0: Foundation    ─── L0 → L1   (Weeks 1-4)    🟢 Metadata + Loaders + Chunking + Sync
Phase 1: Vector DB     ─── L1        (Weeks 5-6)    🟢 Qdrant migration + RBAC foundation
Phase 2: Monitoring    ─── L2        (Weeks 7-10)   🟢 Service graph + anomaly detection
Phase 3: Diagnostics   ─── L3        (Weeks 11-14)  🟢 MCP tools + semantic chunking + cascade
Phase 4: Agents        ─── L4 → L5   (Weeks 15-20)  🟢 RBAC + remediation + episodic memory
Phase 5: Autonomous    ─── L6        (Future)       🔄 Full lifecycle automation
```

---

## 7. Ingestion Pipeline - Phased Input Complexity Plan

This section provides a **deep-dive into the ingestion pipeline itself** — how inputs increase in complexity across 12 dimensions through each phase. Unlike Section 6 (which covers the broader multi-agent system), this plan focuses exclusively on the ingestion subsystem: what data types enter the pipeline, how they're processed, stored, secured, and measured.

### Complexity Progression Overview

```
INPUT COMPLEXITY

Phase 0     Phase 1      Phase 2       Phase 3        Phase 4         Phase 5
  │            │            │             │              │               │
  ▼            ▼            ▼             ▼              ▼               ▼
┌──────┐   ┌──────┐    ┌──────┐      ┌──────┐       ┌──────┐        ┌──────┐
│ .md  │   │ .md  │    │ .md  │      │ .md  │       │ .md  │        │ .md  │
│ only │   │+ PDF │    │+ PDF │      │+ PDF │       │+ PDF │        │+ PDF │
│      │   │+HTML │    │+HTML │      │+HTML │       │+HTML │        │+HTML │
│ 1 svc│   │.DOCX │    │.DOCX │      │.DOCX │       │.DOCX │        │.DOCX │
│      │   │      │    │+Code │      │+Code │       │+Confl│        │+Confl│
│ 5-10 │   │ 1svc │    │ repos│      │repos  │       │+Slack│        │+Slack│
│ docs │   │10-20 │    │(AST) │      │(AST)  │       │+Jira │        │+Jira │
│      │   │ docs │    │      │      │       │       │+Hist │        │+Hist │
│      │   │      │    │ 3 svc│      │ 5+svc │       │10+svc│        │ 20+svc│
│      │   │      │    │30-50 │      │ 50-80 │       │100-200│       │ 500+ │
│      │   │      │    │ docs │      │ docs  │       │ docs  │        │ docs │
└──────┘   └──────┘    └──────┘      └──────┘       └──────┘        └──────┘
                                     +Memo ry
```

---

### Phase 0 — Foundation (L0 → L1, Weeks 1-4)

**Core objective:** Stabilize existing Markdown-only ingestion, add citation metadata, DocumentLoader interface, PDF support.

#### Data Ingestion Overview

| Dimension | Current State | Phase 0 Target | Change |
|---|---|---|---|
| **Data types ingested** | Knowledge Documents (`.md`), Metrics (Prometheus), Logs (Loki) | Same + Knowledge Documents in `.md` and `.pdf` | + PDF documents |
| **Formats** | `.md` only for RAG; PromQL/LogQL for live data | `.md` + `.pdf` for RAG | + PDF (PyMuPDF) |
| **Sources** | Local filesystem (`synthetic-data/runbooks/`) | Local filesystem (+ `synthetic-data/pdfs/`) | One directory |
| **Volume** | ~5–10 documents, ~50–150 chunks | ~10–20 documents, ~100–300 chunks | 2× volume |
| **Supported services** | 1 (`checkout-api`) | 1 | No change |
| **Pipeline architecture** | Single procedural script (`ingestion.py`) | `DocumentLoader` interface + `MarkdownLoader` + `PDFLoader` | Decoupled loaders |
| **Chunking strategy** | `MarkdownHeaderTextSplitter` on `##` | Multi-stage: Header → Recursive sub-split | Nested chunking |
| **Embedding model** | `all-MiniLM-L6-v2` (384-dim, CPU) | Same | No change |
| **Vector store** | ChromaDB (local, persistent) | ChromaDB | No change |
| **Metadata schema** | `source` (filename), `section` (header) | + `source_url` (path), `last_updated` (ISO), `content_hash` (SHA256) | **3 new fields** |
| **RBAC** | None — all chunks accessible | None | No change |
| **Re-indexing** | Full rebuild (deletes and recreates) | **Incremental sync** via content hash diffing | **Major improvement** |
| **Quality metrics** | None tracked | Chunk count, embedding time, recall@3 (manual) | Baseline established |
| **Infrastructure added** | None | None | No change |
| **Tests** | `test_ingestion.py` (basic) | Loader unit tests, PDF extraction tests | New test suite |
| **Failure modes** | Missing vectorstore → graceful degradation | Same + malformed PDF → graceful skip | More resilient |

#### Key Deliverables

| Week | Deliverable | Files Changed | Risk |
|---|---|---|---|
| W1 | Add `source_url`, `last_updated`, `content_hash` to chunk metadata | `src/ingestion.py` | Low — metadata-only change |
| W2 | `DocumentLoader` abstract base class + `MarkdownLoader` refactor | `src/ingestion.py`, new `src/loaders/` | Medium — refactors core pipeline |
| W3 | `PDFLoader` via PyMuPDF + multi-stage chunking | `src/loaders/pdf_loader.py` | Medium — PDF parsing edge cases |
| W4 | Incremental sync via content hash comparison | `src/ingestion.py` | Medium — hash storage + diff logic |

**Success criteria:** 100% of existing tests pass. Manual PDF ingestion produces chunks with `source_url` pointing to the file path. Incremental re-index < 5 seconds for 10 unchanged documents.

---

### Phase 1 — Vector DB & RBAC Foundation (L1, Weeks 5-6)

**Core objective:** Migrate vector store to Qdrant with payload-indexed metadata filtering. Add HTML and DOCX loaders.

#### Data Ingestion Overview

| Dimension | Phase 0 State | Phase 1 Target | Change |
|---|---|---|---|
| **Data types ingested** | Knowledge Docs (`.md`, `.pdf`) | + HTML pages, DOCX outlines | **2 new formats** |
| **Formats** | `.md`, `.pdf` | + `.html`, `.docx` | 4 formats total |
| **Sources** | Local filesystem | Local filesystem (all 4 formats) | No new source types |
| **Volume** | ~10–20 documents | ~10–20 documents | Same volume, more formats |
| **Pipeline architecture** | Interface-based loaders | + `HTMLLoader`, `DOCXLoader` | 2 new loaders |
| **Chunking** | Header → Recursive | Same + table preservation logic | Better table handling |
| **Embedding model** | `all-MiniLM-L6-v2` | Same | No change |
| **Vector store** | ChromaDB | **Dual-write:** ChromaDB + **Qdrant** | **Major infrastructure change** |
| **Metadata schema** | 5 fields (+source_url, +updated, +hash) | + `doc_type`, `word_count`, `file_size` | 8 fields total |
| **RBAC** | None | Metadata pre-filtering on Qdrant (by `doc_type`) | Foundation laid |
| **Re-indexing** | Incremental (hash-based) | Same | Stable |
| **Quality metrics** | Chunk count, recall | + Latency p50/p99 per query, precision@3 | Automated tracking |
| **Infrastructure added** | None | Qdrant container + Dashboard | Docker Compose update |
| **Tests** | Loader tests, PDF tests | + Qdrant dual-write tests, HTML/DOCX extraction | Expanded coverage |
| **Failure modes** | Qdrant down → ChromaDB fallback (read-only) | Degraded but available | **New fallback path** |

#### Key Deliverables

| Week | Deliverable | Files Changed | Risk |
|---|---|---|---|
| W5 | Qdrant Docker container + dual-write pipeline | `docker-compose.yml`, `src/ingestion.py` | Medium — Qdrant integration |
| W5 | HTML loader via `langchain` (BS4) | `src/loaders/html_loader.py` | Low — well-supported |
| W6 | Switch reads to Qdrant + metadata pre-filtering | `src/incident_pilot.py`, `src/ingestion.py` | Medium — migration needs validation |
| W6 | DOCX loader via `python-docx` | `src/loaders/docx_loader.py` | Low — established library |

**Success criteria:** Qdrant query latency < 50ms p50, < 200ms p99. ChromaDB fallback activates automatically when Qdrant is down. All 4 format loaders produce consistent chunk schemas.

---

### Phase 2 — Service Topology & Monitoring Data (L2, Weeks 7-10)

**Core objective:** Ingest service topology data from code repositories via Graphify. Add baseline store for anomaly detection. Expand to 3 services.

#### Data Ingestion Overview

| Dimension | Phase 1 State | Phase 2 Target | Change |
|---|---|---|---|
| **Data types ingested** | Knowledge Docs (4 formats) | + **Service Topology** (Graphify AST → Neo4j) | **New data type: graph** |
| **Formats** | `.md`, `.pdf`, `.html`, `.docx` | Same + **Cypher graph queries** | Non-text data type |
| **Sources** | Local filesystem | + **Code repositories** (AST-parsed) | Cross-repo ingestion |
| **Volume** | ~10–20 documents, 1 service | **3 services**, ~30–50 documents, graph nodes | **3× volume** |
| **Supported services** | 1 (`checkout-api`) | **3** (+ `payment-api`, `fraud-svc`) | Multi-service |
| **Pipeline architecture** | File-based loaders | + **Graphify CLI** → Neo4j Cypher | Parallel pipeline |
| **Chunking** | Header → Recursive | Same + **code-aware chunking** (preserves functions) | Code block handling |
| **Embedding model** | `all-MiniLM-L6-v2` | **BGE-Large-en-v1.5** (1024-dim, GPU) | **Major accuracy upgrade** |
| **Vector store** | Qdrant + ChromaDB | Qdrant (primary) → ChromaDB removed | Simplify to single store |
| **Metadata schema** | 8 fields | + `service`, `team`, `repo_url` | 11 fields |
| **RBAC** | By `doc_type` (metadata filter) | By `service` + `team` (metadata filter) | **Graduated from doc_type to service-level** |
| **Re-indexing** | Incremental (hash) for docs | + **Graph re-build** per deploy for topology | New sync type |
| **Quality metrics** | Latency, precision@3 | + **Recall@5**, **MRR**, **NDCG@10** | Enterprise metrics |
| **Infrastructure added** | Qdrant | **Neo4j** (+ Graphify), **PostgreSQL** (baseline) | 2 new services |
| **Tests** | Loader + Qdrant | + Neo4j graph query tests, multi-service tests | Integration tests |
| **Failure modes** | Graphify fails → manual YAML fallback | Graceful degradation | Fallback path |

#### Key Deliverables

| Week | Deliverable | Files Changed | Risk |
|---|---|---|---|
| W7 | Graphify on 3 microservice repos → Neo4j bootstrap | New `src/topology/` module | Medium — first graph ingestion |
| W7 | Service topology MCP tool (read-only Cypher queries) | New `src/topology/mcp_server.py` | Medium — MCP integration |
| W8 | Baseline store (PostgreSQL + pgvector) + anomaly detector | New `src/anomaly/` | Medium — statistical models |
| W8 | BGE-Large-en-v1.5 embedding upgrade | `src/ingestion.py`, `src/incident_pilot.py` | Medium — GPU requirement |
| W9 | Expand to 3 services: metrics, logs, runbooks, docs | Multiple files | Medium — data generation |
| W9 | Proactive monitor daemon (polls every 30s) | New `src/monitor.py` | High — core L2 feature |
| W10 | Code-aware chunking (preserves function boundaries) | `src/loaders/code_chunker.py` | Medium — AST parsing |

**Success criteria:** 3 services fully ingested with topology graphs. Anomaly detector detects > 90% of synthetic incidents within 60s. BGE-Large achieves > 15% improvement on Recall@3 vs all-MiniLM. Service graph graphify → Neo4j pipeline completes < 60s per repo.

---

### Phase 3 — Diagnostic Data & MCP Tools (L3, Weeks 11-14)

**Core objective:** Ingest diagnostic tool outputs (read-only commands) as queryable data. Add semantic chunking. Expand to 5+ services.

#### Data Ingestion Overview

| Dimension | Phase 2 State | Phase 3 Target | Change |
|---|---|---|---|
| **Data types ingested** | Docs (4 fmts) + Topology (graph) | + **Diagnostic outputs** (command results) | **New data type: ephemeral** |
| **Formats** | `.md`, `.pdf`, `.html`, `.docx`, Cypher | Same + **Tool stdout/stderr** + **Trace data** | Structured + unstructured |
| **Sources** | Filesystem + Code repos | + **Live diagnostic commands** (MCP tools) | Live API ingestion |
| **Volume** | 3 services, 30–50 docs | **5+ services**, 50–80 docs, diagnostic logs | **1.5× volume** |
| **Pipeline architecture** | Batch + periodic (30s) | + **Real-time diagnostic results** (MCP) | Hybrid pipeline |
| **Chunking** | Header → Recursive → Code-aware | + **Semantic chunking** (embedding-threshold) | Adaptive chunking |
| **Embedding model** | BGE-Large-en-v1.5 | Same + optional **BGE Reranker** (stage 2) | 2-stage retrieval |
| **Vector store** | Qdrant | Qdrant + **payload indexes on all filter fields** | Index optimization |
| **Metadata schema** | 11 fields | + `tool_name`, `exit_code`, `diagnostic_type` | 14 fields |
| **RBAC** | By `service` + `team` (metadata filter) | Same + **JWT role verification** per query + **Diagnostic output sensitivity** (inherits target service RBAC) | **Stronger enforcement + context-aware** |
| **Re-indexing** | Incremental (docs) + per-deploy (graph) | + **On-demand indexing** for diagnostic tools | Ad-hoc indexing |
| **Quality metrics** | Recall@5, MRR, NDCG@10 | + **Latency budget** (< 5s end-to-end), **citation accuracy** | SLA enforcement |
| **Infrastructure added** | Neo4j, PostgreSQL | **Temporal** (durable execution) | Resilience |
| **Tests** | Unit + integration | + **Diagnostic tool mocking**, latency budget tests | Performance tests |
| **Failure modes** | Tool timeout → cached result fallback | Graceful with stale warning | Staleness awareness |

#### Key Deliverables

| Week | Deliverable | Files Changed | Risk |
|---|---|---|---|
| W11 | MCP tool protocol — 5 read-only diagnostic commands | New `src/tools/` module | High — security review needed |
| W12 | Semantic chunking for disorganized docs | `src/ingestion.py` | Medium — new dependency |
| W13 | BGE Reranker (2-stage retrieval) | `src/incident_pilot.py` | Medium — latency impact |
| W14 | Cascade reasoner — DAG walker for cross-service faults | New `src/cascade/` | High — algorithmic complexity |
| W14 | Diagnostic output ingestion + recall | `src/query_logs.py` | Medium — new data path |

**Success criteria:** 5 diagnostic tools function with < 2s latency each. Semantic chunking improves precision@3 by > 10% on disorganized docs. Cascade reasoner correctly identifies root cause in 3-service cascade scenarios.

---

### Phase 4 — RBAC, Episodic Memory & External Source Connectors (L4 → L5, Weeks 15-20)

**Core objective:** Full RBAC on all chunks. Ingest episodic memory via Mnemon. Connect to Confluence, Slack, Jira. Expand to 10+ services.

#### Data Ingestion Overview

| Dimension | Phase 3 State | Phase 4 Target | Change |
|---|---|---|---|
| **Data types ingested** | Docs (4) + Topology + Diag | + **Episodic Memory** (Mnemon) + **External docs** (Confluence, Slack, Jira) | **3 new data types** |
| **Formats** | `.md`, `.pdf`, `.html`, `.docx`, Cypher, stdout | + **Mnemon graph** (JSON) + **Confluence API** + **Slack history** + **Jira issues** | Multi-API ingestion |
| **Sources** | Filesystem, Code repos, Live tools | + **Confluence spaces**, **Slack channels**, **Jira projects** | External API sources |
| **Volume** | 5 services, 50–80 docs | **10+ services**, 100–200 docs, memory store | **2× volume** |
| **Supported services** | 5 | **10+** | Double coverage |
| **Pipeline architecture** | Batch + real-time + on-demand | + **Scheduled sync** (Confluence hourly, Slack daily, Jira on-change) | Scheduled connectors |
| **Chunking** | Multi-stage + Semantic | Same + **Late Chunking** (context-aware embeddings) | State-of-the-art |
| **Embedding model** | BGE-Large + Reranker | Same + **Instructor-XL** (domain-specific, optional) | 3rd model option |
| **Vector store** | Qdrant (payload-indexed) | Qdrant + **FalkorDB/ArcadeDB** evaluation for blast radius | Graph DB eval |
| **Metadata schema** | 14 fields | + `allowed_roles[]`, `source_url_external`, `original_author`, `last_synced` | **19 fields** (+ RBAC) |
| **RBAC** | JWT per-query role verification | + **OpenFGA post-filter** (stale ACL catch) + **AD/LDAP sync** | **Full RBAC** |
| **Re-indexing** | Incremental + per-deploy + on-demand | + **Scheduled connector sync** with change detection | Automated sync |
| **Quality metrics** | Latency, recall, citation accuracy | + **Access violation attempts**, **RBAC coverage %**, **memory recall precision** | Security monitoring |
| **Infrastructure added** | Qdrant, Neo4j, PostgreSQL, Temporal | **Mnemon**, **OpenFGA**, **AD/LDAP resolver**, **Confluence/Slack/Jira connectors** | **5+ new services** |
| **Tests** | All previous + performance | + **RBAC penetration tests**, **connector integration tests**, **memory recall tests** | Security testing |
| **Failure modes** | Connector down → stale data (with TTL warning) | Graceful with freshness badge | Staleness awareness |

#### Key Deliverables

| Week | Deliverable | Files Changed | Risk |
|---|---|---|---|
| W15 | RBAC stamping pipeline — `allowed_roles[]` on every chunk | `src/ingestion.py`, new `src/rbac/` | High — security-critical |
| W16 | AD/LDAP role resolution service + JWT token issuance | New `src/rbac/auth_service.py` | High — production auth |
| W16 | OpenFGA post-filter layer | New `src/rbac/post_filter.py` | Medium — fine-grained auth |
| W17 | Confluence connector (via LlamaHub) + scheduled sync | New `src/connectors/confluence.py` | Medium — API rate limits |
| W17 | Slack connector (message history + files) | New `src/connectors/slack.py` | Medium — history volume |
| W18 | Jira connector (incident tickets + postmortems) | New `src/connectors/jira.py` | Low — well-documented API |
| W18 | Mnemon episodic memory integration | New `src/memory/` | Medium — new tool adoption |
| W19 | Post-incident learning loop | New `src/learning/` | High — complex logic |
| W19 | Late chunking (Jina) — experimental | `src/ingestion.py` | Medium — research dependency |
| W20 | Enterprise hardening — audit trail, SSO/SAML, compliance | Multiple files | High — cross-cutting |

**Success criteria:** All 10+ services have RBAC-stamped chunks. Confluence sync completes < 5 min for 20 pages. Mnemon recalls past incidents with > 85% relevance precision. Zero false negatives in RBAC enforcement (penetration test).

---

### Phase 5 — Autonomous Operations (L6, Future)

**Core objective:** Full lifecycle automation for known incidents. Continuous ingestion quality monitoring. Auto-healing pipeline. Global scale.

#### Data Ingestion Overview

| Dimension | Phase 4 State | Phase 5 Target | Change |
|---|---|---|---|
| **Data types ingested** | All previous + Memory + External connectors | + **Real-time monitoring feedback loops**, **Auto-generated runbooks**, **Postmortem drafts** | Auto-generated content |
| **Formats** | All previous | + **LLM-generated Markdown**, **Anomaly reports**, **Cascade summaries** | Agent-generated data |
| **Sources** | Filesystem, Code, APIs, Tools, Memory | + **Agent feedback loop** (anomaly → diagnose → remediate → learn) | Self-referential |
| **Volume** | 10 services, 100–200 docs, memory store | **20+ services**, 500+ docs, 1000+ memory entries | **Scale 5×** |
| **Pipeline architecture** | Batch + real-time + scheduled | + **Streaming ingestion** + **Auto-scaling pipeline workers** | Event-driven |
| **Chunking** | Multi-stage + Semantic + Late | + **Adaptive chunking** (chooses strategy per doc type via classifier) | ML-driven chunking |
| **Embedding model** | BGE-Large + Reranker + Instructor-XL | + **Fine-tuned domain model** (fine-tuned on SRE docs) | Custom model |
| **Vector store** | Qdrant (+ graph DB eval) | **Qdrant cluster** + **sharded indexes** + **multi-region replication** | Enterprise deployment |
| **Metadata schema** | 19 fields + RBAC | + `auto_generated`, `confidence_score`, `review_status`, `feedback_loop_ref` | 23 fields |
| **RBAC** | OpenFGA + AD/LDAP + JWT | + **Dynamic role resolution** (auto-detects team membership), **Attribute-Based Access Control** | ABAC extension |
| **Re-indexing** | Incremental + scheduled | + **Auto-index on document change** (webhook + polling), **Continuous sync** | Event-driven sync |
| **Quality metrics** | All previous + security | + **Auto-detected drift**, **Quality degradation alerts**, **Pipeline health score** | Autonomous monitoring |
| **Infrastructure added** | All previous | **Qdrant cluster**, **Streaming platform** (Kafka), **Model fine-tuning pipeline** | Major infra |
| **Tests** | All previous + penetration | + **Chaos engineering** (kill random pipeline components), **Disaster recovery tests** | Resilience testing |
| **Failure modes** | Full graceful degradation with fallbacks | + **Auto-healing** (restart failed components), **Self-throttling** (backpressure) | Self-healing |

#### Key Capabilities

| Capability | Description | Prerequisites |
|---|---|---|
| **Adaptive chunking** | ML classifier selects optimal chunking strategy per document type (Header for docs, Recursive for code, Semantic for disorganized, Late for long cross-references) | Phase 3 |
| **Fine-tuned embedding** | Domain-specific embedding model fine-tuned on SRE vocabulary (pool exhaustion, cache failover, circuit breaker, blast radius) | Phase 2 data |
| **Streaming ingestion** | Real-time document pipeline via Kafka/Redis streams. Documents indexed within 30s of creation/modification | Phase 4 connectors |
| **Auto-healing pipeline** | Pipeline self-monitors quality metrics. Degradation in Recall@3 triggers automatic re-indexing | Phase 4 quality metrics |
| **Self-generating runbooks** | After 3+ incidents of a type, the Learning Agent auto-generates a draft runbook for human review | Phase 4 memory |
| **Continuous feedback loop** | Every query + response scored for quality. Poor scores trigger re-indexing or re-embedding | All phases |

---

### Ingestion Complexity Progression Summary

```
PHASE 0                              PHASE 1                               PHASE 2
┌──────────────────────┐             ┌──────────────────────┐              ┌──────────────────────┐
│  Formats: 2 (.md,    │    ───→     │  Formats: 4 (+.html, │    ───→     │  Formats: 4          │
│  .pdf)               │             │  .docx)              │              │  + Topology (Cypher)  │
│  Sources: 1 (local)  │             │  Sources: 1 (local)   │              │  Sources: 2 (+ code)   │
│  Vol: ~10-20 docs    │             │  Vol: ~10-20 docs    │              │  Vol: ~30-50 docs     │
│  1 service            │             │  1 service           │              │  3 services           │
│  ChromaDB             │             │  Qdrant + ChromaDB   │              │  Qdrant (only)        │
│  all-MiniLM           │             │  all-MiniLM          │              │  BGE-Large            │
│  No RBAC              │             │  Basic metadata      │              │  Service-level RBAC   │
│  Full rebuild         │             │  Incremental sync    │              │  Incremental + graph  │
│  No quality metrics   │             │  Latency + precision  │              │  Recall@5 + MRR       │
└──────────────────────┘             └──────────────────────┘              └──────────────────────┘
        │                                    │                                     │
        ▼                                    ▼                                     ▼
PHASE 3                               PHASE 4                               PHASE 5
┌──────────────────────┐             ┌──────────────────────┐              ┌──────────────────────┐
│  Formats: 5 (+MCP    │    ───→     │  Formats: 8 (+Mem +  │    ───→     │  Formats: 10+ (+auto- │
│  tool output)        │             │  Confl + Slack+Jira) │              │  generated)            │
│  Sources: 3 (+tools) │             │  Sources: 6 (+3 API)  │              │  Sources: 7 (+agent)   │
│  Vol: ~50-80 docs    │             │  Vol: ~100-200 docs  │              │  Vol: 500+ docs        │
│  5 services           │             │  10+ services        │              │  20+ services          │
│  Qdrant + Reranker   │             │  Qdrant + OpenFGA    │              │  Qdrant cluster       │
│  BGE-Large           │             │  BGE + Instructor-XL │              │  Fine-tuned model      │
│  JWT role RBAC       │             │  Full RBAC + AD/LDAP │              │  Dynamic ABAC          │
│  Semantic chunking   │             │  Late chunking       │              │  Adaptive chunking     │
│  Various              │             │  Scheduled connectors│              │  Event-driven sync     │
│  Latency SLA (5s)    │             │  RBAC violation      │              │  Pipeline health score │
│  Citation accuracy   │             │  penetration tests   │              │  Auto-healing          │
└──────────────────────┘             └──────────────────────┘              └──────────────────────┘
```

### Key Dependencies Between Phases

```
Phase 0 ─── Provides: Metadata schema, Loader interface, Incremental sync
     │
     ▼
Phase 1 ─── Provides: Qdrant, Payload indexing, Multi-format loaders
     │                    (Phase 4 RBAC depends on Qdrant metadata filtering)
     ▼
Phase 2 ─── Provides: Service graph, BGE-Large embedding, 3-service scale
     │                    (Phase 3 cascade depends on service graph)
     ▼
Phase 3 ─── Provides: MCP tools, Semantic chunking, Cascade reasoner
     │                    (Phase 4 remediation depends on diagnostics)
     ▼
Phase 4 ─── Provides: RBAC, Mnemon memory, External connectors
     │                    (Phase 5 autonomy depends on memory)
     ▼
Phase 5 ─── Provides: Adaptive chunking, Fine-tuned model, Auto-healing
```

### Implementation Effort by Phase

| Phase | Duration | Eng Weeks | Key Risk | Go/No-Go Decision Point |
|---|---|---|---|---|
| **Phase 0** | 4 weeks | 4 | PDF quality variance | W2: Loader interface test passes |
| **Phase 1** | 2 weeks | 3 | Qdrant migration data loss | W5: Dual-write validates 100% parity |
| **Phase 2** | 4 weeks | 6 | Graphify accuracy on polyglot repos | W7: Topology graph validated vs manual YAML |
| **Phase 3** | 4 weeks | 6 | MCP security (read-only enforcement) | W11: Security audit of tool commands |
| **Phase 4** | 6 weeks | 10 | External API rate limits + auth | W15: RBAC penetration test passes |
| **Phase 5** | Future | TBD | Foundation for autonomous operations | All previous phases stable |

---


## 8. Compliance Mapping: SOC2, HIPAA, PCI-DSS

This section maps each phase of the ingestion pipeline to specific compliance requirements under **SOC2** (AICPA Trust Services Criteria), **HIPAA** (45 CFR § 164.312 Technical Safeguards), and **PCI-DSS v4.0** (Requirements 3, 7, 8, 10). Understanding this mapping is critical because the ingestion pipeline handles data that may contain PII, PHI, cardholder data, or confidential topology — and each framework imposes specific controls on how that data must be accessed, logged, retained, and secured.

### 8.1 Framework Overview: Key Controls for Ingestion

| Control Area | SOC2 (TSC 2017) | HIPAA (45 CFR § 164.312) | PCI-DSS v4.0 | Ingestion Relevance |
|---|---|---|---|---|
| **Access Control** | CC6.1, CC6.2, CC6.3 — Logical access, least privilege, provisioning/deprovisioning | §164.312(a)(1) — Unique user IDs, emergency access, automatic logoff, encryption | Req. 7 (Restrict access by need-to-know), Req. 8 (Unique IDs, MFA) | RBAC on every chunk; `allowed_roles[]` stamping at ingestion time |
| **Audit Logging** | CC7.2, CC7.3 — Security event logging, evaluation of anomalies | §164.312(b) — Required: audit controls for all ePHI access | Req. 10 — Automated audit trails for all access events (`10.2`, `10.3`, `10.5`) | Every retrieval + ingestion logged with `request_id`, user, timestamp |
| **Data Integrity** | PI1.1-PI1.5 — Complete, valid, accurate, timely processing | §164.312(c)(1) — Mechanism to authenticate ePHI integrity | Req. 3 — Protect stored cardholder data, render PAN unreadable | Content hash (SHA256) on every chunk; incremental sync verifies integrity |
| **Encryption at Rest** | CC6.1 — Protect information assets throughout lifecycle | §164.312(a)(2)(iv) — Addressable: encryption/decryption | Req. 3.4 — Strong cryptography for stored PAN | Qdrant + PostgreSQL encryption; encrypted volume for vector store |
| **Encryption in Transit** | CC6.7 — Data transmission controls | §164.312(e)(1) — Addressable: integrity controls, encryption | Req. 4 — Encrypt transmission of cardholder data over open networks | TLS for all API calls (Prometheus, Loki, Qdrant, Neo4j) |
| **Data Classification** | C1.1 — Restrict confidential info to authorized users | §164.502, §164.506 — Permitted uses/disclosures of PHI | Req. 3.1 — Data retention and disposal policies | `doc_type`, `allowed_roles[]`, `service` metadata on every chunk |
| **Retention & Disposal** | C1.2 — Manage retention and disposal of confidential info | §164.530 — Administrative requirements for PHI handling | Req. 3.1, 3.2 — Minimize storage, prohibit sensitive auth data | Content-hash-based freshness; phased retirement of stale chunks |
| **Change Management** | CC8.1 — Authorize, test, approve changes | — | Req. 6 — Maintain vulnerability management program | Incremental sync with version tracking; CI/CD gate for ingestion pipeline |
| **Incident Response** | CC7.3 — Evaluate logged events for anomalies | §164.308(a)(6) — Security incident procedures | Req. 12 — Information security policy | The ingestion pipeline powers the IR copilot; ingestion failures trigger alerts |

### 8.2 Data Type Compliance Classification

Each of the 7 data types ingested by IncidentPilot has a different compliance profile:

| Data Type | SOC2 Sensitivity | HIPAA (ePHI) | PCI (Cardholder) | Controls Required |
|---|---|---|---|---|
| **Knowledge Documents** | 🔴 HIGH — topology, credentials, PII | 🟡 Possible (runbooks may contain PHI) | 🟡 Possible (remediation steps) | RBAC, audit, encryption, retention |
| **Time-Series Metrics** | 🟢 LOW — aggregate performance data | 🟢 LOW — no patient data | 🟢 LOW — no card data | Basic logging |
| **Application Logs** | 🟡 MEDIUM — may contain PII | 🔴 HIGH — may contain PHI | 🔴 HIGH — may contain PAN | PII/PHI redaction before storage; access control |
| **Incident Simulator** | ⚫ Dev-only — non-production | ⚫ Dev-only | ⚫ Dev-only | No compliance controls required |
| **Episodic Memory** | 🔴 HIGH — past incident details | 🔴 HIGH — may contain PHI | 🟡 Possible | Full RBAC + audit + encryption + retention policy |
| **Service Topology** | 🟡 MEDIUM — internal architecture | 🟢 LOW — no PHI | 🟢 LOW — no card data | Access control (need-to-know) |
| **System Prompts** | 🟢 LOW — agent behavior config | 🟢 LOW | 🟢 LOW | Version control, change management |

### 8.3 Compliance Milestones by Phase

| Phase | SOC2 Milestone | HIPAA Milestone | PCI-DSS Milestone | Audit Readiness |
|---|---|---|---|---|
| **Phase 0** (W1-4) | **CC6.1** foundation — `content_hash` for data integrity tracking | **§164.312(c)** — SHA256 integrity on all chunks | **Req. 3.1** — Data retention policy via freshness tracking | Baseline: all chunks have traceable source and integrity hash |
| **Phase 1** (W5-6) | **CC6.2, CC6.3** — User access control via metadata pre-filtering | **§164.312(a)(1)** — Unique identification via JWT claims | **Req. 7.2, 8.2** — Role-based access, unique IDs | Every query filtered by JWT role claims |
| **Phase 2** (W7-10) | **CC7.1** — Vulnerability scanning for ingestion infra | **§164.312(c)** — Data integrity on baseline metrics | **Req. 8.6** — Service account management for automated pipelines | PostgreSQL baseline store with structured logging (foundation for Phase 4 audit) |
| **Phase 3** (W11-14) | **CC7.2, CC7.3** — Security event logging for MCP tool execution | **§164.312(d)** — Person/entity authentication for diagnostic tools | **Req. 8.6** (service accounts) + **Req. 10.3** — Record event details (user ID, type, timestamp) | MCP tool execution logged with full context; service account JWT per tool |
| **Phase 4** (W15-20) | **CC6.7, C1.1, C1.2** — Encryption, classification, retention | **§164.312(a)(2)(iv), §164.312(e)** — Encryption + transmission security; **§164.308(a)(1)(ii)(D)** — Information system activity review | **Req. 3.4, 4, 10.5** — Full audit review cycle | **Full compliance-ready state**: RBAC + audit + encryption + retention + audit review workflow |
| **Phase 5** (Future) | **CC8.1** — Automated change management for pipeline | **§164.308(a)(6)** — Automated IR procedures | **Req. 12** — Continuous compliance monitoring | Autonomous compliance verification |

### 8.4 Current Compliance Gap Analysis

| Requirement | Current State | Gap | Risk Level | Target Phase |
|---|---|---|---|---|
| **Access control by role** | None — no JWT, no RBAC, no `allowed_roles[]` | All chunks accessible to all users | 🔴 Critical | Phase 1 (pre-filtering) + Phase 4 (full RBAC) |
| **Audit trail for retrievals** | None — no query logging | Cannot prove who saw what | 🔴 Critical | Phase 4 (pgAudit) |
| **Data integrity verification** | None — full rebuild, no hash | Cannot detect tampering | 🟡 Medium | Phase 0 (content_hash) |
| **Encryption at rest** | None — ChromaDB stores on local filesystem | Data at risk on disk | 🟡 Medium | Phase 1 (Qdrant encryption) |
| **Encryption in transit** | None — all internal Docker network (localhost) | Low risk in Docker, critical in production | 🟢 Low current | Phase 1+ (TLS for production) |
| **Data classification** | None — only `source` + `section` metadata | No distinction between sensitive and open data | 🟡 Medium | Phase 0 (doc_type) + Phase 2 (service, team) |
| **Retention and disposal** | None — full rebuild on every run | No retention policy; data deleted without tracking | 🟡 Medium | Phase 0 (incremental sync) |
| **Change management** | None — manual ingestion.py edits | No version control for pipeline config | 🟡 Medium | Phase 0 (loader abstraction) |

### 8.5 Audit Trail Architecture

The audit trail is designed to satisfy SOC2 CC7.2/CC7.3, HIPAA §164.312(b), and PCI-DSS Req. 10 simultaneously:

```
Every Retrieval:                          Every Ingestion:
┌─────────────────────────────┐           ┌─────────────────────────────┐
│ request_id (UUID)           │           │ batch_id (UUID)             │
│ timestamp (ISO 8601)        │           │ timestamp (ISO 8601)        │
│ user_id (from JWT)          │           │ documents_loaded (count)    │
│ user_role (from JWT)        │           │ chunks_created (count)      │
│ query_text (truncated)      │           │ embedding_model_used        │
│ num_chunks_returned         │           │ vector_store_target         │
│ chunk_ids (top-3)           │           │ hashes (SHA256 of inputs)   │
│ retrieval_latency_ms        │           │ ingestion_latency_ms        │
│ source_badges (live/static) │           │ errors (if any)             │
└─────────────────────────────┘           └─────────────────────────────┘
        │                                            │
        └────────────────────┬───────────────────────┘
                             ▼
              ┌──────────────────────────────────┐
              │       Audit Store (PostgreSQL)    │
              │  • Append-only (immutable)         │
              │  • pgAudit for DDL monitoring     │
              │  • 90-day hot retention           │
              │  • 7-year cold retention (S3)     │
              │  • Encrypted at rest (TDE)        │
              │  • Accessible only via admin API  │
              └──────────────────────────────────┘
```

### 8.6 Compliance Responsibility Matrix

| Control | Engineering (Ingestion) | Platform/SRE | Security/Compliance Team |
|---|---|---|---|
| RBAC metadata stamping | ✅ Implement `allowed_roles[]` pipeline | ✅ Provision Qdrant with JWT support | ✅ Define role hierarchy and ACL schema |
| Audit logging | ✅ Implement structured audit records | ✅ Provision PostgreSQL + pgAudit | ✅ Define retention policy (90d hot, 7yr cold) |
| Encryption at rest | ✅ Enable Qdrant/PostgreSQL encryption | ✅ Manage KMS keys | ✅ Audit key rotation schedule |
| Encryption in transit | ✅ Configure TLS for all APIs | ✅ Manage certificate lifecycle | ✅ Verify cipher suites annually |
| Data classification | ✅ Implement `doc_type` + metadata schema | — | ✅ Review classification taxonomy quarterly |
| Retention & disposal | ✅ Implement freshness tracking + GC | ✅ Configure automated archival | ✅ Define disposal policy per data type |
| Penetration testing | — | ✅ Schedule annual pen test | ✅ Scope, execute, remediate findings |
| Compliance documentation | ✅ Document controls in runbooks | ✅ Maintain architecture diagrams | ✅ Own SOC2/HIPAA/PCI audit evidence |

### 8.7 Key Compliance Decision Points

1. **Which data types require what level of control?** — Metrics (LOW) need only basic logging. Application Logs (MEDIUM-HIGH) require PII redaction before storage. Episodic Memory (HIGH) requires full RBAC + encryption + audit + retention policy.

2. **Is encryption at rest needed from Phase 0?** — No. Phase 0-1 operates entirely in a Docker development environment. Production encryption should be implemented in Phase 1 (Qdrant) and extended in Phase 4 (PostgreSQL).

3. **Who owns compliance documentation?** — Engineering implements the controls (RBAC, audit, encryption). SRE provisions the infrastructure. Security/Compliance team defines the policies and provides audit evidence.

4. **What retention duration for audit logs?** — 90 days hot storage in PostgreSQL for active review. 7 years cold storage in S3 for compliance archive (per HIPAA and PCI-DSS retention requirements).

5. **Can we use the same audit store for all three frameworks?** — Yes. The structured audit schema (Section 8.5) satisfies all three frameworks. The key difference is the retention policy: SOC2 requires 6 months, HIPAA requires 6 years, PCI-DSS requires 3 years. We meet all three with 90d hot + 7yr cold.

---

## 9. Decision Matrix - Options Assessment

| Decision Point | Option | Rationale | Maturity Level |
|---|---|---|---|
| **RAG Framework** | **LangChain** (orchestrator) + **LlamaHub** (loaders) | No rewrite; best multi-agent support via LangGraph | L0+ |
| **Chunking** | Multi-stage: MarkdownHeader → Recursive → (future: Semantic) | Preserves structure + handles oversized sections | L0→L3 |
| **Embedding** | **BGE-Large-en-v1.5** (upgrade from all-MiniLM) | Best open-source accuracy for technical docs | L1+ |
| **Vector DB** | **Qdrant** (primary) + **ChromaDB** (dev/fallback) | Best RBAC support + self-hosted + fastest filtered search | L1+ |
| **RBAC Model** | JWT-scoped tokens + Qdrant pre-filter + OpenFGA post-filter | No leaks; catches stale ACLs | L4+ |
| **Episodic Memory** | **Mnemon** (Apache 2.0, single binary) | Zero infrastructure; dedup + decay + audit built-in | L5+ |
| **Service Topology** | **Graphify** (Python, tree-sitter AST) | Auto-generates from code; push to Neo4j | L2+ |
| **Approval Gate** | LOW auto / MEDIUM Slack / HIGH PagerDuty / CRITICAL manual | Graduated risk model; defense-in-depth | L4+ |
| **Audit Trail** | PostgreSQL + structured logging | Every retrieval and ingestion is traceable | L4+ |

### Key Insight

The ingestion pipeline is not a standalone data processing concern. It is the **foundation layer** for a multi-agent enterprise system. Every architectural decision — from chunking strategy to vector DB choice to metadata schema — must account for the downstream agents that depend on it. **RBAC and metadata traceability are the two most critical investments** because they cannot be retrofitted easily once agents are deployed at scale.

---

## 10. References & Sources

### 10.1 Embedding Benchmarks (MTEB)

| Source | URL | Relevance |
|---|---|---|
| **MTEB Leaderboard (Official)** | [huggingface.co/spaces/mteb/leaderboard](https://huggingface.co/spaces/mteb/leaderboard) | Primary leaderboard for embedding model comparison across retrieval, clustering, classification tasks. Supports filtering by task type (e.g., Retrieval for Recall@k). |
| **MTEB Paper** | Muennighoff, N., Tazi, N., Magne, L., & Reimers, N. (2022). *MTEB: Massive Text Embedding Benchmark*. arXiv:2210.07316. [arxiv.org/abs/2210.07316](https://arxiv.org/abs/2210.07316) | Original research paper defining the benchmark methodology and evaluation taxonomy across 8 task types and 58 datasets. |
| **all-MiniLM-L6-v2** | [huggingface.co/sentence-transformers/all-MiniLM-L6-v2](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2) | 384-dim model. ~56.3 MTEB overall score. Fast, CPU-friendly, but lower accuracy vs modern models. Our current embedding model. |
| **BGE-Large-en-v1.5** | [huggingface.co/BAAI/bge-large-en-v1.5](https://huggingface.co/BAAI/bge-large-en-v1.5) | 1024-dim model. ~64.6 MTEB overall score. Top open-source performer for technical/enterprise retrieval. Recommended L1 upgrade path. |
| **Instructor-XL** | [huggingface.co/hkunlp/instructor-xl](https://huggingface.co/hkunlp/instructor-xl) | 768-dim model. ~55.8 MTEB overall score. Instruction-tuned for domain-specific retrieval. Requires 24GB+ VRAM. Suitable for L3+ specialized use cases. |
| **text-embedding-3-small** | [platform.openai.com/docs/guides/embeddings](https://platform.openai.com/docs/guides/embeddings) | 1536-dim (variable) model. ~62.3–64.0 MTEB overall score. Proprietary API. Suitable as L2 alternative if API dependency is acceptable. |
| **Sentence-Transformers Documentation** | [sbert.net](https://www.sbert.net/) | Official documentation for the sentence-transformers library used for all-MiniLM-L6-v2 and BGE models. |

> **Note:** MTEB scores are dynamic; new models are submitted frequently. As of mid-2026, all four analyzed models rank outside the top 50 overall due to newer entrants (Qwen-embedding, Jina v3). Their retrieval-specific performance remains well-documented; filter the leaderboard by the **Retrieval** task tab for Recall@k comparisons.

---

### 10.2 Vector Database Documentation

#### Qdrant

| Source | URL | Key Details |
|---|---|---|
| **Security & Access Control** | [qdrant.tech/documentation/security/](https://qdrant.tech/documentation/security/) | JWT-based RBAC with API key support (Admin, Read-Only, Granular Access keys). JWT claims support `exp`, `access`, and `value_exists` conditional validation. Must be explicitly enabled via `jwt_rbac: true` in self-hosted configuration. |
| **Secure Self-Hosted Tutorial** | [qdrant.tech/documentation/tutorials-operations/secure-qdrant/](https://qdrant.tech/documentation/tutorials-operations/secure-qdrant/) | Step-by-step guide to configuring RBAC for self-hosted Qdrant instances. Covers API key generation, JWT token issuance, and collection-scoped permissions. |
| **Payload Filtering** | [qdrant.tech/documentation/search/filtering/](https://qdrant.tech/documentation/search/filtering/) | Supports `must` (AND), `should` (OR), `must_not` (NOT) clauses. Condition types include `match`, `any`, `except`, and nested JSON via dot notation. Performance depends on creating payload indexes on frequently filtered fields. |
| **GitHub Repository** | [github.com/qdrant/qdrant](https://github.com/qdrant/qdrant) | Apache 2.0 license. Rust-based vector database. Docker image available. LangChain wrapper (`langchain-qdrant`) available. |

#### Weaviate

| Source | URL | Key Details |
|---|---|---|
| **Manage Roles (RBAC)** | [docs.weaviate.io/weaviate/configuration/rbac/manage-roles](https://docs.weaviate.io/weaviate/configuration/rbac/manage-roles) | Native RBAC with granular role-based permissions. Resource types: Role Management, User Management, Collections, Tenants, Data Objects, Backups, Cluster Data Access. Supports prefix matching (e.g., `testRole*`). |
| **RBAC Overview** | [docs.weaviate.io/weaviate/configuration/rbac](https://docs.weaviate.io/weaviate/configuration/rbac) | Overview of Weaviate's RBAC architecture. Roles assigned to users via API keys. Integration with OIDC providers for SSO. |
| **GitHub Repository** | [github.com/weaviate/weaviate](https://github.com/weaviate/weaviate) | BSD 3-Clause license. Go-based vector database. Docker image available. |

#### pgvector (PostgreSQL)

| Source | URL | Key Details |
|---|---|---|
| **pgvector GitHub** | [github.com/pgvector/pgvector](https://github.com/pgvector/pgvector) | Open-source vector similarity search for PostgreSQL. Supports exact and approximate nearest neighbor search (HNSW, IVFFlat indexes). Fully compatible with PostgreSQL features. |
| **PostgreSQL Row-Level Security** | [postgresql.org/docs/current/ddl-rowsecurity.html](https://www.postgresql.org/docs/current/ddl-rowsecurity.html) | PostgreSQL 9.5+ native RLS feature. Allows per-row access policies based on user roles or session context (`current_setting`). Must be explicitly enabled (`ALTER TABLE ... ENABLE ROW LEVEL SECURITY`). Table owners bypass RLS unless `FORCE ROW LEVEL SECURITY` is set. |
| **Iterative Index Scans** | [github.com/pgvector/pgvector#iterative-index-scans](https://github.com/pgvector/pgvector#iterative-index-scans) | pgvector 0.8.0+ feature enabling the index scan to continue searching until enough results satisfy RLS filters. Critical for combining HNSW indexing with row-level security. |

#### Pinecone

| Source | URL | Key Details |
|---|---|---|
| **Security Overview** | [docs.pinecone.io/guides/production/security-overview](https://docs.pinecone.io/guides/production/security-overview) | Project-level IAM with Control Plane (infrastructure) and Data Plane (data) permission separation. SSO integration on Enterprise tier. Audit logging on Enterprise tier. |
| **Manage Namespaces** | [docs.pinecone.io/guides/manage-data/manage-namespaces](https://docs.pinecone.io/guides/manage-data/manage-namespaces) | Namespaces for logical data partitioning within an index. Auto-created on first upsert. All operations scoped to a single namespace. Common pattern for multi-tenant isolation. |
| **Filter by Metadata** | [docs.pinecone.io/guides/search/filter-by-metadata](https://docs.pinecone.io/guides/search/filter-by-metadata) | Rich filter operators: `$eq`, `$ne`, `$gt`, `$gte`, `$lt`, `$lte`, `$in`, `$nin`, `$exists`, `$and`, `$or`. Filter evaluation happens during the search phase for efficiency. |

---

### 10.3 Episodic Memory & Knowledge Graph

#### Mnemon

| Source | URL | Key Details |
|---|---|---|
| **GitHub Repository** | [github.com/mnemon-dev/mnemon](https://github.com/mnemon-dev/mnemon) | **Apache 2.0 license.** Single Go binary with SQLite backend. LLM-supervised episodic memory with four-graph architecture: temporal (when), entity (who/what), causal (cause→effect), semantic (similarity). |
| **Installation** | Homebrew: `brew install mnemon-dev/tap/mnemon` | No external dependencies, no API keys required. Multi-framework support via markdown-installable harness (GUIDELINE.md, SKILL.md). |
| **Key Features** | Built-in | Importance decay with access-count boosting and automated GC. Built-in deduplication (detects duplicates on `remember` and auto-consolidates). Privacy-safe receipts (hashed operation receipts for audit). Named stores for per-service isolation. |

#### Graphify

| Source | URL | Key Details |
|---|---|---|
| **GitHub Repository** | [github.com/Graphify-Labs/graphify](https://github.com/Graphify-Labs/graphify) | **MIT license.** Python package (`graphifyy` on PyPI). Code-to-knowledge-graph using tree-sitter AST parsing across ~40 languages. CLI command: `graphify`. |
| **Official Website** | [graphify.net](https://graphify.net/) | Documentation, quickstart guides, and community resources. |
| **MCP Support** | `uv tool install "graphifyy[mcp]"` | Exposes knowledge graph as an MCP stdio server for agent integration. |
| **Neo4j Integration** | `uv tool install "graphifyy[neo4j]"` | Push generated graphs to Neo4j for persistent storage and Cypher queries. |
| **Key Features** | Built-in | Tree-sitter AST for deterministic code parsing (no LLM). Cross-file link resolution (calls, imports, inherits). Community detection (Leiden algorithm). Query/path/explain operations. |

---

### 10.4 RAG Frameworks & Orchestration

| Source | URL | Key Details |
|---|---|---|
| **LangChain Documentation** | [python.langchain.com](https://python.langchain.com) | Current RAG framework. LangChain v0.3+ for Chains, Groq integration, ChromaDB. LangGraph for multi-agent orchestration with human-in-the-loop. |
| **LlamaHub** | [llamahub.ai](https://llamahub.ai) | 150+ community-contributed document loaders, including Confluence, Slack, Jira, Google Drive, PDF. Plug-and-play integration with LlamaIndex. Can also be used independently. |
| **LangGraph Documentation** | [langchain-ai.github.io/langgraph](https://langchain-ai.github.io/langgraph/) | Stateful, cyclic agent workflows. MCP-native support. Production-grade checkpointing for human-in-the-loop. |
| **LlamaIndex Documentation** | [docs.llamaindex.ai](https://docs.llamaindex.ai) | `SemanticSplitterNodeParser`, auto-metadata extraction, `DocStore` for incremental indexing. Strong alternative for ingestion-stage processing. |

---

### 10.5 Chunking Strategies

| Source | URL | Key Details |
|---|---|---|
| **LangChain Text Splitters** | [python.langchain.com/docs/how_to/#text-splitters](https://python.langchain.com/docs/how_to/#text-splitters) | Documentation for `MarkdownHeaderTextSplitter`, `RecursiveCharacterTextSplitter`, and other splitter implementations. |
| **Late Chunking (Jina AI)** | [jina.ai/news/late-chunking-in-long-context-embedding-models](https://jina.ai/news/late-chunking-in-long-context-embedding-models/) | Context-aware chunking approach that embeds full document context while preserving token-level positioning. Requires long-context embedding models. |
| **Semantic Chunking (LlamaIndex)** | [docs.llamaindex.ai/en/stable/module_guides/loading/node_parsers/](https://docs.llamaindex.ai/en/stable/module_guides/loading/node_parsers/) | LlamaIndex's `SemanticSplitterNodeParser` that splits based on embedding similarity thresholds rather than fixed character counts. |

---

### 10.6 Enterprise Agent Architecture

| Source | URL | Key Details |
|---|---|---|
| **MCP Protocol (Anthropic)** | [modelcontextprotocol.io](https://modelcontextprotocol.io) | Model Context Protocol standard for tool integration with LLM agents. Enables standardized diagnostic tool execution (Phase 3). |
| **OpenFGA (Auth0)** | [openfga.dev](https://openfga.dev) | Fine-grained authorization system for post-filter RBAC validation. Relationship-based access control (ReBAC). Recommended as the post-filter layer in the Hybrid RBAC pattern. |
| **PostgreSQL Documentation** | [postgresql.org/docs/current](https://www.postgresql.org/docs/current/) | General PostgreSQL reference for baseline store, audit store, and pgvector integration. |

---

### 10.7 Additional Tool Evaluation References

| Tool | Source | License | Key Evaluation Criteria |
|---|---|---|---|
| **Microsoft GraphRAG** | [github.com/microsoft/graphrag](https://github.com/microsoft/graphrag) | MIT | Deferred (Phase 3+). LLM-dependent entity extraction, batch indexing, expensive for small corpora. Recommended only if document corpus exceeds 100+ documents. |
| **QMD (Tobi Lütke)** | [github.com/tobi/qmd](https://github.com/tobi/qmd) | MIT | Optional ChromaDB replacement. BM25 + vector + LLM reranking hybrid search. MCP-native. Requires Node.js ≥ 22. Limited to markdown files. |
| **BGE Reranker** | [huggingface.co/BAAI/bge-reranker-v2-m3](https://huggingface.co/BAAI/bge-reranker-v2-m3) | MIT | Optional second-stage reranker for improving retrieval precision. Used in combination with QMD or ChromaDB. ~1.1GB model size. |
| **PyMuPDF (fitz)** | [pymupdf.readthedocs.io](https://pymupdf.readthedocs.io/) | AGPLv3 / Commercial | PDF text extraction for PDFLoader. Recommended for Phase 0 (Foundation). Fast, accurate, supports tables and code blocks. |
| **Neo4j** | [neo4j.com/docs](https://neo4j.com/docs/) | GPLv3 (Community) | Graph database for service topology storage. Cypher query language. Largest graph database community. Recommended for Phase 2. |

---

*Document version: 2026-07-17  
Next review: Phase 0 completion (Week 4)*