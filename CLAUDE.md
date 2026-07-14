# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

**incident-pilot** is an AI-powered incident-response copilot for on-call SRE engineers. It uses RAG over runbooks, postmortems, and code docs; queries synthetic logs/metrics via tools; recalls similar past incidents via memory; and opens GitHub issues — but it **never executes deploys, rollbacks, or any production-mutating action** without explicit human approval. The primary user persona is Alex Kim, an SRE who needs fast triage at 2am, not autonomous fixes.

Tech stack: **Python + LangChain**. The agent is in early build (Week 1–2 of a 4-week plan).

## Running the synthetic data generator

The only runnable code so far is the data-generation script. Run it from inside `synthetic-data/script/`:

```bash
cd synthetic-data/script
python generate_synthetic_data.py
```

This writes two pairs of files into `synthetic-data/metrics/` and `synthetic-data/logs/`:
- `checkout-api-2026-05-14-{metrics.json,app-logs.jsonl}` — a past resolved incident
- `checkout-api-current-{metrics.json,app-logs.jsonl}` — an "ongoing" incident anchored to `now`

Re-running overwrites both files. The script validates metric/log alignment at the end and raises `AssertionError` on any mismatch — this is intentional; don't suppress it.

## Corpus layout

| Directory | Contents |
|---|---|
| `synthetic-data/runbooks/` | Service runbooks for RAG indexing — one file per service |
| `synthetic-data/postmorterms/` | Past-incident postmortems for RAG indexing |
| `synthetic-data/metrics/` | JSON time-series metrics for the log/metrics query tool |
| `synthetic-data/logs/` | JSONL application logs, aligned with metrics by construction |
| `docs/` | Generation prompts and team context |

## Conventions for runbooks and postmortems

**Before adding a new runbook**, read `docs/RUNBOOK_GENERATION_PROMPT.md`. Key constraints:
- Frontmatter: only `service` and `doc_type` fields — no extras.
- Separate triage paths for `p99-latency-high` vs `error-rate-high`; they are different failure families.
- No rollback/deploy instructions in the runbook — those belong in the guardrail layer.
- No API schemas inline — reference a `code-docs/<service>-request-schema.md` by name.
- Every `##` section must be self-contained (the file is chunked on headers).

**Before adding a new postmortem**, read `docs/POSTMORTEM_GENERATION_PROMPT.md`. Key constraints:
- Title by symptom/impact, not root cause.
- `related_runbooks` must use the exact filename that exists in `synthetic-data/runbooks/`.
- The `tags` root-cause phrase must match the exact phrase used in the corresponding runbook's Known Issue heading.
- No "key excerpts for retrieval" section — that's the memory layer's job, populated from real sessions.
- Action items table: max 2 rows, each independently verifiable against the rest of the corpus.

## Architecture and data flow

### Component map

```
┌─────────────────────────────────────────────────────────────────────┐
│  Alex Kim (on-call SRE)                                             │
│  Types an incident query, e.g. "API latency spiked 5x — what's     │
│  going on?" or "has this happened before?"                          │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ query
                                ▼
┌───────────────────────────────────────────────────────────────────┐
│  Gradio UI                                                        │
│  • Text input / response display                                  │
│  • Expandable agent-trace panel (tool calls, retrieved chunks,    │
│    memory matches)                                                │
│  • Badges: guardrail status | cache hit/miss                      │
└───────────────────────────────┬───────────────────────────────────┘
                                │
                                ▼
┌───────────────────────────────────────────────────────────────────┐
│  LangChain Agent (orchestrator)                                   │
│  Governed by System Prompt: triage-copilot tone +                 │
│  "no autonomous production actions" rule                          │
│                                                                   │
│  ┌─────────────────┐   ┌──────────────────┐   ┌───────────────┐  │
│  │ Guardrail Layer │   │   RAG Retrieval  │   │    Memory     │  │
│  │ (unconditional, │   │                  │   │   Store       │  │
│  │ runs first on   │   │  Vector Store    │   │ (cross-session│  │
│  │ every query)    │   │  ┌────────────┐  │   │  past-        │  │
│  │                 │   │  │ Embedded   │  │   │  incident     │  │
│  │ Blocks: deploy, │   │  │ chunks of  │  │   │  records)     │  │
│  │ rollback,       │   │  │ synthetic- │  │   │               │  │
│  │ hotfix, any     │   │  │ data/      │  │   │  Reads:       │  │
│  │ production-     │   │  │ runbooks/  │  │   │  incident     │  │
│  │ mutating action │   │  │ postmortem │  │   │  records from │  │
│  └─────────────────┘   │  │ s/         │  │   │  prior        │  │
│                         │  └────────────┘  │   │  sessions     │  │
│                         └──────────────────┘   │               │  │
│                                                └───────────────┘  │
│  ┌──────────────────────────────────────────┐  └───────────────┘  │
│  │  Tools (exposed via MCP)                 │                     │
│  │                                          │                     │
│  │  query_logs(service, timeframe)          │                     │
│  │  ┌──────────────────────────────────┐   │                     │
│  │  │ Cache (keyed on service+timeframe│   │                     │
│  │  │ — miss hits synthetic-data/)     │   │                     │
│  │  └──────────────────────────────────┘   │                     │
│  │                                          │                     │
│  │  create_github_issue(summary, labels)    │                     │
│  │  → GitHub sandbox repo API               │                     │
│  └──────────────────────────────────────────┘                     │
│                                                                   │
│  Observability: every query, retrieval, tool call, cache event,   │
│  and guardrail refusal is traced under a single trace ID          │
└───────────────────────────────┬───────────────────────────────────┘
                                │ cited response
                                ▼
                          Alex Kim / Gradio UI
```

### Data sources each component reads

| Component | Reads from | Writes to |
|---|---|---|
| RAG ingestion pipeline | `synthetic-data/runbooks/*.md`, `synthetic-data/postmorterms/*.md` | Vector store (in-memory or persistent) |
| RAG retrieval | Vector store | — |
| `query_logs` tool | `synthetic-data/logs/*.jsonl`, `synthetic-data/metrics/*.json` | Cache |
| Cache | Cache store | Cache store |
| `create_github_issue` tool | — | GitHub sandbox repo |
| Memory | Memory store (past sessions) | Memory store |
| Observability | Agent events | Trace/dashboard store |

### Query-to-response sequence

```
Alex types query
        │
        ▼
1. GUARDRAIL CHECK (first, unconditional)
   Is this a deploy / rollback / production-mutating request?
   ├── YES → return refusal immediately; log guardrail event; stop here
   └── NO  → continue
        │
        ▼
2. MEMORY LOOKUP
   Search past-incident records for similar symptom patterns
   └── Returns: closest past incident(s) with resolution summary + postmortem citation
        │
        ▼
3. RAG RETRIEVAL
   Embed the query → cosine search over Vector Store
   └── Returns: top-k chunks from synthetic-data/runbooks/ and synthetic-data/postmorterms/
        │        (each chunk tagged: source file + section header)
        ▼
4. TOOL CALLS (agent decides which, if any, are needed)
   ├── query_logs(service, timeframe)
   │     └── Cache hit?  YES → return cached result (log cache hit)
   │                     NO  → read synthetic-data/logs/*.jsonl +
   │                            synthetic-data/metrics/*.json
   │                          → strip "phase" field before returning to agent
   │                          → store in cache; log cache miss
   │
   └── create_github_issue(summary, labels)
         └── Call GitHub sandbox API → return issue URL
        │
        ▼
5. RESPONSE ASSEMBLY
   Agent composes triage summary, explicitly labelling each claim:
   ├── "Documented runbook step" — sourced from a RAG chunk (cite file + section)
   ├── "Past incident match" — sourced from memory (cite incident ID + postmortem)
   ├── "Live data" — sourced from query_logs tool call (cite timeframe)
   └── "Agent-inferred suggestion" — not from a retrieved source (clearly flagged)

   If retrieved metrics exceed severity threshold → recommend immediate human
   paging instead of continuing autonomous triage
        │
        ▼
6. GRADIO UI RENDERS
   ├── Response text with citations
   ├── Agent trace panel: tool calls made, chunks retrieved, memory matches
   └── Badges: [Guardrail: passed | blocked] [Cache: hit | miss]
        │
        ▼
7. OBSERVABILITY
   Full session trace (all steps above) written with shared trace ID
   → feeds the observability dashboard
```

### Critical architectural constraint

The guardrail layer (step 1) must be unconditional — it must **not** depend on RAG retrieval or memory being available. If the vector store fails to load, the agent must still refuse deploy/rollback requests. Implement guardrail checks as a pre-agent classifier or prompt-level rule that runs before any retrieval or tool call.

The synthetic-data `phase` field in metrics (`baseline`/`climbing`/`plateau`/`recovering`) is a dataset-layer annotation only — strip it before returning data to the agent so the agent doesn't see ground-truth labels it should be inferring.

## Key guardrail requirement

Queries 4 and 6 from `requirements/requirements.md` must be refused at all times ("roll back the last deploy", "push a hotfix directly to production"). This refusal must fire even if the RAG corpus or memory layer fails to load. Test these explicitly before any demo.
