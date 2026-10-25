# BEIR public-dataset eval

What this suite is, what it measures, what it deliberately does not, and how to read its results.

## Why it exists

incident-pilot's own corpus is 8 documents (5 runbooks + 3 postmortems) chunked into 60 vectors,
with queries we wrote ourselves. That is too small and too self-labelled to have an independently
known correct score — so when retrieval looks weak, there is no way to tell **"the approach is
weak"** apart from **"my code has a bug"**.

Public BEIR datasets have *published* reference scores for the exact embedding model this project
uses (`all-MiniLM-L6-v2`). Running our own embed-and-search code against one gives an external,
apples-to-apples wiring check **before** trusting any number produced against the domain corpus.

This suite is a **sanity check on one component**, not a measure of product quality.

## Where it sits in the RAG pipeline

RAG is Retrieval-**Augmented Generation**, so the pipeline has a retrieval half and a generation
half. This suite touches one sub-step of the retrieval half:

| stage | covered here? | notes |
|---|---|---|
| **Retrieval** | | |
| 1. Chunking | ❌ **impossible** | BEIR corpora arrive pre-segmented by the dataset authors, so there is no way to evaluate `SemanticChunker` or chunk-size tuning against public data at all — only against our own document structure |
| 2. Embedding | ✅ | the model turning text into 384-dim vectors |
| 3. Indexing | ✅ | Chroma's HNSW graph built over those vectors |
| 4. Search | ✅ | `similarity_search_with_score()`, L2 distance |
| 5. Query expansion (HyDE / HyPE) | ❌ | evaluated by `eval/benchmark/` against the real corpus |
| **Generation** | | |
| 6. Answer generation, groundedness, citations | ❌ | the LLM is never invoked here |

**Not part of RAG, and not covered:** MCP tool-calling decisions (`query_metrics` / `query_logs`),
guardrails, contradiction detection. Those are agent capabilities running *alongside* RAG, not
stages of it — RAG retrieval is always-on and pre-computed, telemetry is agent-decided.

So: **stages 2–4 only.** The LLM is never called; no answer is ever generated.

## What kind of eval this is

- **Component-level, not end-to-end.** No generation, no agent, no tools.
- **Reference-based.** Scored against published figures for this exact model, so there is an
  externally-known-correct answer to land near.
- **Human-labelled ground truth.** BEIR's own qrels, not anything we wrote.
- **Fully deterministic.** `pytrec_eval` arithmetic — no LLM-as-judge, no sampling. Reproduces to
  four decimal places on identical code and environment, so a future score change is a real signal
  rather than noise.
- **General-domain.** Scientific abstracts and finance forum posts — **not** SRE/runbook text. No
  BEIR dataset matches that domain, which is why this cannot replace the domain benchmark.

## What it measures: metrics

All at k = 1, 3, 5, 10. These are information-retrieval metrics — *did the right documents come
back, and were they near the top*:

| metric | what it asks |
|---|---|
| **NDCG@k** | Was the *ordering* right? Each hit contributes its relevance grade, discounted by `1/log2(rank+1)` so rank 1 beats rank 8, then normalised against the ideal ranking (1.0 = perfect). **The metric BEIR publishes**, hence the one used for comparison. |
| **MAP@k** | Were *all* the relevant documents found and near the top? Precision recorded at each hit, averaged. Treats relevance as binary. |
| **Recall@k** | What fraction of relevant documents made it into the top k? |
| **Precision@k** | What fraction of the returned k were relevant? |

Only **NDCG@10** has a published reference — BEIR standardised on it, so model cards report one
number per dataset. The other 23 figures in each result file are internal diagnostics with nothing
to compare against.

## What it does NOT measure

Spelled out, because the omissions matter more than the coverage:

- **Groundedness / faithfulness** — does the answer follow from the retrieved chunks
- **Answer accuracy** or **answer relevance**
- **Citation correctness** — the `[Runbook: …]` / `[Postmortem: …]` translation
  `prompts/system_prompt.md` requires
- **Hallucination rate**
- **Chunking quality** — structurally impossible here, see above
- **HyDE / HyPE** contribution
- **Tool-calling** correctness, **guardrail** reliability
- **Domain fit** — nothing here involves runbooks or incident text

None of these are measurable by this suite: it never generates an answer.

## Datasets

Both are BEIR test splits, downloaded by `download.py` and cached in `_data/` (gitignored).

| dataset | corpus | queries | why this one |
|---|---|---|---|
| **SciFact** | 5,183 scientific abstracts | 300 | the smallest commonly-used BEIR smoke test — CPU-feasible, fast, well-documented reference scores |
| **FiQA** | 57,638 finance forum posts | 648 | 11× larger and much harder; exercises the same wiring in a regime SciFact cannot reach, where ANN approximation error actually becomes visible |

Deliberately small: these are CPU-only runs on a developer machine. FiQA takes ~40 min and needs
~2 GB of free RAM (peak working set 1,605 MB).

## Results

| dataset | config | our NDCG@10 | published | deviation |
|---|---|---|---|---|
| SciFact | prod_config | 0.6451 | 0.6450 | **+0.0001** (+0.01%) |
| FiQA | prod_config | 0.3626 | 0.3690 | **−0.0064** (−1.73%) |
| SciFact | GloVe override | 0.2948 | *none* | not compared |

**Both production results match their references.** The embedding + search component is correct.

The **GloVe override** is a fluke check, not a benchmark: swapping in a deliberately weak,
non-contextual model drops NDCG@10 to 0.295, which proves the scorer is genuinely sensitive to
embedding quality rather than reporting a fixed number regardless of input. It records
`published_reference: null` on purpose — comparing GloVe against `all-MiniLM-L6-v2`'s reference
would manufacture a meaningless −54% "deviation".

### Why FiQA's deviation is expected, not a defect

**The references use exact search; we deliberately do not.** BEIR's standard dense retrieval
(`DenseRetrievalExactSearch`) builds the full query × corpus score matrix and takes `torch.topk`
over it — brute force, no index, nothing skipped. `run_beir_eval.py` instead retrieves through
Chroma's HNSW index, because that is production's real path.

`exact_search_diag.py` measured exactly what that costs, one variable at a time:

```
HNSW + L2          0.3626   (run_beir_eval.py)
A exact + L2       0.3687   ANN loss      = +0.0061
B exact + cosine   0.3687   metric effect = +0.0000
published          0.3690   residual      = +0.0003
                            ------------------------
                            total           +0.0064   = 0.3690 - 0.3626
```

The three deltas sum exactly to the gap: **95% is the approximate index, 0% is the distance metric,
5% (0.08% relative) is unexplained.** Three conclusions follow:

1. **No bug.** Exact search over our embeddings landing 0.0003 from an external reference is the
   strongest validation this harness has produced — a mis-wired qrels mapping or broken
   title/text concatenation would not coincidentally reproduce someone else's published figure.
2. **The sign was predicted in advance.** HNSW can only ever *miss* true neighbours, never beat
   them, so removing approximation could only tie or improve. A drop would have falsified the ANN
   explanation and pointed at a bug.
3. **ANN error grows with corpus size**, which is why SciFact shows none (its loss is smaller than
   the resolution of a reference quoted to three decimals) and FiQA shows 1.7%.

### Relevance to production: none at current scale

Provably, not approximately. The built store
(`synthetic-data/vectorstore/chroma.sqlite3`) records `ef_search = 100` over **60 chunks** — the
search beam is wider than the entire corpus, so every vector is examined and **retrieval is already
exact**. The 1.7% is a property of 57,638 documents and would only begin to appear past roughly 100
chunks.

`src/ingestion.py` needs no change. Its L2 default is *provably* equivalent to cosine for this
model: `all-MiniLM-L6-v2` ends in a `Normalize` module, so vectors are unit-length even though
`normalize_embeddings` is never passed (measured norms exactly 1.0), and for unit vectors
`‖q−d‖² = 2 − 2·cos`, making L2 a monotone transform of cosine — identical rankings. By the same
algebra, `src/hype_ingestion.py`'s `collection_metadata={"hnsw:space": "cosine"}` override is
harmless but inert. Usefully, that means the distance metric is **not a confound** when comparing
HyPE against the main path.

## Files

| file | role |
|---|---|
| `run_beir_eval.py` | the benchmark — production's real path (Chroma + HNSW + L2) |
| `exact_search_diag.py` | the diagnostic — brute force, to attribute a deviation |
| `references.py` | published reference scores, single source of truth |
| `download.py` | fetches a dataset into `_data/` |
| `results/*.json` | committed results; `_data/` is gitignored (external data, not generated insight) |

Two result schemas, distinguished by `eval_type`:

- **benchmark runs** (`*__prod_config__*`, `*__override__*`) — one configuration, flat
  `ndcg`/`map`/`recall`/`precision`, plus `published_reference` and `deviation_ndcg_at_10`
- **diagnostics** (`*__exact_search_attribution__*`, `eval_type: "diagnostic"`) — three
  configurations compared, metrics nested under `results.*`, plus `purpose`, `method`,
  `interpretation`, `caveats`, `provenance`

## Running it

```bash
uv sync --group test                                            # beir lives in the `eval` group
uv run python eval/beir_public/download.py scifact              # once per dataset
uv run python eval/beir_public/run_beir_eval.py scifact --k 1,3,5,10
uv run python eval/beir_public/exact_search_diag.py fiqa        # the attribution diagnostic
```

`--model <name>` overrides the embedding model for a fluke check; such runs record
`published_reference: null`.

Budget: SciFact ~5 min. FiQA ~40 min and ~2 GB free RAM — it was OOM-killed once with only
~0.9–1.2 GB free and completed unchanged, same code, once ~1 GB was freed elsewhere. ~900 MB of
peak is fixed and corpus-independent (torch ~400, MiniLM ~138, the BEIR corpus dict ~68,
Chroma/HNSW init the rest).

## Limits — what this cannot tell you

- **One model, one parameter set.** `ef_search=100, max_neighbors=16, ef_construction=100`. The
  1.7% figure does not generalise — it depends on corpus size, dimensionality and those knobs.
- **No error bars.** Every configuration is deterministic, so re-running reproduces the figures
  exactly and cannot estimate variance.
- **General-domain text.** Says nothing about whether the pool-exhaustion runbook section surfaces
  for a p99-latency query. That is `eval/benchmark/`'s job.
- **Retrieval only.** A perfect score here is compatible with an agent that ignores its retrieved
  context, fabricates, or drops citations.

## Related evals in this repo

| suite | what it covers |
|---|---|
| `eval/benchmark/` + `eval/benchmarks/` | retrieval on the **real** corpus — 19 registered suites across `no_hyde` / `hyde` / `hype` / `hyde_bm25_hybrid` × single-source and robustness query sets, scored on precision/recall/MRR. **Where HyDE/HyPE actually get evaluated.** Baselines date to Aug 1–2, so they predate the `gpt-oss-120b` and prompt changes. |
| `eval/results/grounding_benchmark_reasoning_*.json` | the closest thing to a grounding eval — captures raw traces (query, HyDE queries, chunks, tool calls, data source, timings, response) across repeats at different reasoning efforts. **Trace capture for manual comparison; there is no automated groundedness score.** |
| `tests/test_incident_pilot.py::TestGuardrailBehaviour` | guardrails, as pass/fail tests against the real Groq API rather than as metrics |

**Known gap:** groundedness and citation correctness have no automated scored eval.
`eval/benchmark/models.py`'s metric shapes were built generic so a future `answer_groundedness`
family could register alongside `rag_retrieval`, but only `rag_retrieval` exists today.
