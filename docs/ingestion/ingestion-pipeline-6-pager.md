# IncidentPilot Ingestion Pipeline — 6-Pager

**Document type:** Architecture Strategy / Deep-Dive  
**Status:** v1.0 Draft  
**Author:** Staff Developer  
**Date:** July 2026  
**Works cited:** `docs/ingestion/ingestion-analysis.md`, `docs/ingestion/INGESTION_EXECUTIVE_SUMMARY.md`, `docs/incident-pilot-6-pager.md`

---

## 1. Who We Are Writing This For

This document is a companion deep-dive to the main IncidentPilot Enterprise 6-pager. While that document covers the full product strategy (detection → diagnosis → remediation → learning), this one focuses exclusively on the **ingestion pipeline** — the foundation upon which all agent capabilities depend.

| Audience | What They Care About |
|---|---|
| **VP of Engineering** | Investment required, build vs buy for ingestion infra, timeline to enterprise readiness |
| **SRE Directors** | Data freshness, RBAC compliance, audit trails for SOC2 |
| **Staff+ Platform Engineers** | Architecture decisions (loaders, chunking, vector DB, embedding), tool trade-offs, migration path |

---

## 2. Context & Problem Statement

### 2.1 The Ingestion Pipeline Today

IncidentPilot's current ingestion pipeline (`src/ingestion.py`) is a **single-file, single-format, single-service** pipeline built for an L0 MVP:

```
Markdown files (synthetic-data/runbooks/*.md)
  └─▶ Strip YAML frontmatter (regex)
       └─▶ MarkdownHeaderTextSplitter (## headers)
            └─▶ all-MiniLM-L6-v2 embeddings (384-dim, CPU)
                 └─▶ ChromaDB vector store (synthetic-data/vectorstore/)
                      └─▶ On every run: DELETE and recreate entire store
```

For live data (`src/query_logs.py`), a separate pipeline queries Prometheus and Loki:

```
Prometheus (localhost:9090) ─┐
                             ├─▶ query_logs() ─▶ structured summary ─▶ LLM prompt
Loki (localhost:3100) ───────┘
                             Fallback: static JSON/JSONL files when offline
```

**What works well:**
- Structured log analysis (`analyze_logs()`) never sends raw logs to the LLM — good security pattern
- Data source badges (live vs fallback) give engineers transparency
- ChromaDB is simple and sufficient for current corpus (~50-150 chunks)

**What doesn't scale:**

| # | Gap | Evidence in Code | Business Impact |
|---|---|---|---|
| 1 | **Single format** | `ingestion.py:52` — only `*.md` files | Confluence, PDF, Slack, Jira docs cannot enter pipeline |
| 2 | **Single service** | `query_logs.py:39` — `DEFAULT_SERVICE = "checkout-api"` | Cross-service outages invisible to the agent |
| 3 | **Full rebuild every run** | `ingestion.py:89` — `shutil.rmtree` + `mkdir` | No incremental sync; doesn't scale beyond 10 docs |
| 4 | **No RBAC** | `ingestion.py:83` — only `source` + `section` metadata | Any user sees all documents — no role-based access |
| 5 | **No audit trail** | No retrieval logging anywhere | Non-compliant for SOC2/HIPAA |
| 6 | **No episodic memory** | `incident_pilot.py:query()` has no memory mechanism | Every incident handled as if first time |
| 7 | **No service topology** | `incident_pilot.py` has hardcoded single-service thresholds | Cannot detect cascading failures |

### 2.2 The Cost of These Gaps

| Impact | Current State | Target | Annual Cost |
|---|---|---|---|
| **Stale runbooks** | Manual updates, no freshness detection | Auto-verified freshness via content hash | ~$50K in wasted triage time |
| **Missing context** | Cannot query Confluence, Jira, Slack | Multi-source RAG | ~$30K in context-switching |
| **Repeat incidents** | 30-50% of incidents are repeats | < 5% via episodic memory | ~$68K/year (from main 6-pager) |
| **Compliance risk** | No audit trail for retrieval | Full pgAudit logging | Potential audit failure |
| **On-call burnout** | Engineers do manual multi-tool correlation | Unified ingestion = unified query | ~$50K in turnover cost |

### 2.3 Root Causes

1. **Tight coupling** — Ingestion is a single monolithic script (`ingestion.py`). Adding a new format, chunking strategy, or vector store means modifying the same 100-line file.

2. **No abstraction layer** — There is no `DocumentLoader` interface, no `Chunker` abstraction, no `VectorStore` wrapper. Everything is hardcoded to specific implementations.

3. **No metadata enrichment** — Chunks carry only `source` (filename) and `section` (header). No content hash, no last-updated timestamp, no RBAC roles, no service tag.

4. **No content-level RBAC** — ChromaDB has no native RBAC support. Every query returns everything.

5. **Freshness is a binary state** — Data is either loaded or gone. No concept of "stale," "needs re-indexing," or "version N+1 available."

### 2.4 The Opportunity

An enterprise-grade ingestion pipeline unlocks **every downstream agent capability**:

| Ingestion Capability | Unlocks Agent Level | ROI Driver |
|---|---|---|
| Multi-format, multi-source loaders | L1+ Instrumented | No more context-switching |
| Metadata RBAC stamping | L4 Remediation Agent | SOC2 compliance, safe remediation |
| Episodic memory store (Mnemon) | L5 Learning Agent | 90% reduction in repeat incidents |
| Service topology (Graphify + Neo4j) | L2 Proactive Monitor | MTTD from 15min → < 60s |
| Incremental sync + freshness tracking | L0-L3 All | Predictable re-index costs |
| Audit trail (pgAudit) | L4+ Enterprise | Compliance readiness |

---

## 3. Proposed Solution: Ingestion Pipeline Evolution

### 3.1 Vision Statement

> **The ingestion pipeline evolves from a single-file Markdown-only loader to a pluggable, multi-format, RBAC-secured, incrementally-synced data foundation that serves all agents from L0 through L6 — without any single agent needing to understand how data was loaded, chunked, or secured.**

### 3.2 Core Principles

1. **Deterministic processing** — All ingestion logic (chunking, hashing, metadata extraction) is in testable Python code, not LLM prompts. No LLM is involved at ingestion time.

2. **Metadata-first security** — RBAC is enforced at the chunk level. Every chunk carries `allowed_roles[]` before it enters the vector store. Post-filtering is a defense-in-depth layer, not the primary mechanism.

3. **Fail-open with degradation** — If a vector store is down, fall back to keyword search over raw documents. If a data source is unreachable, serve cached data. The agent never goes silent.

4. **Observable pipeline** — Every ingestion run produces structured metrics: `ingestion_docs_loaded`, `ingestion_chunks_created`, `ingestion_latency_ms`, `ingestion_freshness_days`. These are exposed as Prometheus metrics and surfaced in Grafana.

### 3.3 Target Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      Ingestion Pipeline — Target Architecture                  │
│                                                                               │
│   Data Sources                                                                 │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐ │
│   │ .md/.pdf │  │ Confl.   │  │ Slack    │  │ Jira     │  │ Code Repos    │ │
│   │ .html/.  │  │ Spaces   │  │ Channels │  │ Projects │  │ (Graphify)    │ │
│   │ .docx    │  │          │  │          │  │          │  │               │ │
│   └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  └───────┬───────┘ │
│        │              │              │              │               │         │
│        ▼              ▼              ▼              ▼               ▼         │
│   ┌──────────────────────────────────────────────────────────────────────┐  │
│   │                         Document Loader Layer                          │  │
│   │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐ │  │
│   │  │  File    │  │ Confl.   │  │ Slack    │  │ Jira     │  │ Graph  │ │  │
│   │  │Loader    │  │ Loader   │  │ Loader   │  │ Loader   │  │ Loader │ │  │
│   │  └──────────┘  └──────────┘  └──────────┘  └──────────┘  └────────┘ │  │
│   └──────────────────────────────┬───────────────────────────────────────┘  │
│                                  │                                           │
│   ┌──────────────────────────────▼───────────────────────────────────────┐  │
│   │                         Chunking Pipeline                              │  │
│   │  ┌──────────────────────┐ ┌────────────────────┐ ┌────────────────┐  │  │
│   │  │ Stage 1: Header Split│─▶│ Stage 2: Recursive │─▶│ Stage 3:      │  │  │
│   │  │ (MarkdownHeader)     │  │ Sub-split (code    │  │ Semantic      │  │  │
│   │  │                      │  │ blocks, tables)    │  │ (Phase 3+)    │  │  │
│   │  └──────────────────────┘ └────────────────────┘ └────────────────┘  │  │
│   └──────────────────────────────┬───────────────────────────────────────┘  │
│                                  │                                           │
│   ┌──────────────────────────────▼───────────────────────────────────────┐  │
│   │                      Metadata & RBAC Stamping                          │  │
│   │   source_url  │  content_hash  │  last_updated  │  allowed_roles[]  │  │
│   │   service     │  doc_type      │  team          │  source_url_external│  │
│   └──────────────────────────────┬───────────────────────────────────────┘  │
│                                  │                                           │
│   ┌──────────────────────────────▼───────────────────────────────────────┐  │
│   │                 Embedding + Vector Store Layer                          │  │
│   │  ┌──────────────────────────┐  ┌──────────────────────────────────┐   │  │
│   │  │  Embedding (configurable) │  │  Vector Store (Qdrant +         │   │  │
│   │  │  • L0: all-MiniLM     │  │  ChromaDB fallback)               │   │  │
│   │  │  • L2: BGE-Large      │  │  • Payload-indexed metadata       │   │  │
│   │  │  • L4: Instructor-XL   │  │  • JWT RBAC pre-filtering        │   │  │
│   │  └──────────────────────────┘  │  • Incremental sync             │   │  │
│   │                               └──────────────────────────────────┘   │  │
│   └──────────────────────────────────────────────────────────────────────┘  │
│                                                                               │
│   ┌──────────────────────────────────────────────────────────────────────┐  │
│   │                      Agent Integration Layer                            │  │
│   │                                                                         │  │
│   │  Triage Agent (L0) ──▶ retrieve(query, user_role) → RBAC-filtered chunks │  │
│   │  Proactive Monitor (L2) ──▶ topology → cascade analysis              │  │
│   │  Diagnostic Agent (L3) ──▶ MCP tools → diagnostic data ingestion     │  │
│   │  Remediation Agent (L4) ──▶ RBAC-verified runbook steps              │  │
│   │  Learning Agent (L5) ──▶ remember(resolution) → episodic memory      │  │
│   └──────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.4 Target Metrics

| Metric | Current | Target (20 weeks) | Measurement |
|---|---|---|---|
| **Supported formats** | 1 (`.md`) | 8 (`.md`, `.pdf`, `.html`, `.docx`, Confluence, Slack, Jira, Graph) | Count |
| **Supported services** | 1 | 10+ | Count |
| **Re-index latency (10 docs)** | ~10s full rebuild | < 2s incremental sync | Timer |
| **RBAC coverage** | None | 100% chunks stamped with `allowed_roles[]` | Coverage % |
| **Retrieval precision@3** | ~80% (estimated) | > 95% | Automated eval |
| **Retrieval latency p50** | ~200ms (ChromaDB) | < 50ms (Qdrant payload-indexed) | Timer |
| **Citation freshness** | Unknown (no tracking) | Every citation shows `last_updated` | Metadata field |
| **Audit trail** | None | Every retrieval logged to PostgreSQL | Log count |
| **Episodic memory recall** | None | Past incidents returned within 500ms | Timer |

---

## 4. Key Capabilities (By Phase)

### Phase 0: Foundation (Weeks 1-4) — L0 → L1

**Investment:** $40K | **Headcount:** 1.5 FTE | **Risk:** Low

Adds citation metadata, DocumentLoader abstraction, PDF support, and incremental sync.

#### Architecture Change

```
Current:                          Phase 0:
┌────────────────────┐            ┌────────────────────────────┐
│ ingestion.py       │            │ DocumentLoader (ABC)       │
│  • glob *.md       │            │  ├─ MarkdownLoader         │
│  • strip front.    │            │  ├─ PDFLoader (PyMuPDF)    │
│  • split on ##     │            │  └─ (future loaders)      │
│  • embed & store   │            │ ChunkingPipeline           │
│  • delete & repeat │            │  ├─ Stage 1: Header Split  │
└────────────────────┘            │  └─ Stage 2: Recursive     │
                                  │ IncrementalSync (hash)     │
                                  │ Metadata: +3 fields        │
                                  └────────────────────────────┘
```

| Capability | Why It Matters | How It Works |
|---|---|---|
| **citation metadata** | Every chunk carries `source_url`, `last_updated`, `content_hash` | SHA256 of document → stored alongside chunk |
| **PDF ingestion** | Postmortems often arrive as PDFs | PyMuPDF → text extraction → multi-stage chunking |
| **Incremental sync** | Re-index drops from 10s to < 2s | Compare content hash before re-embedding |
| **Loader abstraction** | New formats don't require pipeline changes | `DocumentLoader` ABC with `load(path) → List[Document]` |

### Phase 1: Vector DB & RBAC Foundation (Weeks 5-6) — L1

**Investment:** $20K | **Headcount:** 1.5 FTE | **Risk:** Medium

Migrates from ChromaDB to Qdrant with payload-indexed metadata filtering. Adds HTML and DOCX loaders.

| Capability | Why It Matters | How It Works |
|---|---|---|
| **Qdrant dual-write** | Zero-downtime migration from ChromaDB | Write to both, read from Qdrant, ChromaDB as fallback |
| **Metadata pre-filtering** | Filter by `doc_type` before vector search | Qdrant payload indexes on filter fields |
| **HTML/DOCX loaders** | Enterprise docs in Word format | `langchain` BS4 loader + `python-docx` |
| **Latency tracking** | p50/p99 query metrics | Prometheus counter per query |

### Phase 2: Service Topology & Monitoring Data (Weeks 7-10) — L2

**Investment:** $50K | **Headcount:** 1.5 FTE (backend + SRE) | **Risk:** Medium-High

Ingests service topology from code repos, adds baseline store, expands to 3 services.

| Capability | Why It Matters | How It Works |
|---|---|---|
| **Service knowledge graph** | Understands cross-service dependencies | `graphify .` on each repo → Neo4j |
| **BGE-Large embedding** | 15-20% better retrieval accuracy | 1024-dim, GPU-accelerated, configurable |
| **Code-aware chunking** | Preserves function boundaries | tree-sitter AST → chunk on function defs |
| **Multi-service ingestion** | 3 services instrumented | Metrics, logs, runbooks per service |
| **PostgreSQL baseline store** | Statistical anomaly detection | TimescaleDB hypertables for time-series |

### Phase 3: Diagnostic Data & Enhanced Retrieval (Weeks 11-14) — L3

**Investment:** $50K | **Headcount:** 1.5 FTE | **Risk:** Medium

Ingests diagnostic tool outputs, adds semantic chunking, reranker, cascade reasoner.

| Capability | Why It Matters | How It Works |
|---|---|---|
| **MCP diagnostic tools** | Agent runs real read-only commands | 5 tools: SQL, Redis, circuit breaker, traces, deploy history |
| **Semantic chunking** | Better retrieval for disorganized docs | Embedding-threshold-based splitting |
| **BGE Reranker** | 2-stage retrieval: recall then re-rank | Stage 1: recall 20. Stage 2: rerank top 5 |
| **Cascade reasoner** | Cross-service root cause detection | DAG walker over Neo4j graph |
| **JWT RBAC** | Role verification on every query | JWT claims → Qdrant filter |

### Phase 4: RBAC, Memory & External Connectors (Weeks 15-20) — L4 → L5

**Investment:** $80K | **Headcount:** 2 FTE (backend + platform) | **Risk:** Medium

Full RBAC, episodic memory (Mnemon), Confluence/Slack/Jira connectors, audit trail.

| Capability | Why It Matters | How It Works |
|---|---|---|
| **Full RBAC stamping** | Every chunk has `allowed_roles[]` | Enforced at ingestion time, not query time |
| **AD/LDAP integration** | Role resolution from corporate directory | LDAP query → JWT claims → Qdrant filter |
| **Mnemon episodic memory** | Agent learns from past incidents | `remember(description, tags)` → `recall(query, k)` |
| **Post-incident learning loop** | Baselines auto-adjust | Feedback score → threshold adjustment |
| **Confluence/Slack/Jira connectors** | Enterprise knowledge enters RAG | LlamaHub loaders + hourly sync |
| **pgAudit logging** | Every retrieval logged for SOC2 | Structured audit table |

### Phase 5: Autonomous Operations (Future) — L6

| Capability | Description | Prerequisite |
|---|---|---|
| **Adaptive chunking** | ML classifier selects optimal chunk strategy per doc type | Phase 3 semantic chunking |
| **Auto-healing ingestion** | Failed loaders auto-retry with exponential backoff | Phase 0 loader abstraction |
| **Streaming ingestion** | Real-time document updates from Kafka/webhooks | Phase 4 connectors |
| **ABAC (Attribute-Based Access Control)** | Dynamic role resolution per query context | Phase 4 RBAC |
| **Self-optimizing embedding** | Fine-tuned model on incident-specific corpus | Phase 2 data |

---

## 5. Implementation Plan

### 5.1 Phased Timeline

```
W1  W2  W3  W4  W5  W6  W7  W8  W9  W10 W11 W12 W13 W14 W15 W16 W17 W18 W19 W20
■■  ■■  ■■  ■■  ■■  ■■  ■■  ■■  ■■  ■■  ■■■ ■■■ ■■■ ■■■ ■■■■ ■■■■ ■■■■ ■■■■ ■■■■ ■■■■
└──Phase 0──┘  └─P1─┘  └────Phase 2────┘  └────Phase 3────┘  └────────Phase 4────────┘
Foundation      Vector    Service Topology  MCP + Enhanced    RBAC + Memory + Connectors
                DB        + Monitoring       Retrieval              + Audit

Phase 0: Metadata, Loaders, PDF, Incremental Sync
Phase 1: Qdrant migration, HTML/DOCX, metadata pre-filtering
Phase 2: Graphify + Neo4j, BGE-Large, anomaly detector, 3 services
Phase 3: MCP tools, semantic chunking, reranker, cascade reasoner
Phase 4: Mnemon memory, RBAC + AD/LDAP, Confluence/Slack/Jira, audit
```

### 5.2 Weekly Deliverables

#### Phase 0: Foundation (Weeks 1-4)

| Week | Deliverable | Dependencies | Key Files |
|---|---|---|---|
| W1 | Add `source_url`, `last_updated`, `content_hash` to chunk metadata | None | `src/ingestion.py` |
| W2 | `DocumentLoader` abstract base class + `MarkdownLoader` refactor | W1 | New `src/loaders/__init__.py`, `src/loaders/markdown.py` |
| W3 | `PDFLoader` (PyMuPDF) + multi-stage chunking (Header → Recursive) | W2 | `src/loaders/pdf.py`, `src/chunking/` |
| W4 | Incremental sync via content hash diffing | W1 | `src/ingestion.py` |

#### Phase 1: Vector DB (Weeks 5-6)

| Week | Deliverable | Dependencies | Key Files |
|---|---|---|---|
| W5 | Qdrant Docker container + dual-write pipeline | P0 | `docker-compose.yml`, `src/ingestion.py` |
| W5 | HTMLLoader via `langchain` BeautifulSoup | W2 | `src/loaders/html.py` |
| W6 | Switch reads to Qdrant + metadata pre-filtering | W5 | `src/incident_pilot.py`, `src/retrieval.py` |
| W6 | DOCXLoader via `python-docx` | W2 | `src/loaders/docx.py` |

#### Phase 2: Service Topology + Monitoring (Weeks 7-10)

| Week | Deliverable | Dependencies | Key Files |
|---|---|---|---|
| W7 | Graphify on 3 service repos → Neo4j bootstrap | None | New `src/topology/` module |
| W7 | Service topology MCP tool (Cypher queries) | W7 | `src/topology/mcp_server.py` |
| W8 | Baseline store (PostgreSQL + TimescaleDB) | P0 | New `src/baseline/` |
| W8 | BGE-Large-en-v1.5 embedding upgrade | P1 (Qdrant) | `src/ingestion.py`, `src/incident_pilot.py` |
| W9 | Expand to 3 services: metrics, logs, runbooks | W7-W8 | Multiple |
| W9 | Proactive monitor daemon (30s poll) | W8 | New `src/monitor.py` |
| W10 | Code-aware chunking (AST function boundaries) | W2 | `src/chunking/code_chunker.py` |

#### Phase 3: MCP + Enhanced Retrieval (Weeks 11-14)

| Week | Deliverable | Dependencies | Key Files |
|---|---|---|---|
| W11 | MCP tool protocol (5 diagnostic commands: SQL, Redis, circuit breaker, traces, deploy history) | P1 | New `src/tools/` |
| W12 | Semantic chunking (embedding-threshold-based) | P0 | `src/chunking/semantic.py` |
| W13 | BGE Reranker (2-stage retrieval pipeline) | W8 BGE-Large | `src/reranker.py` |
| W14 | Cascade reasoner (DAG walker over Neo4j) | W7 topology | New `src/cascade/` |
| W14 | Diagnostic output data ingestion + recall | W11 | `src/query_logs.py` extension |

#### Phase 4: RBAC, Memory & Connectors (Weeks 15-20)

| Week | Deliverable | Dependencies | Key Files |
|---|---|---|---|
| W15 | RBAC stamping pipeline — `allowed_roles[]` on every chunk | P1 Qdrant | `src/metadata/roles.py` |
| W16 | AD/LDAP role resolution + JWT token issuance | W15 | New `src/auth/` |
| W17 | ConfluenceLoader (LlamaHub), SlackLoader, JiraLoader | P0 loaders | `src/loaders/confluence.py`, etc. |
| W18 | Mnemon episodic memory integration | None | New `src/memory/`, `mnemon` binary |
| W19 | Post-incident learning loop (feedback → threshold adjustment) | W18 Mnemon | `src/learning/` |
| W20 | pgAudit logging for every retrieval + SSO/SAML | W15 RBAC | `src/audit/` |

### 5.3 Resource Estimates

| Role | Phase 0 | Phase 1 | Phase 2 | Phase 3 | Phase 4 | Total FTE-weeks |
|---|---|---|---|---|---|---|
| Backend Python Engineer | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 20 |
| SRE / Platform Engineer | 0.5 | 0.5 | 0.5 | 0.5 | 0.5 | 10 |
| AI/ML Engineer | 0 | 0 | 0.5 | 0.5 | 0.5 | 5 |
| **Total FTE** | **1.5** | **1.5** | **2.0** | **2.0** | **2.0** | **—** |

### 5.4 Cost Summary (20 Weeks)

| Category | Phase 0 (4w) | Phase 1 (2w) | Phase 2 (4w) | Phase 3 (4w) | Phase 4 (6w) | Total |
|---|---|---|---|---|---|---|
| Engineering (1-1.5 FTE avg) | $32K | $16K | $40K | $40K | $60K | **$188K** |
| Infrastructure (compute, DBs, storage) | $4K | $2K | $6K | $6K | $10K | **$28K** |
| Third-party services (Qdrant, Graphify) | $0 | $500 | $1.5K | $1K | $3K | **$6K** |
| GPU compute (BGE-Large self-hosted) | $3K | $1K | $2K | $2K | $5K | **$13K** |
| LLM inference (Groq free tier) | $1K | $0.5K | $0.5K | $1K | $2K | **$5K** |
| **Total** | **$40K** | **$20K** | **$50K** | **$50K** | **$80K** | **$240K** |

> **Note:** LLM costs assume BGE-Large self-hosted (GPU compute) + Groq free tier for inference. If switching to OpenAI embeddings (text-embedding-3-small), add ~$5K for corpus indexing.

---

## 6. ROI & Risk Analysis

### 6.1 ROI Derivation (Incremental to Main 6-Pager)

The main IncidentPilot 6-pager calculates **$494K/year** in total savings from the full product. The ingestion pipeline alone enables a subset of those savings:

| Ingestion Capability | Enables | Annual Savings (from main 6-pager) | Ingestion Attribution |
|---|---|---|---|
| **Multi-source connectors** (Confluence, Slack, Jira) | Eliminates context-switching during triage | $30K/year (est.) | 100% — this is purely ingestion |
| **Incremental sync + freshness** | Healthy runbook corpus → accurate triage | $50K/year (est. from stale docs) | 100% — ingestion ensures freshness |
| **Episodic memory (Mnemon)** | L5 Learning Agent → 90% fewer repeat incidents | $68K/year | 80% — memory is stored/recalled via ingestion |
| **Service topology (Graphify + Neo4j)** | L2 Proactive Monitor → MTTD 15min → < 60s | $100K/year (indirect, faster detection) | 50% — detection also depends on monitoring |
| **RBAC + Audit trail** | SOC2 compliance, safe remediation | $40K/year (compliance risk, audit prep) | 100% — ingestion stamps RBAC |
| **Ingestion-attributable total** | | | **~$240K/year** |

### 6.2 Ingestion Pipeline ROI

| Time | Cumulative Investment | Cumulative Savings | Net |
|---|---|---|---|
| **Month 3** (Phase 0-1 complete) | $60K | $20K | -$40K |
| **Month 5** (Phase 2-3 complete) | $160K | $100K | -$60K |
| **Month 7** (Phase 4 complete) | $240K | $160K | -$80K |
| **Month 12** (Post-deployment) | $240K | $280K | **+$40K** |
| **Year 2** | $240K | $520K | **+$280K ROI** |

**Breakeven on ingestion investment: Month 10-11** (driven by repeat incident elimination + fresh runbooks)
**Year 2 ROI on ingestion alone: 117%**

### 6.3 Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Qdrant migration breaks existing queries** | Low | High | Dual-write for 2 weeks + ChromaDB fallback |
| **BGE-Large GPU requirement** | Medium | Medium | Quantized CPU version available, or use text-embedding-3-small API |
| **Confluence/Slack API rate limits** | Medium | Medium | Scheduled syncing with backoff; cache aggressively |
| **Mnemon is a new project** | Medium | Low | Start with non-critical path (learning loop), promote after validation |
| **RBAC stamping is too coarse** | Medium | Medium | Design metadata schema with `allowed_roles[]` array (not single role) |
| **Engineers resist another tool** | High | Low | All changes are behind the existing Gradio UI — no new UX for engineers |

### 6.4 Build vs Buy for Ingestion Components

| Component | Build | Buy / Open Source | Recommendation |
|---|---|---|---|
| **Document loaders** | Custom per format | LlamaHub (150+ connectors) | **LlamaHub** — richer ecosystem, faster integration |
| **Vector store** | ChromaDB (current) | Qdrant / Weaviate / Pinecone | **Qdrant** — self-hosted, JWT RBAC, LangChain support |
| **Episodic memory** | Custom PostgreSQL + pgvector | Mnemon (Apache 2.0) | **Mnemon** — single binary, dedup, decay, zero external deps |
| **Service topology** | Manual YAML | Graphify (MIT) | **Graphify** — auto-generates from code repos, MCP-native |
| **Embedding model** | all-MiniLM (current) | BGE-Large / Instructor-XL | **BGE-Large** — best open-source accuracy per cost |
| **Audit logging** | Custom | pgAudit (PostgreSQL) | **pgAudit** — battle-tested, compliance-ready |

### 6.5 Key Open Questions

1. **Embedding model strategy** — Single model vs model routing (BGE-Large for technical runbooks, Instructor-XL for postmortem narratives)? Single model is simpler; routing adds complexity but could improve accuracy.

2. **Qdrant cluster vs standalone** — For 10+ services with 100-200 docs, standalone Qdrant is sufficient. When does clustering become necessary? (Estimate: > 1M chunks or > 50 services.)

3. **Confluence sync frequency** — Hourly sync is aggressive. Is daily sufficient for incident response? Confluence runbooks are updated infrequently; daily sync with on-demand refresh during active incidents is a reasonable hybrid.

4. **Mnemon integration pattern** — CLI subprocess calls vs embedding Go binary via cgo? CLI adds ~50ms per call but avoids build complexity. Acceptable for non-critical path.

5. **LLM dependency for semantic chunking** — Semantic chunking requires an embedding model for threshold-based splitting. If the embedding service is down, should we fall back to recursive splitting? (Yes — fail-open.)

---

## 7. FAQ

**Q: Why refactor ingestion at all? ChromaDB + Markdown works today.**
A: It works for a demo with 5 runbooks. For 10+ services with 100+ documents, every gap becomes a blocker: no RBAC means no multi-team use, no incremental sync means 30+ second re-indexes, no multi-format means Confluence/Slack/Jira data is invisible.

**Q: Why Qdrant and not Weaviate or pgvector?**
A: All three are viable. Qdrant is chosen because: (1) JWT-based RBAC at the collection level — no extra DB round-trip for permission checks, (2) self-hosted in Docker (same as the rest of our stack), (3) payload indexes on metadata fields enable sub-50ms filtered searches, (4) proven Python and LangChain integrations. Weaviate's native RBAC is also strong; the choice is Qdrant for simpler integration with our existing LangChain stack.

**Q: Why Mnemon for episodic memory instead of building on PostgreSQL?**
A: Mnemon handles the hard parts we'd otherwise build ourselves: (1) four-graph memory (temporal, entity, causal, semantic), (2) importance decay with automated GC, (3) built-in deduplication, (4) zero external API dependencies (single Go binary + SQLite). Building this on PostgreSQL + pgvector would take 3-4 weeks. Mnemon integration is 2 days.

**Q: How do we ensure the embedding model upgrade doesn't break existing queries?**
A: (1) Configurable model selection — the model is a setting, not hardcoded. (2) Dual-index during migration — both all-MiniLM and BGE-Large indexes live in Qdrant simultaneously. (3) A/B test retrieval quality for 1 week before switching default. (4) Document the trade-off: BGE-Large needs GPU or quantized CPU.

**Q: What happens when Confluence/Slack API rate limits are hit?**
A: (1) Exponential backoff with jitter. (2) Sync is scheduled (hourly), not real-time — a missed sync window is acceptable. (3) Cached data from the last successful sync serves queries. (4) Slack's rate limit is per-workspace; we sync per-channel with offsets.

**Q: This adds 5 new infrastructure services (Qdrant, Neo4j, PostgreSQL, Mnemon, Graphify). Is that sustainable?**
A: Three of these are additive only in later phases: Neo4j (Phase 2, W7), PostgreSQL baseline store (Phase 2, W8), Mnemon (Phase 4, W18). Qdwart replaces ChromaDB (net zero). The total infrastructure footprint at Phase 4 is manageable: Qdrant (~1GB RAM), Neo4j (~2GB), PostgreSQL (~1GB), Mnemon (~100MB). All fit within a single Docker host with 8GB RAM.

---

## Appendix A: Metadata Schema Evolution

```
Phase 0                Phase 1                Phase 2                Phase 4
┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐   ┌────────────────────────────┐
│ source          │   │ source          │   │ source          │   │ source                     │
│ section         │   │ section         │   │ section         │   │ section                    │
│ source_url      │   │ source_url      │   │ source_url      │   │ source_url                 │
│ last_updated    │   │ last_updated    │   │ last_updated    │   │ last_updated               │
│ content_hash    │   │ content_hash    │   │ content_hash    │   │ content_hash               │
│                 │   │ doc_type        │   │ doc_type        │   │ doc_type                   │
│                 │   │ word_count      │   │ word_count      │   │ word_count                 │
│                 │   │ file_size       │   │ file_size       │   │ file_size                  │
│                 │   │                 │   │ service         │   │ service                    │
│                 │   │                 │   │ team            │   │ team                       │
│                 │   │                 │   │ repo_url        │   │ repo_url                   │
│                 │   │                 │   │                 │   │ tool_name                  │
│                 │   │                 │   │                 │   │ exit_code                  │
│                 │   │                 │   │                 │   │ diagnostic_type            │
│                 │   │                 │   │                 │   │ allowed_roles[]            │
│                 │   │                 │   │                 │   │ source_url_external        │
│                 │   │                 │   │                 │   │ original_author            │
│                 │   │                 │   │                 │   │ last_synced                │
│                 │   │                 │   │                 │   │
│ 5 fields        │   │ 8 fields        │   │ 11 fields       │   │ 18 fields                  │
│                 │   │                 │   │                 │   │
│                 │   │                 │   │                 │   │ Phase 5 adds:              │
│                 │   │                 │   │                 │   │ confidence_score           │
│                 │   │                 │   │                 │   │ auto_generated             │
│                 │   │                 │   │                 │   │ review_status              │
│                 │   │                 │   │                 │   │ feedback_loop_ref          │
└─────────────────┘   └─────────────────┘   └─────────────────┘   └────────────────────────────┘
```

## Appendix B: Infrastructure Footprint Comparison

| Service | Current (Phase 0) | Phase 1 | Phase 2 | Phase 3 | Phase 4 |
|---|---|---|---|---|---|
| **ChromaDB** | ✅ Persistent | ✅ Fallback | ❌ Removed | ❌ | ❌ |
| **Qdrant** | ❌ | ✅ Primary | ✅ (payload-indexed) | ✅ (payload-indexed) | ✅ (payload-indexed) |
| **Neo4j** | ❌ | ❌ | ✅ + Graphify | ✅ + Graphify | ✅ + Graphify |
| **PostgreSQL** | ❌ | ❌ | ✅ Baseline store | ✅ Baseline store | ✅ + pgAudit |
| **Temporal** | ❌ | ❌ | ❌ | ✅ Durable execution | ✅ |
| **Mnemon** | ❌ | ❌ | ❌ | ❌ | ✅ Episodic memory |
| **Total RAM** | ~500MB | ~1.5GB | ~4GB | ~5GB | ~6GB |
| **CPU cores** | 1 | 2 | 4 | 4 | 4 |

## Appendix C: Decision Matrix

| Decision Point | Option A | Option B | Option C | Recommendation |
|---|---|---|---|---|
| **RAG Framework** | Keep LangChain (current) | Migrate to LlamaIndex | LangChain + LlamaHub loaders | **LangChain + LlamaHub** — best of both |
| **Vector Store** | Keep ChromaDB | Qdrant | Weaviate | **Qdrant** — JWT RBAC, self-hosted, LangChain-native |
| **Embedding Model** | Keep all-MiniLM-L6-v2 | BGE-Large-en-v1.5 | text-embedding-3-small | **BGE-Large** — best open-source accuracy, self-hostable |
| **Chunking** | Keep MarkdownHeader | Multi-stage (Header → Recursive → Semantic) | Late Chunking | **Multi-stage** — pragmatic quality per cost |
| **Episodic Memory** | Custom PostgreSQL + pgvector | Mnemon | Skip for now | **Mnemon** — Apache 2.0, dedup, decay, 2-day integration |
| **Service Topology** | Manual YAML | Graphify + Neo4j | GraphRAG | **Graphify + Neo4j** — deterministic, code-level, MCP-native |
| **Auth / RBAC** | None (current) | JWT + Qdrant filtering | OpenFGA | **JWT + Qdrant** — simpler, no extra infra |
| **Audit** | None | pgAudit | Custom logger | **pgAudit** — battle-tested, SOC2-ready |

## Appendix D: Loader Abstraction Interface

```python
# Proposed interface — src/loaders/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

@dataclass
class Document:
    """Standard document representation for all loaders."""
    content: str
    source_url: str
    doc_type: str  # "runbook", "postmortem", "arch_doc"
    last_updated: Optional[str] = None  # ISO 8601
    content_hash: Optional[str] = None  # SHA256
    metadata: dict = field(default_factory=dict)

class DocumentLoader(ABC):
    """All loaders implement this interface. No exceptions."""

    @abstractmethod
    def load(self, source: str) -> list[Document]:
        """Load documents from a source path or URI.
        
        Args:
            source: Local file path, URL, Confluence space ID, Slack channel, etc.
            
        Returns:
            List of Document objects with content and metadata.
            
        Raises:
            LoaderError: If the source is unreachable or malformed.
        """
        ...

    @abstractmethod
    def supported_formats(self) -> list[str]:
        """Return list of MIME types or format identifiers this loader handles."""
        ...
```

## Appendix E: Migration Path (ChromaDB → Qdrant)

```
Week 5: Dual-Write
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   ingestion.py   │────▶│    ChromaDB     │     │   No change     │
│   (unchanged)    │     │    (primary)    │────▶│  (reads go to   │
│                  │────▶│     Qdrant      │     │   ChromaDB)     │
└─────────────────┘     │   (shadow copy)  │     └─────────────────┘
                         └─────────────────┘

Week 6: Switch Read
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   ingestion.py   │────▶│    ChromaDB     │     │  Fallback only  │
│   (unchanged)    │     │   (fallback)    │     │                 │
│                  │────▶│     Qdrant      │────▶│  (reads go to   │
│                  │     │   (primary)     │     │   Qdrant)       │
└─────────────────┘     └─────────────────┘     └─────────────────┘

Rollback Plan:
  - If Qdrant returns errors in > 1% of queries for 24 hours:
    1. Set environment variable: VECTOR_STORE=chromadb  (env var support added in W5)
    2. Restart the agent service
    3. All reads route to ChromaDB within 30 seconds
    4. File a P1 bug to fix the Qdrant issue
```

> **Env var support:** The `VECTOR_STORE` environment variable is introduced during Week 5 when Qdrant dual-write is implemented. It defaults to `qdrant` and accepts `chromadb` as a fallback value. No code changes are needed post-deployment — the rollback is a config change + restart.

---

*This document is a companion to `docs/incident-pilot-6-pager.md`. Read that document first for the full product strategy, ROI analysis, and enterprise multi-agent architecture.*

> **Related documents:** [`docs/ingestion/ingestion-analysis.md`](ingestion-analysis.md) (full analysis) · [`docs/ingestion/INGESTION_EXECUTIVE_SUMMARY.md`](INGESTION_EXECUTIVE_SUMMARY.md) (executive summary)
