# RAG Chunking and Retrieval Design

## Why This Document Exists

As we built the ingestion pipeline for IncidentPilot, we encountered two fundamental
problems that cannot be solved by simple fixes. This document records what those
problems are, what options we considered for each, why most options fall short, and
which strategy we have chosen and why. Anyone joining the project or extending it to
a new enterprise should read this before touching the ingestion or retrieval code.

---

## The Two Problems

### Problem 1 — Chunking: We Cannot Know the Document Format in Advance

When IncidentPilot is deployed at a new enterprise, their runbooks and postmortems
will arrive in whatever format that organisation has historically used. We have no
control over this. We cannot predict it. And we cannot afford to write new code every
time a new format appears.

To illustrate how different real enterprise document formats actually are, here are
three formats from our own corpus — all covering the same service, all about the
same incident type, all written by competent engineers:

**Format 1 — ISTM Known Errors (table-driven)**
```
Overview
┌─────────────────┬────────────────────────────────────┐
│ Name            │ Postgres Connection Pool Exhaustion │
│ Status          │ ACTIVE                              │
│ Owner           │ payments-platform                   │
│ Summary         │ p99 latency climbs gradually...     │
└─────────────────┴────────────────────────────────────┘

Error details
┌─────────────────┬────────────────────────────────────┐
│ Description     │ Each pod holds a fixed pool...      │
│ Workaround      │ Increase PgBouncer pool size...     │
│ Solution        │ No permanent fix shipped yet...     │
└─────────────────┴────────────────────────────────────┘
```
No headings in the traditional sense. Structure lives in table row labels. The
"sections" are table cells. A heading detector would see "Name", "Status", "Owner"
as headings and split on them — producing meaningless single-field chunks.

**Format 2 — Formal Runbook Template (numbered sections)**
```
1. Overview
   1.1 Purpose
   1.2 Scope
   1.3 Target Audience
   1.4 Prerequisites

2. Triggers
   2.1 When to Use This Runbook
   2.2 Related SLOs

3. Diagnostic Steps
   Step 1: Determine which alert fired
   Step 2: Latency path
   Step 3: Error-rate path

4. Resolution Actions
   4.1 Common Fixes
   4.2 Service Restart Procedure

5. Rollback Procedure
6. Escalation Matrix
7. Communication Templates
8. Post-Incident Actions
9. Reference Information
10. Version History
```
Structured with numbered top-level sections and numbered subsections. Ten sections,
some with tables inside them. Splitting at subsections (1.1, 1.2) would orphan
fragments with no surrounding context. Splitting only at top-level (1., 2., 3.)
produces large but coherent chunks.

**Format 3 — Scoutflo Style (prose with bold headers)**
```
Checkout API Connection Pool Exhaustion

Meaning
Checkout API requests slow down and eventually fail because the service's
Postgres connection pool is exhausted...

Impact
Checkout requests experience rising latency and outright failures (503s)...

Playbook
1. Confirm which alert fired...
2. Check the connection pool dashboard...
3. Check the cache hit ratio panel...
4. Check the downstream dependency latency panel...
5. Check application logs...
6. Check the recent deploy timeline...
7. Check current PgBouncer pool state...
8. If headroom exists, increase the pool size...

Diagnosis
1. If active_connections is pinned at max_connections and cache_hit_ratio
   is normal — this is confirmed connection pool exhaustion...
```
No section numbers. Four bold headers. Eight numbered steps inside Playbook that
cross-reference each other ("per step 7", "from step 2"). If Playbook splits mid-list,
the engineer gets step 1-4 in one chunk and step 5-8 in another. Step 8 references
step 7 but they are now in different chunks — context is broken.

**And this is only three formats.** Real enterprise corpora include:
- Confluence wiki exports (nested headings, inline comments, sidebar notes)
- ServiceNow ticket templates (form fields, resolution notes, CI references)
- Notion page exports (toggle blocks, callout boxes, database embeds)
- PagerDuty runbooks (trigger conditions, response plays, subscriber lists)
- Internal Word/PDF documents (inconsistent styles, mixed numbered and bulleted lists)
- Plain text files written informally with no consistent structure at all
- Markdown from different teams following different conventions (# vs ## vs bold)
- Multilingual documents where English heading detection fails entirely

No two organisations have the same format. Even within one organisation, different
teams use different tools and conventions. The problem is not "handle three formats" —
it is "handle any format you have never seen before, automatically, without a
developer writing code for it."

---

### Problem 2 — Retrieval: Semantic Similarity Does Not Equal Diagnostic Relevance

Even if chunking is done perfectly, the retrieval step has a fundamental gap that
chunking alone cannot fix.

Vector store retrieval works by semantic similarity: given a query, return the chunks
whose content is most semantically similar to the query text. This works well when the
query vocabulary closely matches the document vocabulary. It breaks down when the
query names a symptom but the relevant content describes diagnostic actions.

**Why the query and the relevant content use different vocabulary:**

When an SRE is paged at 2am, they describe what they observe — a symptom. The symptom
vocabulary is different from the triage vocabulary:

- The engineer says: **"high latency on checkout-api"**
- The runbook says: **"Check the connection pool dashboard: active_connections vs max_connections"**
- These two phrases share almost no vocabulary. Semantically they are distant.
- But this chunk is the single most important thing to retrieve.

- The engineer says: **"connection pool exhaustion"**
- The runbook says: **"Check the cache hit ratio — if it dropped sharply, go to Known Issue #2"**
- This chunk is also critical — it rules out the cache as the cause — but it barely
  mentions connection pool exhaustion. A semantic search for "connection pool exhaustion"
  will not retrieve it.

**Concrete example of what gets retrieved vs what should be retrieved:**

Engineer query: "high latency on checkout-api"

| Chunk | Semantically similar to query? | Diagnostically necessary? |
|---|---|---|
| "p99 latency climbs gradually over 10-20 minutes" | YES — mentions latency | YES |
| "check active_connections vs max_connections" | NO — no latency mention | YES — first triage step |
| "check cache hit ratio panel" | NO — no latency mention | YES — must rule out cache |
| "check downstream dependency latency panel" | PARTIAL | YES — must rule out upstream |
| "PgBouncer pool size mitigation" | NO | YES — the fix |
| "escalation path: page team lead after 15 min" | NO | YES — if unresolved |

A semantic search for "high latency" retrieves the first chunk and misses most of the
rest. The engineer gets a description of what connection pool exhaustion looks like
but none of the steps to confirm it, rule out alternatives, or fix it.

Engineer query: "connection pool exhaustion"

| Chunk | Semantically similar? | Diagnostically necessary? |
|---|---|---|
| "Known Issue #1: Postgres connection pool exhaustion" | YES | YES |
| "check cache hit ratio — rule out cache failover" | NO | YES — required to confirm CPE |
| "check downstream dependency latency panel" | NO | YES — required to rule out upstream |
| "increase PgBouncer pool size" | PARTIAL | YES — the mitigation |
| "escalation path" | NO | MAYBE — if mitigation fails |

Again, only the first chunk is reliably retrieved. The engineer gets the description
of the problem but not the full diagnostic path to confirm and address it.

**The core reason this happens:**

Runbooks are written as diagnostic flows. Each step eliminates a hypothesis or points
to the next action. The steps are semantically diverse by design — checking the
connection pool, checking the cache, checking downstream dependencies, and escalating
are all different topics. They split into separate chunks. None of them individually
matches the vocabulary of the symptom the engineer describes.

No chunking strategy fixes this. You can chunk perfectly and still retrieve the wrong
chunks because the retrieval mechanism itself is the problem.

---

## Options for Problem 1 — Chunking Strategy

### Option A — Section-Based: Format-Specific Functions

Write a dedicated chunking function for each document format encountered.
Maintain a registry mapping format names to functions.

**Pros:**
- Chunks align precisely with document intent — a "Known Issue" section stays intact
- Predictable, easy to debug — you can inspect exactly what each function does
- High chunk quality for known formats

**Cons:**
- Every new enterprise format requires a developer to read the documents, understand
  the structure, write a function, and deploy new code
- Fails silently on unknown formats — falls back to a single giant chunk
- Not reusable across clients — the functions encode client-specific knowledge
- Scales to zero — each new format is a fresh development task

**Why it is not viable for enterprise deployment:**
Imagine deploying at a bank with 12 teams using 7 different runbook formats. You need
7 functions before you can ingest a single document. Then a new team joins with their
own format — another function, another deployment. The system is never actually
finished. It is also a data privacy risk: a developer must read confidential
operational runbooks to understand their structure.

---

### Option B — Section-Based: Statistical Structure Detection

Analyse the raw text mathematically — line length distribution, blank line frequency,
capitalisation ratio, numeric prefix patterns. Derive a split strategy from these
signals without knowing the format in advance.

**Pros:**
- No code changes per enterprise — derives structure from the document itself
- Fully offline and deterministic — same document always produces the same result
- Fast — pure string analysis, no external calls

**Cons:**
- A table cell reading "Owner" and a section heading reading "Owner" are
  indistinguishable statistically — both are short, capitalised, surrounded by content
- Threshold sensitivity — what line length counts as a heading? Changes per language,
  per organisation style, per document density
- Fails on dense prose documents that have no visual structure signals at all
- German compound words make headings look like body text; CJK text has no capitalisation

**Why it is not viable:**
Statistical analysis detects visual patterns, not meaning. Runbook formats often
deliberately use table rows, numbered lists, or inline bold text as structure —
none of which produce the kind of clear visual signal statistics can reliably detect.
The approach requires constant threshold tuning and still misclassifies regularly.

---

### Option C — Section-Based: LLM Derives Split Strategy at Registration Time

Run a one-time registration phase where the LLM reads each new document, identifies
its structure, and returns a split pattern. Store the result in a registry JSON file.
Use stored patterns for all subsequent ingestion runs.

**Pros:**
- Handles any format including exotic ones — the LLM reads documents like a human
- No code changes per enterprise
- LLM cost is bounded — called once per registration, not per ingestion run
- Registry is human-readable — a developer can inspect and correct patterns manually

**Cons:**
- Non-deterministic — the same document can return a different split pattern on
  different days depending on LLM sampling
- Network dependency during onboarding — fails in air-gapped or highly restricted
  environments
- If the LLM picks a bad pattern, every document of that type chunks incorrectly
  until someone notices and re-registers
- Adds an extra operational step — registration must run before ingestion works

---

### Option D — Semantic Chunking

Embed the document text sentence by sentence or paragraph by paragraph. Compare each
unit to the previous one. When semantic similarity drops sharply, the topic has
shifted — close the current chunk and start a new one. No knowledge of document
format required.

**Pros:**
- Completely format-agnostic — works for ISTM tables, formal templates, prose,
  plain text, PDFs, Confluence exports, any language, without any configuration
- Zero code changes per enterprise — drop documents in, run ingestion
- Chunks align with meaning not visual structure — a chunk is always a coherent idea
- The same model we already use (all-MiniLM-L6-v2) does the splitting — no new
  dependency
- Handles the Scoutflo preamble problem naturally — the title sentence is semantically
  close to the Meaning section so they stay in the same chunk

**Cons:**
- Triage steps that are semantically distinct get split even though they are
  diagnostically related. "Check the connection pool" and "check the cache hit ratio"
  are different topics — they split. But an engineer triaging high latency needs both
- Threshold sensitivity — how large a semantic drop defines a boundary? Needs tuning
- Computationally heavier than regex — embedding every sentence takes time on large
  corpora (acceptable at document scale, not at Wikipedia scale)
- PDF text extraction can lose paragraph boundaries — wall-of-text extraction makes
  paragraph-level chunking unreliable

**The key insight:**
Semantic chunking solves Problem 1 completely. But it does not solve Problem 2.
Even perfectly coherent semantic chunks fail retrieval when the query vocabulary
does not match the chunk vocabulary. That is Problem 2's domain.

---

### Option E — Recursive Character Splitting

Ignore structure entirely. Split every document at fixed character or token boundaries
with overlap between adjacent chunks.

**Pros:**
- Simplest possible implementation — one function, zero format logic
- Works on any document, any format, any language
- Zero configuration or registration step

**Cons:**
- Splits mid-sentence, mid-step, mid-table — an engineer gets half an instruction
  with no context for the other half
- The overlap helps but does not prevent loss of a critical sentence that falls
  exactly at a boundary
- Retrieved chunks start and end at arbitrary points — the LLM has to mentally
  trim noise before it can cite anything
- Retrieval quality is significantly lower than any structure-aware approach

---

## Options for Problem 2 — Retrieval Strategy

### Option A — Naive Semantic Search (current approach)

Embed the query, find the k most similar chunks, return them.

**Pros:** Simple, fast, no extra LLM call.

**Cons:** Returns what is similar to the query, not what is diagnostically relevant.
"High latency" retrieves latency descriptions but not triage procedures. The retrieval
gap is fundamental and unfixable within this approach.

---

### Option B — Keyword Expansion (manual)

Pre-define a mapping from common symptom terms to related search terms.
"High latency" → also search for "connection pool", "cache hit ratio", "downstream
dependency".

**Pros:** No extra LLM call at query time. Deterministic.

**Cons:**
- The mapping must be written manually per service, per symptom — same problem as
  Option A for chunking: someone has to pre-teach every term
- Misses synonyms and novel phrasings
- Does not generalise to a new enterprise without a new mapping

--- i have high latyency -> high latency coukd be due to connectuoin echaustion, downstream deoencency issues 

### Option C — HyDE / Multi-Query Retrieval

Before hitting the vector store, send the engineer's query to the LLM with a prompt:
"This engineer is triaging an incident. Expand this query into 4-5 search queries
that would retrieve all the relevant triage steps, known causes, diagnostic
procedures, and mitigation actions for this symptom."

Run all generated queries against the vector store independently. Merge and deduplicate
results. Pass the full union to the LLM for final answer synthesis.

**Pros:**
- Closes the retrieval gap — the LLM generates queries in the vocabulary of the
  document, not the vocabulary of the symptom
- Works for any service, any enterprise, any symptom — no pre-defined mappings
- One extra LLM call per query — small cost, significant recall improvement
- "High latency" generates queries for pool checks, cache checks, downstream checks,
  escalation paths — all the diagnostically relevant chunks are now retrieved

**Cons:**
- One additional LLM call per engineer query — adds ~1-2 seconds latency
- Generated queries can occasionally miss a relevant angle — mitigated by using 4-5
  queries rather than 1
- Non-deterministic — the expanded queries vary slightly between runs

---

### Option D — Parent-Child Chunk Retrieval

Store two levels of chunks: small child chunks for precise retrieval and large parent
chunks that contain more context. When a child chunk matches the query, return its
parent instead of the child alone.

**Pros:**
- Retrieved content is richer and more coherent
- Diagnostic steps that split at the child level stay together at the parent level

**Cons:**
- More complex storage — two sets of chunks, a parent-child index
- Does not solve the vocabulary mismatch — the child chunk still needs to match the
  query to trigger the parent retrieval
- Works best when combined with HyDE, not as a standalone fix

---

## Why Section-Based Chunking Cannot Work Without Knowing the Format

This deserves a direct, concrete answer since it drives the entire design decision.

Section-based chunking requires knowing:
1. What constitutes a section boundary in this document
2. Whether to split at `##` headers, numbered sections, bold text, blank lines, or
   table row labels
3. Whether subsections should be kept with their parent or split independently
4. Whether preamble content should merge into the first section or stand alone

None of this can be determined without either reading the document (human) or having
a format-specific rule (code). There is no generic answer because the same visual
signal means different things in different formats:

| Signal | In Format A means | In Format B means |
|---|---|---|
| Short line surrounded by blank lines | Section heading | Warning label |
| Bold text | Section heading | Emphasis within a paragraph |
| Numbered line | Section number | Ordered list step |
| Table row label | Metadata field | Section heading |
| ALL CAPS line | Section heading | Alert name or severity level |

A rule that correctly identifies headings in one format will misfire in another. There
is no universal rule because the formats themselves are not universal. The only
approaches that avoid this are: let a human write format-specific code (not scalable),
let an LLM read and interpret the document (Option C above — adds network dependency
and non-determinism), or abandon section detection entirely and use meaning instead
(semantic chunking — Option D above).

---

## Chosen Strategy

### Chunking: Semantic Chunking (Option D) with Recursive Split Safety Net

**Why:**
- Solves the enterprise format problem completely — no format-specific code ever needed
- Works for any document from any organisation in any format in any language
- Chunks are coherent units of meaning — the LLM can cite them cleanly
- Uses the same embedding model already in the stack — no new dependency

**Implementation:**
- Primary: paragraph-level semantic similarity using all-MiniLM-L6-v2
- Fallback for dense prose (no paragraph boundaries): sentence-level semantic similarity
- Safety net: RecursiveCharacterTextSplitter for any chunk exceeding the embedding
  model's token limit (~1000 characters)
- Each chunk is tagged with source filename and a generated topic label as metadata

---

### Retrieval: HyDE Multi-Query Expansion (Option C)

**Why:**
- Closes the gap between symptom vocabulary and triage vocabulary
- An engineer typing "high latency" now retrieves pool checks, cache checks,
  downstream dependency checks, and escalation paths — not just chunks that mention
  latency
- Works generically for any service, any enterprise, without pre-defined mappings
- One extra LLM call per query is a small cost for a large improvement in diagnostic
  coverage

**Implementation:**
- Before hitting ChromaDB, send the engineer's query to the LLM
- LLM generates 4-5 targeted search queries covering different diagnostic angles
- All queries run against ChromaDB independently
- Results are merged and deduplicated
- The union of results is passed to the LLM for final triage synthesis

---

### Why This Combination

Semantic chunking and HyDE solve different problems and do not overlap:

| Problem | Solved by |
|---|---|
| Unknown enterprise document formats | Semantic chunking |
| Query vocabulary ≠ triage vocabulary | HyDE multi-query expansion |
| Mid-sentence splits on large sections | Recursive character split safety net |
| Retrieved chunks too short to cite | Paragraph-level (not sentence-level) primary split |

Neither is sufficient alone. Semantic chunking without HyDE still fails to retrieve
all diagnostically relevant chunks. HyDE with poor quality chunks (from recursive
character splitting) still produces noisy, uncitable retrieval results. Together they
address the full problem end to end.

---

## What This Means in Practice

**For a new enterprise onboarding:**
1. Drop their runbook files into `synthetic-data/real-runbooks/`
2. Run `python src/ingestion.py`
3. Done — no code changes, no registration step, no format analysis

**For an engineer querying during an incident:**
1. Type the symptom as observed: "high latency on checkout-api"
2. HyDE expands to 4-5 targeted queries
3. All relevant triage chunks are retrieved — pool checks, cache checks, escalation
4. LLM synthesises a complete triage summary citing every source

**For future maintainers:**
There is no format-specific code to maintain. Adding support for a new document type
from a new enterprise requires zero code changes — only running ingestion on the
new files.
