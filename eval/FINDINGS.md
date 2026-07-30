# RAG retrieval eval — findings

Scope note: every claim below is grounded in a result actually produced by
the eval scripts in this directory or a direct inspection of retrieved
chunk content (via `similarity_search_with_score` / `IncidentPilot.retrieve`
run interactively) — nothing here is inferred or extrapolated beyond what
was measured. Where coverage is partial (e.g. HyDE only tested against
checkout-api), that's stated explicitly rather than implied to generalize.

## 1. Chunking bug found and fixed (via `eval_retrieval_single_source.py`)

Running the first single-query, top-3, no-HyDE eval (5 queries, one per
service/incident pair) surfaced a complete retrieval miss on the
payment-service pool-exhaustion query (precision/recall/RR all 0).
Investigation (documented in full in conversation, summarized here):

- Root cause: `ingestion.py`'s `SemanticChunker` correctly keeps a whole
  runbook section (symptom + mitigation + escalation) together as one
  chunk, but any such chunk exceeding `MAX_CHUNK_CHARS` (1500) got
  re-split by a plain `RecursiveCharacterTextSplitter` with no awareness
  of headers or instruction boundaries. Confirmed via direct tokenizer
  inspection: the embedding model (`all-MiniLM-L6-v2`, 256-token limit)
  would only ever see the first 256 of 542 tokens in the oversized chunk,
  and both the diagnostic phrase and the mitigation text fell entirely
  outside that window.
- Confirmed corpus-wide, not isolated: all 7 corpus documents (4 runbooks,
  3 postmortems) have at least one `SemanticChunker` output exceeding
  `MAX_CHUNK_CHARS`.
- Fix applied: `_safety_split()` now tags every split-off piece with
  `metadata["parent_content"]` (the full pre-split text). Pieces are still
  embedded/searched individually (staying within the token limit), but
  retrieval substitutes `parent_content` back in when present, so a split
  chunk still returns its complete original section. Applied in both
  `ingestion.py` (tagging) and `incident_pilot.py`'s
  `_retrieve_with_queries()` (substitution + dedup fingerprint keyed on the
  substituted content, so sibling split-pieces don't double-occupy
  `MAX_RETRIEVED_CHUNKS` slots).
- Measured effect (5-query single-source baseline, no HyDE):
  mean recall 0.80 → 1.00, MRR 0.50 → 0.70.

## 2. No-HyDE robustness eval (`rag_qrels_synthetic_robustness.py`, 72 queries, all 4 services)

Synthetic phrasing variants (service-name form × symptom phrasing mode ×
vague/misspelled wording) built to stress-test whether retrieval survives
how a real on-call engineer actually writes, not just clean technical
phrasing. Result: **mean recall 0.778, MRR 0.609** across all 72 (see
conversation for the full per-query table; not re-persisted as JSON since
this run predates the results/ directory).

13 complete failures (recall=0), all individually verified against actual
retrieved content (not inferred from query text alone):

- **Category A — "service" token collision (8 occurrences).** Natural-
  language phrasing like `"checkout service"` retrieves the *wrong*
  service's runbook entirely, confidently (low/good distance scores on the
  wrong doc). Confirmed cause: `payment-service`, `auth-service`,
  `listing-service` all literally contain the word "service" in their
  canonical names; `checkout-api` does not — so any query containing that
  word gets pulled toward the other three regardless of topic.
- **Category B — postmortem-prose competition (2 occurrences).**
  Heavily failure-themed vague language (e.g. `"seeing failuers on
  orders"`) retrieves postmortem narrative text over the correct runbook's
  procedural text. Confirmed by direct chunk inspection: the correct
  runbook doesn't appear in the top-3 at all for these queries.
- **Category C — right document, wrong specific chunk (2 occurrences).**
  The correct runbook is retrieved, but not the specific chunk containing
  the required diagnostic phrase. Confirmed by direct content inspection
  of the retrieved chunks (e.g. `auth-service-runbook.md` retrieved at
  both rank 1 and rank 2 for `"auth svc taking longer than usual to log
  in"`, but neither chunk contains `"cache node fails"` — one is a
  cross-service-check paragraph, the other is the document's opening/intro
  chunk).
- **Category D — misspelled service name loses the document entirely (1
  occurrence).** `"athu service latencyy has increased"` — the correct
  document is absent from the top-3 entirely.
- **Not a failure category, verified separately:** misspellings alone
  (`latencyy`, `resposne`, `increaed`, etc.) were well-tolerated *whenever*
  paired with a service-name form that didn't trigger Category A — typo
  tolerance itself isn't broken.
- **Anomaly checked, confirmed not a bug:** `listing-service` queries
  showing precision above the theoretical 1/3 ceiling (0.667) were
  verified via direct chunk inspection to reflect two genuinely distinct
  relevant chunks (the document's intro chunk independently references
  both Known Issues, in addition to the detailed chunk) — not a
  duplicate-counting bug.

## 3. HyDE vs. no-HyDE comparison (checkout-api only — 3 base queries / 36 tuples + the 2 original checkout-api single-source queries)

**Scope limitation, stated explicitly:** this comparison only covers
checkout-api. The other 3 services have not been run against
`IncidentPilot.retrieve()` (the HyDE pipeline) — do not generalize these
findings to auth-service, listing-service, or payment-service without
running the equivalent eval for them.

| Query set | Metric | No-HyDE | With HyDE |
|---|---|---|---|
| Single-source (2 queries) | Precision / Recall / MRR | 0.333 / 1.000 / 1.000 | 0.250 / 1.000 / 1.000 |
| Robustness (36 queries) | Precision / Recall / MRR | 0.250 / 0.667 / 0.579 | 0.178 / 0.833 / 0.616 |

Raw data: `results/checkout_single_source_no_hyde.json`,
`results/checkout_robustness_no_hyde.json` (full `RagEvalReport`, incl.
per-chunk retrieved content), `results/checkout_single_source_hyde.json`,
`results/checkout_robustness_hyde.json` (summary-level only — per-chunk
content wasn't re-captured for the HyDE runs, to avoid a second paid Groq
run purely for record-keeping).

Precision dropping under HyDE in both rows is mechanical, not a quality
loss: `MAX_RETRIEVED_CHUNKS` caps at 6 under HyDE vs. a fixed k=3 without
it, so the same count of relevant chunks becomes a smaller fraction of a
larger returned set.

Row-by-row reconciliation against the 9 checkout-specific Category-A/B
failures from section 2 (computed directly from the two JSON files, not
estimated):

- **6 fully recovered** (recall 0 → 1.0): `"checkout service connections
  maxed out maybe"`, `"checkout service orders are failing"`, `"checkout
  service requessts are failing"`, `"chckout srv seeing failuers on
  orders"`, `"checkout service users cant log in"`, `"checkout service
  usres cant log in"`.
- **2 partially recovered** (recall 0 → 0.5): `"checkout service response
  time has gone up"`, `"checkout service latenci has spiked"`.
- **1 still failing** (recall 0 → 0): `"chckout srv customers getting
  logged out randomly"`.
- **2 new regressions** (recall 1.0 → 0, previously working queries broken
  by HyDE): `"chckout srv possibly a db connection issue"`, `"checkout srv
  lot of customers cant checkout"`.

Root cause of one regression confirmed directly (not inferred): re-ran
`pilot._expand_query()` for `"chckout srv possibly a db connection issue"`
and inspected both the generated queries and the final retrieved chunks.
HyDE generated 5 sub-queries that didn't reinforce the runbook's actual
vocabulary (`"Database connection issues"`, `"network connectivity
issues"`, `"Immediate fix: restart checkout server"`, `"verify database
connectivity"`, `"on-call manager and SRE team"`) — searching all 6
(original + 5 expansions) pulled in content from two postmortems and
`listing-service-runbook.md`, leaving only one of the final 6 slots for
`checkout-api-runbook.md`, and that slot didn't land on the chunk with the
required phrase. At k=3 without HyDE, the raw query matched the correct
chunk directly; the broader-but-vaguer HyDE expansion diluted it out. This
wasn't checked for the second regression — only one was directly
diagnosed.

## 4. Categorized list of issues to look at, by priority

| Priority | Issue | Status | Scope tested |
|---|---|---|---|
| P0 | Chunk header-orphaning / mid-instruction split | Fixed | Corpus-wide (all 7 docs) |
| P0 | Duplicate-chunk precision inflation (dedup fingerprint) | Fixed | Corpus-wide |
| P1 | "service" token collision (Category A) | Partially mitigated by HyDE for checkout-api (6/8 checkout-specific occurrences fixed or improved) — **not fixed at the source**, and HyDE's mitigation is non-deterministic (see regressions below). Not yet tested for auth-service/listing-service/payment-service's own natural-language forms. | checkout-api only |
| P1 | HyDE query-expansion can regress previously-working queries | New finding this round — not a rare edge case in this sample (2 of 36, ~5.5%). No mitigation attempted yet. | checkout-api only |
| P2 | Postmortem-prose competition (Category B) | Open, unmitigated. HyDE fixed the 1 checkout-specific occurrence found, but this is a single data point, not confirmed as a general fix. | checkout-api only |
| P2 | Right-document-wrong-chunk (Category C) | Open, not yet tested against HyDE at all (both Category C occurrences were auth-service and payment-service, outside this HyDE run's scope). | Untested against HyDE |
| P2 | `fraud-scoring-svc` has no dedicated runbook | Open, deferred (unchanged from earlier finding — not touched this round) | N/A |
| — | Misspelling/typo tolerance | Verified fine both with and without HyDE, no action needed | checkout-api (no-HyDE), partially reconfirmed under HyDE |

**Explicitly not yet done, so not claimed above:** HyDE eval for
auth-service, listing-service, payment-service (36 more robustness tuples
already exist for these in `rag_qrels_synthetic_robustness.py` but haven't
been run through `IncidentPilot.retrieve()`); diagnosis of the second HyDE
regression; any fix attempt for Category A, B, or C.
