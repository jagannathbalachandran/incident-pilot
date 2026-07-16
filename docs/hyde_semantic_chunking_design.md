# HyDE + Semantic Chunking: Why They Work Together for Incident Triage

## Purpose of This Document

This document explains how HyDE (multi-query expansion) and semantic chunking
complement each other specifically in the incident triage domain. It covers how
HyDE works, why it is effective for triage, and why semantic chunking is the right
chunking strategy to pair with it. Concrete query examples show the before-and-after
difference in retrieval quality.

This is a companion to `rag_chunking_retrieval_design.md` which explains the
problem statement and options. Read that first if you have not already.

---

## Quick Recap: The Two Problems

**Problem 1 — Chunking:** Enterprise runbooks come in unpredictable formats. We
cannot write format-specific code per client. Solution: semantic chunking — split
on meaning shifts, not visual structure.

**Problem 2 — Retrieval:** An engineer's symptom vocabulary does not match the
triage vocabulary in runbooks. "High latency" does not semantically match "check
active_connections vs max_connections" even though that chunk is the most important
thing to retrieve. Solution: HyDE — expand the query before hitting the vector store.

These two problems are independent. Semantic chunking solves Problem 1. HyDE solves
Problem 2. Neither solves the other's problem. Both are needed.

---

## What HyDE Is

HyDE stands for Hypothetical Document Expansion. In its original academic form, it
generates a hypothetical answer document and uses that to search instead of the
original query. In this project we use a practical variant: **multi-query expansion**.

Instead of searching once with the engineer's raw query, we ask the LLM to generate
4-6 targeted search queries that together cover all the diagnostic angles relevant
to the symptom. All queries run against ChromaDB independently. Results are merged
and deduplicated. The union is passed to the LLM for final triage synthesis.

```
Engineer types a symptom
        │
        ▼
LLM expands into 4-6 targeted queries
        │
        ├── Query 1 → ChromaDB → top-3 chunks
        ├── Query 2 → ChromaDB → top-3 chunks
        ├── Query 3 → ChromaDB → top-3 chunks
        ├── Query 4 → ChromaDB → top-3 chunks
        └── Query 5 → ChromaDB → top-3 chunks
                │
                ▼
        Deduplicate and merge
                │
                ▼
        LLM synthesises full triage summary
        citing all retrieved chunks
```

---

## Why Triage Is a Particularly Good Domain for HyDE

Most domains have a one-to-one relationship between a query and its answer. "What
is the capital of France?" has one answer. "What does this function return?" has
one answer.

Incident triage is fundamentally different. One symptom has multiple possible root
causes. An engineer does not know which cause it is — that is the entire point of
triage. They need to:

1. Retrieve all possible causes for the symptom
2. Check each one systematically to confirm or rule it out
3. Follow the correct mitigation path for whichever cause is confirmed
4. Escalate correctly if nothing resolves it

A single semantic search query cannot retrieve all of this because each triage path
uses different vocabulary. The connection pool check, the cache check, the downstream
dependency check, and the escalation path are all semantically distinct topics. They
live in different chunks. They will not all match a single query.

HyDE solves this directly: the LLM knows that "high latency on a web service backed
by a database and a cache" is typically caused by connection pool exhaustion, cache
layer issues, downstream dependency slowness, or a bad deploy. It generates one
query per hypothesis. Each query retrieves the relevant triage path. The engineer
gets the complete diagnostic picture.

---

## Concrete Examples

### Example 1: "checkout-api p99 latency climbing gradually for 15 minutes"

**Without HyDE — single semantic search:**

The query "p99 latency climbing gradually" matches chunks that describe the symptom:

```
Result 1 — runbook | When to use this runbook
"checkout-api-p99-latency-high (p99 > 1500ms for 5 min)"

Result 2 — postmortem | Summary
"p99 latency climbs gradually over 10-20 minutes as connections fill up"

Result 3 — ISTM | Overview
"p99 latency climbs gradually (not a step change) while active_connections
pins at max_connections"
```

The engineer gets three chunks describing what the symptom looks like. Nothing about
what to do. No cache check. No downstream check. No mitigation. No escalation path.

---

**With HyDE — LLM generates 5 targeted queries:**

```
Query 1: "connection pool exhaustion active_connections max_connections check"
Query 2: "Redis cache hit ratio drop failover triage checkout-api"
Query 3: "downstream dependency latency payment-gateway fraud-scoring spike"
Query 4: "PgBouncer pool size mitigation headroom postgres max_connections"
Query 5: "checkout-api escalation path SEV1 incident commander unresolved"
```

Each query retrieves a different part of the triage picture:

```
Query 1 retrieves →
  runbook | Triage: if p99-latency-high fired
  "Check connection pool dashboard — if active_connections pinned at
  max_connections → go to Known Issue #1"
  → First triage step: confirm or rule out pool exhaustion

Query 2 retrieves →
  runbook | Triage: if p99-latency-high fired
  "Check cache hit rate — if dropped sharply (~95% → under 50%)
  → go to Known Issue #2"
  → Second triage step: rule out cache failover

Query 3 retrieves →
  runbook | Triage: if p99-latency-high fired
  "Check downstream dependency latency — if payment-gateway-svc spiked
  at the same time → escalate to that dependency's on-call, no local fix"
  → Third triage step: rule out upstream cause

Query 4 retrieves →
  Scoutflo | Playbook
  "Increase PgBouncer pool size: edit checkout-pool.ini, raise
  default_pool_size, reload via RELOAD — check headroom first"
  → Mitigation steps if pool exhaustion is confirmed

Query 5 retrieves →
  Runbook Template | Escalation Matrix
  "Unresolved after 15 min → page team lead, open #inc-checkout-latency.
  Unresolved after 30 min → declare SEV1, page incident commander"
  → What to do if triage does not resolve it
```

The engineer gets the full triage flow — three root causes to check in order,
mitigation if the most common cause is confirmed, and escalation if not — from
a single symptom description.

---

### Example 2: "error rate is climbing but latency looks normal"

**Without HyDE:**

"Error rate high latency normal" retrieves chunks mentioning errors and error rates:

```
Result 1 — postmortem | Impact
"~9% of checkout attempts failed with 503 or exceeded client timeout"

Result 2 — runbook | When to use this runbook
"checkout-api-error-rate-high (5xx+4xx rate > 2%)"

Result 3 — ISTM | Overview
"pool-acquisition timeout errors appear in application logs"
```

The engineer gets descriptions of what high error rate looks like. They do not get
the critical framing that "fast failures with normal latency is a completely different
failure family from the latency-based known issues" — which is the most important
thing to understand before starting triage on this symptom.

---

**With HyDE — 5 targeted queries:**

```
Query 1: "error rate high latency normal fast failures request contract"
Query 2: "4xx 400 bad request schema mismatch client payload triage"
Query 3: "401 unauthorized auth token rotation expired checkout-api"
Query 4: "5xx application error logs stack traces deploy correlation"
Query 5: "error rate path no pool cache involvement diagnostic only"
```

Each retrieves a distinct piece of the error-rate triage path:

```
Query 1 retrieves →
  runbook | Triage: if error-rate-high fired without elevated p99 latency
  "Fast failures point at the request/contract, not resource contention —
  a different failure family from Known Issue #1/#2 which are slowness-driven"
  → The critical framing that steers the engineer away from pool/cache investigation

Query 2 retrieves →
  runbook | Triage: error-rate path
  "400 Bad Request → payload doesn't match expected schema. Check for a
  recent deploy that changed the request contract, or a client not updated"
  → Specific 400 diagnostic path

Query 3 retrieves →
  runbook | Triage: error-rate path
  "401 Unauthorized → auth token missing, expired, or invalid. Check for
  a recent token-rotation or auth-service change"
  → Specific 401 diagnostic path

Query 4 retrieves →
  runbook | Triage: error-rate path
  "5xx → check application error logs for stack traces — a fast-failing
  bug is exactly what a bad code change looks like"
  → Specific 5xx diagnostic path

Query 5 retrieves →
  Runbook Template | Diagnostic Steps
  "No documented mitigation exists for this path — diagnostic steps only.
  If unresolved, escalate per Section 6"
  → Sets expectations correctly — this path has no quick fix
```

The engineer gets the full 4xx vs 5xx decision tree, all three sub-paths, the
correct failure family framing, and the expectation that this path has no immediate
mitigation — from a symptom query with no technical vocabulary.

---

### Example 3: Vague High-Urgency Query

**Engineer types:** "checkout is down, customers can't complete purchases"

This is the hardest case — no technical vocabulary, maximum urgency, ambiguous
symptom. Naive retrieval gets almost nothing actionable.

**With HyDE — 6 targeted queries:**

```
Query 1: "checkout-api p99 latency high alert triage diagnostic steps"
Query 2: "checkout-api error rate 5xx 4xx high alert triage"
Query 3: "connection pool exhaustion postgres active_connections mitigation"
Query 4: "Redis cache failover cluster_state ok hit ratio recovery"
Query 5: "SEV1 incident commander escalation revenue impact customer facing"
Query 6: "downstream dependency payment-gateway inventory-svc latency spike"
```

Six queries cover every possible failure mode. The engineer gets a complete
incident response starting point — both alert types, both known issues, escalation,
and downstream dependency checks — from a panic message with no technical detail.

This is where HyDE is most valuable: the more vague and urgent the query, the more
the LLM's knowledge of failure modes compensates for the engineer's inability to
articulate technical specifics at 2am under pressure.

---

## Why Semantic Chunking Is the Right Pairing for HyDE

HyDE generates good queries. The quality of the final answer depends on whether
those queries retrieve good chunks. This is where the chunking strategy matters.

### Problem with section-based chunks and HyDE

Consider a section-based chunk from the Runbook Template format:

```
3. Diagnostic Steps
Step 1: Determine which alert fired
Step 2: Latency path — check pool, cache, downstream
Step 3: Error-rate path — check 4xx vs 5xx
4.1 Common Fixes — pool exhaustion mitigation, cache failover mitigation
```

This chunk is large, contains multiple diagnostic paths, and when retrieved it is
hard to cite precisely. The LLM has to extract the relevant parts rather than
citing the chunk as a unit. Worse, if the chunk is too large for the embedding
model and gets recursively split mid-section, a HyDE query for "cache failover
mitigation" might retrieve half a step with no context.

### What semantic chunking gives HyDE

With semantic chunking, each chunk is a coherent unit of meaning. The cache check
step is its own chunk. The pool exhaustion mitigation is its own chunk. The escalation
path is its own chunk. When HyDE generates a targeted query like
"Redis cache failover cluster_state ok hit ratio recovery", it retrieves exactly
the cache-specific chunk — nothing more, nothing less.

```
HyDE query: "Redis cache failover cluster_state ok hit ratio recovery"

With section-based chunks →
  Returns: "3. Diagnostic Steps" (large section, cache is buried inside it,
  engineer has to find it manually)

With semantic chunks →
  Returns: "Confirm failover completed: redis-cli cluster info should show
  cluster_state:ok. If not ok, page #infra-oncall. If ok, cache is warming
  up — self-resolves in 10-15 minutes, no action needed"
  (exactly the right content, immediately citable)
```

Semantic chunking makes each HyDE query retrieval precise. Section-based chunking
forces the LLM to excavate the right content from a larger block.

### The compounding effect

HyDE + semantic chunking compound:

- HyDE generates N targeted queries, each aimed at a specific diagnostic angle
- Semantic chunking ensures each angle has its own coherent chunk
- Every HyDE query retrieves exactly the right chunk for its angle
- The union of retrieved chunks covers the full diagnostic picture with no noise

If either piece is poor quality, the whole system degrades:
- Good HyDE + poor chunks = right area retrieved but hard to cite
- Good chunks + no HyDE = coherent chunks but wrong ones retrieved
- Good HyDE + good chunks = precise, complete, citable triage coverage

---

## The One Genuine Limitation

If the enterprise has service-specific or proprietary failure modes that are not
common knowledge, the LLM may not generate queries for them.

**Example:** A proprietary message queue that fails in a specific way unique to
that organisation. The LLM has never seen it. It will not generate a query for it.

**Mitigation:** Include a service context block in the HyDE expansion prompt:

```
"checkout-api uses Postgres via PgBouncer for connection pooling and Redis
for caching. It calls payment-gateway-svc, inventory-svc, and
fraud-scoring-svc synchronously. Known failure modes: connection pool
exhaustion, Redis cache node failover. Generate triage queries covering
all failure modes for this symptom."
```

This makes the expansion service-aware. The service context is configured once
per service and referenced in the system prompt — not written per query. For a
new enterprise, this context comes from their architecture documentation, which
they provide during onboarding.

---

## Retrieval Quality Comparison

| Query | Naive retrieval gets | HyDE + semantic chunking gets |
|---|---|---|
| "p99 latency climbing gradually" | Symptom descriptions only | Pool check + cache check + downstream check + mitigation + escalation |
| "error rate high latency normal" | Error rate descriptions | Full 4xx/5xx decision tree + failure family framing + escalation |
| "checkout is down" | Almost nothing actionable | All failure modes + both alert paths + SEV1 escalation |
| "connection pool exhaustion" | Pool description only | Pool check + cache rule-out + mitigation steps + contributing factors |

---

## Summary of Why This Combination

| Problem | Solved by | How |
|---|---|---|
| Unknown enterprise document formats | Semantic chunking | Splits on meaning shifts not visual structure — works for any format |
| Query vocabulary ≠ triage vocabulary | HyDE multi-query expansion | LLM generates queries in document vocabulary, not symptom vocabulary |
| One symptom, multiple root causes | HyDE | One query per hypothesis — all diagnostic paths retrieved |
| Retrieved chunks hard to cite | Semantic chunking | Each chunk is a coherent self-contained idea |
| Mid-sentence splits on large sections | Recursive split safety net | Secondary pass on any chunk exceeding embedding model token limit |
| Proprietary failure modes | Service context in HyDE prompt | LLM uses service architecture to generate targeted queries |

Neither semantic chunking nor HyDE is sufficient alone. Together they address
the full pipeline from document ingestion to diagnostic retrieval — for any
enterprise, any document format, any symptom vocabulary.
