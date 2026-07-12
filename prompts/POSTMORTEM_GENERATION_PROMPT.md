# Prompt: Generate an IncidentPilot postmortem

Use this prompt (paste as-is, filling in bracketed details) to generate a new
postmortem consistent with the two already built (`2026-05-checkout-outage.md`,
`2026-03-checkout-outage-cache.md`). It encodes the corrections made while
building those two, so a new one doesn't reintroduce the same mistakes.

---

## The prompt

Generate a single markdown postmortem file for a resolved incident affecting
`[SERVICE_NAME]`, caused by `[ROOT_CAUSE_CATEGORY]` (e.g. "connection pool
exhaustion," "cache node failover," "a bad request-schema deploy"). This will
be chunked and embedded into a RAG vector store — follow this structure
exactly, do not add sections beyond what's listed.

### Title
Title by **symptom/impact, not root cause** — e.g. "Postmortem: [Service]
Partial Outage — [Date]," not "Postmortem: [Root Cause] Incident." A
postmortem's title should reflect what was observable when the incident was
declared, before the cause was known — even though you (the generator) know
the cause already, the title shouldn't encode it. This also matters
practically: multiple incidents can share the same customer-visible symptom
("partial outage") with different causes, and titling by cause makes it too
easy to shortcut retrieval straight to one assumed cause.

### Frontmatter
```yaml
---
title: "Postmortem: [Service] Partial Outage — [Date]"
service: [SERVICE_NAME]
incident_id: [INC-XXXX, unique]
severity: [SEV1/SEV2/etc]
tags: [postmortem, [SERVICE_NAME], [ROOT_CAUSE_CATEGORY as an exact multi-word phrase]]
date: [ISO date, must be in the past relative to today]
duration_minutes: [integer]
customer_impact: "[one-line, concrete — a percentage/count, not 'some users affected']"
related_runbooks: ["[exact filename of the current runbook, verify it matches what's actually in runbooks/]"]
related_github_issues: ["[org/repo#N, ...]"]
---
```
The `tags` field must include the root cause category as an **exact phrase**
matching how it's referred to elsewhere (e.g. if the runbook calls this
"Known Issue #1: Postgres connection pool exhaustion," the tag should contain
that same phrase, not a paraphrase or hyphenated shorthand).

### Section: Summary
2-4 sentences: what happened, the mechanism in plain language, how long it
lasted, how it was resolved. This is the only place a reader should need to
read to get the gist.

### Section: Impact
Concrete, numeric where possible — percentage of requests/users affected,
estimated count, ticket volume, whether data loss or double-processing
occurred. Not vague ("some degradation").

### Section: Timeline (UTC)
Chronological, timestamped bullets from first cause to full resolution.
Include: when the triggering event happened, when an alert fired, any false
leads pursued and how long they cost, when the real cause was confirmed, when
a fix was decided and applied, when things returned to baseline.

### Section: Root cause
State the root cause **explicitly and early**, using the same exact phrase
from the tags — e.g. "This was a **connection pool exhaustion** incident."
Unlike a runbook (which must stay cause-agnostic across many possible
triggers), a postmortem is documenting one specific, already-investigated
incident — it should commit to the actual, specific, confirmed cause here,
not hedge with "possibly" unless the cause was genuinely never conclusively
identified. Explain the mechanism (why this cause produced this symptom),
not just name it.

### Section: Resolution
A dedicated section, separate from the Timeline — state plainly: what fix was
applied, and its type (`rollback` / `config mitigation` / `self-resolved,
no manual action` / `forward-fix deploy`). This needs to be citable on its
own, not something a reader has to reconstruct from timeline entries.

### Section: Contributing factors
Systemic gaps that let this happen or made it worse — distinct from root
cause. Root cause is "what broke"; contributing factors are "why the system
let it break" (e.g. no test at realistic concurrency, no direct alert on the
underlying resource, no backoff on retries).

### Section: What went well
1-3 bullets. Genuine, specific — not filler. If a documented runbook step
worked correctly, say so and name it.

### Section: Action items
A markdown table, **1-2 rows maximum.** Each row's `Status` and any claim it
makes must be independently verifiable against the rest of the corpus before
you include it:
- If a row claims an alert was added, that alert must actually be reflected
  as a trigger in the runbook's "when to use this runbook" section — if it
  isn't, either add it there too or don't claim it here.
- If a row references a fix already reflected in a code doc (e.g. a
  load-testing requirement), the status and GitHub issue number must match
  what that code doc says exactly.
- Do not describe a fix that doesn't match what was actually built elsewhere
  in the corpus (e.g. don't claim "checks now run in parallel" if the
  runbook documents them running sequentially).
- Prefer fewer, verified rows over more, unverified ones.

### Explicitly excluded — do not include
- **No "Key excerpts for future pattern-matching" or any section written to
  make retrieval/memory-matching easier.** A postmortem documents what
  happened for its own sake — it is not authored for the agent's
  convenience. If a future system needs a matchable symptom summary, that's
  a separate artifact's job (memory), populated from real sessions, not
  something to pre-bake into this document.
- **No raw metrics tables or dumps.** Describe the shape of the metrics in
  prose ("p99 climbed gradually over 10-20 minutes") — actual numbers belong
  in the separate metrics dataset, not duplicated here.
- **No hedged-away certainty.** If the cause is known, state it as known.
  Don't soften a confirmed cause into a "likely suspect" just to sound safe.

### Formatting requirement
Use `##` for every major section, no `###` needed. The file will be chunked
on markdown headers — each `##` section must be understandable on its own
without relying on a different section's context, EXCEPT that Timeline may
assume Summary has already established the basic facts.
