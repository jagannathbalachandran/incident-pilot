# Benchmark/baseline tracking

A promotable-baseline system for judging whether a change actually helped -- any change, in any
part of the pipeline, not just retrieval. Repeatable runs, a tracked baseline, and explicit
before/after comparison turn "does this look better?" into evidence instead of an impression.
That distinction is concretely useful here: HyDE's non-determinism made a query flip between HIT
and MISS across two consecutive runs with *nothing* changed, and a small corpus (7 documents,
36-72 synthetic queries) makes it easy to mistake noise for a real improvement or regression --
`repeats` and baseline comparison are how this system accounts for both.

## What we're benchmarking

Today: **RAG retrieval quality** -- given a query, does the right runbook/postmortem chunk come
back, and how highly does it rank. Scored the same way the existing `eval/eval_retrieval_*.py`
scripts already score it (precision/recall/reciprocal-rank against hand-labeled `QrelItem`s in
`eval/rag_qrels*.py`) -- this system doesn't reimplement scoring, it wraps it with repeatable
runs, comparison, and promotion.

This is deliberately not the only benchmark category that will ever exist here -- see
"Dimensions" below for how a future *answer correctness* or *answer groundedness* benchmark
(scoring the LLM's final cited answer, not just retrieval) slots into the same system without a
schema change.

## Dimensions

Every benchmark run is identified by four things, from broadest to narrowest:

| Dimension | What it means | Today's values |
|---|---|---|
| **category** | What's being measured -- metrics differ entirely by category | `rag_retrieval` (precision/recall/reciprocal_rank). Future: `answer_correctness`, `answer_groundedness`, with their own metric names -- metrics are stored as an open `dict[str, float]`, not hardcoded fields, so a new category needs no schema change. |
| **mode** | How the pipeline was configured for the run | `no_hyde` (raw `similarity_search_with_score`, deterministic) / `hyde` (full `IncidentPilot.retrieve()` -- HyDE expansion + search + dedupe + rank, one real Groq call per query). Extensible to e.g. `hyde_bm25_hybrid` later. |
| **suite** | A named qrel-set + scoring function, registered in `registry.py` | `single_source_queries` (5 hand-written qrels, `rag_qrels.py`, one per service) / `synthetic_robustness_queries` (36 generated phrasing-variant qrels, checkout-api only, `rag_qrels_synthetic_robustness_checkout_hyde.py` -- typos, abbreviations, vague symptom-only phrasing). Adding a suite later = one new entry in `registry.py`'s `SUITES` dict, nothing else changes. |
| **n_queries / retrieval_config** | Per-suite metadata, not a run parameter -- captured automatically | `n_queries`: how many qrels the suite has (5 or 36 today). `retrieval_config`: the actual retrieval depth used -- `{"k": 3}` for `no_hyde` (from `eval_retrieval_single_source.K`), `{"chunks_per_query": 3, "max_retrieved_chunks": 6}` for `hyde` (from `incident_pilot.CHUNKS_PER_QUERY`/`MAX_RETRIEVED_CHUNKS`). Read live from the source constants, never hardcoded here -- if those constants change later, a new run's `retrieval_config` will genuinely differ from an older baseline's, and `compare()` flags that loudly instead of silently comparing results retrieved at different depths. |

`category` and `mode` together define a **baseline lineage** -- `rag_retrieval/no_hyde` and
`rag_retrieval/hyde` are never compared against each other, since HyDE costs real LLM calls and
has a different noise profile.

`repeats` (default `1`) is a run parameter, not a suite property: each suite runs `repeats`
times, and every metric is stored both per-repeat and aggregated (`mean`/`std`/`min`/`max`)
across repeats -- `std` is what would actually reveal HyDE's run-to-run noise if you ran
`repeats=3`+. With `temperature=0` now pinned on the HyDE call (`_expand_query` in
`incident_pilot.py`), `repeats=1` is a reasonable default; `repeats` stays available for when
that's not enough (LLM serving can have tiny non-determinism even at `temperature=0`).

## Storage layout

```
eval/benchmarks/
  runs/<category>/<mode>/<timestamp>.json        -- every run ever executed, promoted or not
  baselines/<category>/<mode>/
    current_benchmark.json                       -- the ONE active baseline
    <timestamp>__<slug>.json                     -- past baselines, never deleted
```

`runs/` is a full audit trail of everything ever tried. `baselines/` only holds runs that were
explicitly promoted -- `current_benchmark.json` is a reserved filename, not a pointer; promoting
a new baseline renames whatever was there to a timestamped file (using *its own* `promoted_at` +
a slug of *its own* `description`) before writing the new one. Nothing is ever overwritten or
deleted -- `history` below reads straight off this folder.

## How to promote a baseline

```bash
# 1. Run it -- prints a report, and auto-compares against the current baseline if one exists
uv run python eval/benchmark/cli.py run --category rag_retrieval --mode hyde --repeats 1 \
    --description "what changed and why"

# 2a. If you're happy with it, promote in the same step:
uv run python eval/benchmark/cli.py run --category rag_retrieval --mode hyde --repeats 1 \
    --description "what changed and why" --promote

# 2b. ...or run first, review, and promote the most recent run afterward:
uv run python eval/benchmark/cli.py promote --category rag_retrieval --mode hyde \
    --description "what changed and why"

# Browse the full lineage (current + every past baseline, chronological):
uv run python eval/benchmark/cli.py history --category rag_retrieval --mode hyde
```

**Promotion is never automatic.** `run` always prints the comparison against the current
baseline (if one exists) and stops -- a human decides whether the new run is actually better
before `--promote` (or a separate `promote` call) is used. If no baseline exists yet for that
`(category, mode)`, the first promotion just needs explicit agreement, no prior baseline to beat.

## How comparison works

`compare(baseline, new_run)` matches records **by query text**, not position or count, per suite:

- Deltas (`baseline mean` vs. `new mean`, per metric) are computed **only over the overlapping
  query set** -- queries present in both.
- Queries in the new run but not the baseline (e.g. a suite that grew) are reported separately as
  "N new queries, not compared" -- never silently folded into the delta.
- Queries in the baseline but missing from the new run are reported the same way, in the other
  direction.
- A suite with no baseline counterpart at all is reported as "new suite, nothing to compare
  against" rather than blocking the rest of the comparison.
- If `retrieval_config` differs between baseline and new run for a suite, a
  `RETRIEVAL CONFIG CHANGED` warning prints before the deltas -- the numbers are still shown, but
  flagged as not apples-to-apples (e.g. comparing `k=3` against `k=5` would otherwise look like a
  quality change when it's really just a depth change).
- A pooled `[summary]` comparison rolls up every *compared* suite's overlapping-query deltas.

The CLI never renders a verdict ("this is better/worse") -- only the deltas. That judgment is a
human call, deliberately, since a small delta on a small corpus can be noise (see the HyDE
run-to-run flip mentioned above).

## Using this for design changes

The intended loop for any change you want evidence on -- a retrieval-pipeline change (embedding
model swap, BM25 hybrid, chunk-size tuning), a prompt change, or anything a future category
covers (answer correctness, groundedness):

1. Make sure a current baseline exists for the `(category, mode)` you're about to affect (`history`
   command, or just run once with `--promote` if none exists yet).
2. Make the code change.
3. `run` the same `(category, mode)` again. Read the printed comparison: per-suite deltas, any
   `RETRIEVAL CONFIG CHANGED` warning, and any new/missing query counts.
4. Decide, using the comparison as evidence: genuinely better -> `promote`; worse or inconclusive
   -> leave the existing baseline in place and iterate on the change instead.
5. If the change affects retrieval depth (`K`, `CHUNKS_PER_QUERY`, `MAX_RETRIEVED_CHUNKS`), expect
   the config-mismatch warning on the next run -- that's not a bug, it's telling you the last
   promoted baseline is no longer a clean depth-for-depth comparison, so weigh the delta with that
   in mind before promoting.

Repeat per mode independently (`no_hyde` and `hyde` each need their own promotion decision) --
a change can legitimately help one and hurt the other.
