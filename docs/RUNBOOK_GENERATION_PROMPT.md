# Prompt: Generate an IncidentPilot runbook

Use this prompt (paste as-is, filling in the bracketed service details) to
regenerate a runbook consistent with the one built for checkout-api. It
encodes every scoping decision made while designing that first runbook, so a
new one for a different service stays structurally consistent and doesn't
re-introduce mistakes that were deliberately corrected.

---

## The prompt

Generate a single markdown runbook file for the service `[SERVICE_NAME]`,
intended to be chunked and embedded into a RAG vector store for an
incident-response copilot. Follow this structure and these constraints
exactly — do not add sections beyond what's listed.

### Frontmatter
Minimal only:
```yaml
---
service: [SERVICE_NAME]
doc_type: runbook
---
```
Do not add severity_scope, related_postmortems, last_reviewed, or other
metadata fields — keep this to service name and doc type only.

### Section: "When to use this runbook"
List the specific alert names that should trigger opening this runbook (e.g.
`[SERVICE_NAME]-p99-latency-high`, `[SERVICE_NAME]-error-rate-high`). State
plainly that which alert fired determines which triage path applies below —
do not present a single undifferentiated checklist.

### Section: "Triage: if `p99-latency-high` fired"
An ordered, numbered sequence of read-only diagnostic checks, each ending in
either "go to Known Issue #N" or "this points outside this service, escalate
to X". Include, in this order:
1. Any resource-exhaustion-type internal check relevant to this service
   (connection pools, memory, disk, thread pools — whatever applies).
2. Any caching-layer check relevant to this service, if applicable.
3. A downstream-dependency latency check — explicitly explain that a slow
   call to a dependency shows up in this service's own latency even though
   this service itself is healthy, and that if a dependency's latency
   spiked at the same time, the fix is to escalate to that dependency's
   on-call, not to keep investigating this service — there is no local fix
   for someone else's slow service.
Do not assume or assert a single root cause (e.g. "probably a deploy") in
this section — frame each check as an independent lead to confirm or rule
out, not a hypothesis to confirm.

### Section: "Triage: if `error-rate-high` fired without elevated p99 latency"
Explain that fast failures point at the request/contract, not resource
contention, and are a different failure family from the latency-driven known
issues. Then:
1. Break down error rate by status code class (4xx vs 5xx).
2. For 4xx: cover only 400 (payload doesn't match expected schema — point to
   a separate API-schema code doc by name, do not restate the schema here)
   and 401 (auth token issue) unless told to cover more codes. Do not
   conflate all 4xx codes as "auth" — they have distinct causes.
3. For 5xx: point at application error logs / stack traces, and note that a
   correlation with a recent deploy is a meaningful signal here (unlike the
   latency branch), since a fast-failing bug is what a bad code change
   looks like.
State explicitly if this branch has diagnostic steps only and no documented
mitigation yet — don't imply more coverage than actually exists.

### Section: "Known Issue #N" (one per recurring symptom pattern)
For each known failure pattern this service has actually experienced or is
expected to recur, include:
- **Symptom signature** — the specific, distinguishing shape of the metrics
  and/or log lines (e.g. "gradual climb over 10-20 min" vs "sudden step
  change" — these distinguish different known issues from each other).
- **Mechanism** — a plain explanation of *why* this failure produces that
  symptom shape, in terms of the actual system behavior.
- **Mitigation** — concrete, executable, read-only-or-reversible steps that
  do NOT require a deploy or rollback (e.g. increasing a connection pool's
  size via a config reload rather than a deploy, confirming a cluster's
  health state). Prefer a mitigation that addresses the actual bottleneck
  (e.g. there isn't enough of a finite resource available) over one that
  just resets current state (e.g. restarting a component to clear whatever's
  stuck) — the former is more likely to genuinely help; the latter may or
  may not, depending on whether the root cause recurs immediately after. If
  the mitigation raises a ceiling (like a pool size), explicitly check and
  state whether there's headroom in whatever it's bounded by (e.g. don't
  raise a connection pool past what the database's own connection ceiling
  can support) — otherwise the mitigation just shifts the bottleneck rather
  than relieving it. If contributing causes vary (e.g. could be a deploy,
  could be organic traffic growth, could be an autoscaling mismatch), say so
  explicitly rather than asserting one cause — checking the deploy timeline
  is one independent lead among several, not the presumed answer.

### Section: "Escalation path"
A simple time-boxed L1/L2/L3 structure: who owns triage first, when to page
a team lead, when to declare a higher severity and page an incident
commander.

### Section: "Resolution criteria"
Concrete, measurable thresholds for "this is resolved" (e.g. specific
latency/error-rate numbers sustained for a specific duration) — not a vague
"things look better."

### Explicitly excluded from this runbook — do not include
- **Any rollback, deploy, or hotfix instructions or policy.** This belongs
  in a separate guardrail layer, not the runbook, because guardrail
  behavior must be unconditional and must not depend on RAG retrieval
  succeeding. Do not write a "if asked to roll back" section here.
- **API request/response schemas, field definitions, or service capability
  catalogs.** These belong in a separate code-docs file — reference it by
  name if relevant (e.g. "see code-docs/[SERVICE_NAME]-request-schema.md"),
  never restate schema content inline.
- **Deployment pipeline mechanics, deploy ordering across services, CI/CD
  commands.** Out of scope entirely for a triage runbook.

### Formatting requirement
Use `##` for every major section and `###` only if a Known Issue needs
sub-structure — the file will be chunked on markdown headers, so each `##`
section must be self-contained enough to be understood as a standalone
retrieved chunk (don't rely on context from a different `##` section to make
a chunk make sense).
