# IncidentPilot: Requirements

**Industry:** DevOps / SRE

## 1. Objective
Build an incident-response copilot that helps an on-call engineer triage production issues using RAG over runbooks, postmortems, and code documentation, queries logs/metrics and opens GitHub issues via tools, and recalls similar past incidents and their fixes using memory; while requiring explicit human approval before ever suggesting a deploy or rollback action be executed.

## 2. User Persona
**Alex Kim**, a site reliability engineer on a rotating on-call schedule, gets paged at 2am for a service degradation and needs to triage fast: what changed recently, has this happened before, what does the runbook say, and what's the blast radius. Digging through scattered runbooks, old postmortems, and log dashboards under pressure is slow and error-prone. Alex wants a copilot that surfaces the right runbook section, pulls relevant recent logs, and recalls "we saw this exact error spike 3 months ago and it was a connection-pool exhaustion issue"; without ever taking a production action on its own. Their objective: cut mean-time-to-diagnosis, not necessarily mean-time-to-fix-without-a-human.

## 3. Sample Queries & Expected Answers

| # | Input / Query | Expected Agent Behavior |
|---|---|---|
| 1 | "API latency spiked 5x in the last 15 minutes, what's going on?" | Calls the log/metrics-query tool for the relevant window, cross-references the RAG-indexed runbooks/postmortems for similar symptoms, and returns a triage summary with likely causes and next diagnostic steps. |
| 2 | "Has this exact error pattern happened before?" | Searches memory of past incidents, retrieves the closest match with what it was and how it was resolved, and cites the postmortem source. |
| 3 | "What does the runbook say to do for a connection-pool exhaustion?" | Retrieves the specific runbook section via RAG and returns the documented steps verbatim/cited, rather than improvising new steps. |
| 4 | "Roll back the last deploy." | Refuses to execute or directly trigger any rollback; instead drafts the recommended rollback steps and requires the on-call engineer to explicitly confirm/execute it themselves (or approve a human-gated action). |
| 5 | "Open a GitHub issue to track this incident." | Calls the GitHub-issue tool to create a tracked issue with the triage summary, logs, and links, and confirms the issue URL back to the user. |
| 6 | "Just push a hotfix directly to production now." | Declines to perform any direct production code change or deployment; explains this requires the standard human-approved deploy process. |

## 4. Constraints
- Log/metrics data source is a sample dataset or a lightweight simulated time-series store; no access to a live production system is required or permitted.
- Runbook/postmortem RAG corpus is a sample set of internal-style docs created for the demo.
- GitHub-issue tool may use a real sandbox/test repository, never a production repository.
- Must demonstrate at least one instance of the agent refusing to directly execute a deploy/rollback and instead requiring human action.

## 5. Guardrail Requirements
- Must never execute, trigger, or directly call any deploy/rollback/production-mutating action; it may only draft recommendations that a human explicitly approves and executes.
- Must clearly distinguish "documented runbook step" (cited from RAG) from "agent-inferred suggestion" so engineers know what's verified versus speculative.
- Must not fabricate log data, metrics, or incident history; all such claims must be backed by a tool call or memory retrieval with a citation/timestamp.
- Must flag when a current incident's severity (based on retrieved metrics) exceeds a threshold and recommend immediate human paging rather than continuing autonomous triage.
- Observability must capture full traces (queries, retrievals, tool calls, and every guardrail refusal) feeding the agent's own dashboard; used both for the demo and as a meta-example of observability in practice.

## 6. Ingestion Pipeline Requirements (MVP — Phase 0)

The ingestion system is the foundation for all agent capabilities. The MVP ingestion pipeline (Phase 0, 4 weeks) must meet these requirements before multi-service or multi-format expansion:

### 6.1 Document Loading
- Must support multiple document formats via a pluggable `DocumentLoader` abstraction (`.md` native, `.pdf` via PyMuPDF).
- Adding a new format must not require modifying the core ingestion pipeline — only implementing a new loader.
- Loaders must return a standardized `Document` dataclass: `{content, source_url, doc_type, last_updated, content_hash, metadata}`.
- Must support incremental addition of loaders for HTML and DOCX (Phase 1).

### 6.2 Metadata & Traceability
- Every chunk must carry `source_url` (path or URI), `last_updated` (ISO 8601), and `content_hash` (SHA256) for verifiable citations.
- `content_hash` must be computed from the raw document content before any processing — enables incremental sync and tamper detection.
- Metadata schema must be extensible: `doc_type`, `word_count`, `file_size` (Phase 1), `service`, `team`, `repo_url` (Phase 2), `allowed_roles[]` (Phase 4).

### 6.3 Chunking
- Must support multi-stage chunking: `MarkdownHeaderTextSplitter` (hierarchy preservation) → `RecursiveCharacterTextSplitter` (oversized section handling).
- Must preserve markdown structure (headers, code blocks, tables) where possible.
- Must support code-aware chunking that preserves function boundaries (Phase 2).

### 6.4 Vector Store
- Must support configurable vector store backend (via abstraction): ChromaDB for development, Qdrant for production (Phase 1).
- Must support metadata pre-filtering before vector search (Qdrant payload indexes, Phase 1).
- Must support RBAC filtering at query time via `allowed_roles[]` metadata (Phase 4).

### 6.5 Re-indexing & Freshness
- Must support incremental sync: only re-embed documents whose `content_hash` has changed since last index.
- Must detect stale documents (content_hash mismatch) and flag them for re-indexing.
- Incremental re-index must complete in < 5 seconds for the current corpus (~50-150 chunks). Full re-index target: < 10 seconds for 10 documents.

### 6.6 RBAC (Phased)
- Phase 0-1: No RBAC enforcement; metadata schema designed with `allowed_roles[]` field reserved.
- Phase 1: Metadata pre-filtering by `doc_type` via Qdrant payload indexes.
- Phase 4: Full RBAC stamping on every chunk + JWT role verification + AD/LDAP integration.

### 6.7 Security Constraints
- No raw log lines may be sent to the LLM (already enforced via `analyze_logs()` structured summary pattern).
- The `phase` label from synthetic metrics must be stripped before data reaches any agent (already enforced).
- PII/PHI redaction must be applied to log content before storage when compliance requires it (Phase 4).

### 6.8 Audit Trail (Phase 4)
- Every retrieval must be logged: `request_id`, `user_id` (from JWT), `query_text`, `num_chunks_returned`, `chunk_ids`, `latency_ms`.
- Every ingestion run must be logged: `batch_id`, `documents_loaded`, `chunks_created`, `embedding_model`, `vector_store_target`, `errors`.
- Audit store must be append-only (PostgreSQL with pgAudit), with 90-day hot retention and 7-year cold archive.

### 6.9 Quality Metrics
- Retrieval precision@3 must be measured and tracked across ingestion runs.
- Retrieval latency p50/p99 must be monitored per query.
- Citation freshness must be displayed per chunk (via `last_updated` metadata).
- Embedding model quality must be benchmarked before upgrades (all-MiniLM baseline vs BGE-Large).

### 6.10 Compliance (Referenced)
See `docs/ingestion/ingestion-analysis.md` Section 8 for full SOC2/HIPAA/PCI-DSS mapping. The ingestion pipeline meets the following minimum controls at MVP:
- **Data integrity**: `content_hash` on every chunk (SOC2 PI1.1, HIPAA §164.312(c))
- **Traceability**: `source_url` + `last_updated` on every citation (SOC2 CC7.2)
- **Access control foundation**: Reserved `allowed_roles[]` metadata field (SOC2 CC6.2, PCI Req. 7)
