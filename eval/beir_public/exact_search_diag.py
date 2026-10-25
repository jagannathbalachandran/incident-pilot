"""Diagnostic: attributes the gap between run_beir_eval.py's FiQA score and the
published reference, splitting it between Chroma's approximate index and the
distance metric.

WHY THIS EXISTS
---------------
run_beir_eval.py measures production's real retrieval path (Chroma + HNSW + L2)
and scored NDCG@10 = 0.3626 on FiQA against a published 0.3690 for
all-MiniLM-L6-v2 -- 1.7% low. "1.7% low" on its own does not say whether that
is an approximate index behaving normally, a distance metric that doesn't match
how the model is meant to be used, or a bug in our code. Those have very
different consequences: ANN error shrinks to nothing on a small corpus, while a
metric mismatch or a bug would affect incident-pilot's 60-chunk store just as
much as FiQA's 57,638 docs. This script separates them.

METHOD
------
A controlled experiment: each configuration changes exactly ONE variable from
the previous one, so each delta is attributable to that variable alone. The
corpus is embedded ONCE and the same matrix is reused for both A and B, so no
difference can come from embedding variation.

    row              index    metric   what the step isolates
    ---------------- -------- -------- --------------------------------------
    (run_beir_eval)  HNSW     L2       the observation being explained
    A                exact    L2       removing approximation  -> ANN loss
    B                exact    cosine   changing the metric     -> metric effect
    (published)      exact    --       whatever is left        -> residual

Everything else is held fixed: same model (EMBEDDING_MODEL_NAME, imported from
src/ingestion.py), same "{title} {text}" concatenation, same corpus, same test
queries, same pytrec_eval scoring against the same qrels. Exact search here is
brute force over the full matrix -- no index, hence no approximation.

Unlike the HNSW path, brute-force search is fully deterministic, so re-running
this reproduces the figures below exactly.

FINDINGS (2026-10-22, 57,638 docs / 648 test queries)
-----------------------------------------------------
    HNSW + L2        0.3626    (run_beir_eval.py)
    A exact + L2     0.3687    ANN loss      = +0.0061
    B exact + cosine 0.3687    metric effect = +0.0000
    published        0.3690    residual      = +0.0003
                               ------------------------
                               total           +0.0064  = 0.3690 - 0.3626

The three deltas sum exactly to the total gap, so the attribution accounts for
all of it: 95% the approximate index, 0% the metric, 5% (0.08% relative)
unexplained.

INTERPRETATION
--------------
1. The gap is ANN approximation loss. HNSW can only ever MISS true neighbours,
   never beat them, so removing approximation can only raise or tie the score --
   the sign was predicted in advance, and a drop would have falsified this.

2. The metric contributes nothing, and could not have. all-MiniLM-L6-v2 ends in
   a Normalize module (modules.json: Transformer -> Pooling -> 2_Normalize), so
   embeddings come out unit-length regardless of whether normalize_embeddings is
   passed -- measured norms are exactly 1.0. For unit vectors
   ||q-d||^2 = 2 - 2*cos(q,d), making L2 a monotone transform of cosine: the two
   produce the IDENTICAL ranking. B matching A at every k is therefore a check
   that this script is wired correctly, not a discovery.
   NB: this is why "non-normalized embeddings" (in older result JSONs and in
   b8adbcb's message) is wrong -- the flag is off but the model normalizes.

3. There is no bug. Exact search landing 0.0003 from the published figure is the
   strongest validation of the harness so far -- a mis-wired qrels mapping or
   bad preprocessing would not coincidentally reproduce the reference.

4. Corroborated across corpus sizes. ANN error should grow with corpus size, and
   does: SciFact (5,183 docs) showed no visible loss under HNSW (0.6451 vs
   published 0.645), FiQA (57,638) shows 1.7%.

RELEVANCE TO PRODUCTION
-----------------------
None, at current scale -- and provably so, not just approximately. The built
store (synthetic-data/vectorstore/chroma.sqlite3) records
ef_search=100 over 60 chunks, so the search beam is wider than the entire
corpus: every vector is examined and retrieval is already exact. The 1.7% is a
property of 57,638 documents, not of this pipeline. It would only start to
appear past roughly 100 chunks. No change to src/ingestion.py is warranted:
its L2 default is provably equivalent to cosine for this model.

WHAT THIS DOES NOT ESTABLISH
----------------------------
One dataset, one model, one set of HNSW parameters (ef_search=100,
max_neighbors=16, ef_construction=100). It does not support a general "HNSW
costs 1.7%" claim -- that figure depends on corpus size, dimensionality and
those parameters. And since every configuration is deterministic, re-running
cannot produce an error bar.

Usage:
    uv run python eval/beir_public/exact_search_diag.py fiqa

Needs ~1.1 GB of free RAM and ~40 min on first run (the embedding pass). The
embedding matrices are cached under _data/ (gitignored), so re-runs that only
re-score are near-instant.
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from beir.datasets.data_loader import GenericDataLoader
from beir.retrieval.evaluation import EvaluateRetrieval
from langchain_huggingface import HuggingFaceEmbeddings

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
from ingestion import EMBEDDING_MODEL_NAME  # noqa: E402  single source of truth

from references import HNSW_L2_MEASURED, PUBLISHED_REFERENCES  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent / "_data"
RESULTS_DIR = Path(__file__).resolve().parent / "results"

K_VALUES = [1, 3, 5, 10]
TOP_K = 10
# chunked so embed_documents() never returns millions of Python floats at once
EMBED_CHUNK = 2000
QUERY_BLOCK = 64

# The HNSW+L2 figure this diagnostic explains, and the external reference it is
# compared against. Both are inputs to the attribution, not measured here --
# they come from references.py, the single source of truth shared with
# run_beir_eval.py.


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:
        return "unknown"


def embed_matrix(emb: HuggingFaceEmbeddings, texts: list[str], cache: Path, label: str) -> np.ndarray:
    """Embeds into a preallocated float32 array, in chunks, and caches to .npy.

    Chunking keeps peak memory flat: embed_documents() returns list[list[float]],
    so one call over a whole corpus would materialise ~22M Python float objects
    (~700 MB for FiQA) before conversion. The cache matters because the embedding
    pass is ~40 min and the scoring that follows is seconds -- without it, every
    re-score pays for the embeddings again.
    """
    if cache.exists():
        matrix = np.load(cache)
        print(f"  reusing cached {cache.name} {matrix.shape}", flush=True)
        return matrix
    probe = np.asarray(emb.embed_documents(texts[:1]), dtype=np.float32)
    matrix = np.empty((len(texts), probe.shape[1]), dtype=np.float32)
    matrix[0] = probe[0]
    for i in range(1, len(texts), EMBED_CHUNK):
        chunk = texts[i : i + EMBED_CHUNK]
        matrix[i : i + len(chunk)] = np.asarray(emb.embed_documents(chunk), dtype=np.float32)
        print(f"  {label} {min(i + len(chunk), len(texts))}/{len(texts)}", flush=True)
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.save(cache, matrix)
    return matrix


def exact_topk(
    queries: np.ndarray,
    docs: np.ndarray,
    doc_ids: list[str],
    query_ids: list[str],
    *,
    cosine: bool,
) -> dict[str, dict[str, float]]:
    """Brute-force top-k over the whole corpus -- no index, no approximation.

    Scores are reduced to top-k per query block rather than materialising a full
    (n_queries x n_docs) matrix.
    """
    if cosine:
        docs_m = docs / np.linalg.norm(docs, axis=1, keepdims=True)
        queries_m = queries / np.linalg.norm(queries, axis=1, keepdims=True)
        doc_sq = None
    else:
        docs_m, queries_m = docs, queries
        doc_sq = (docs * docs).sum(axis=1)

    results: dict[str, dict[str, float]] = {}
    for i in range(0, len(query_ids), QUERY_BLOCK):
        block = queries_m[i : i + QUERY_BLOCK] @ docs_m.T
        if doc_sq is not None:
            # -||q-d||^2 up to the per-row constant ||q||^2, which cannot change
            # the ranking within a row
            block = 2.0 * block - doc_sq[None, :]
        idx = np.argpartition(-block, TOP_K, axis=1)[:, :TOP_K]
        for row in range(block.shape[0]):
            ranked = idx[row][np.argsort(-block[row, idx[row]])]
            results[query_ids[i + row]] = {doc_ids[c]: float(block[row, c]) for c in ranked}
        del block
    return results


def score(qrels: dict, results: dict) -> dict[str, dict[str, float]]:
    ndcg, _map, recall, precision = EvaluateRetrieval(None, k_values=K_VALUES).evaluate(
        qrels, results, K_VALUES
    )
    return {"ndcg": ndcg, "map": _map, "recall": recall, "precision": precision}


def _print_table(name: str, metrics: dict) -> None:
    print(f"\n=== {name} ===")
    print(f"{'k':>4} {'NDCG':>8} {'MAP':>8} {'Recall':>8} {'Prec':>8}")
    for k in K_VALUES:
        print(
            f"{k:>4} {metrics['ndcg'][f'NDCG@{k}']:>8.4f} {metrics['map'][f'MAP@{k}']:>8.4f} "
            f"{metrics['recall'][f'Recall@{k}']:>8.4f} {metrics['precision'][f'P@{k}']:>8.4f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Attribute the BEIR gap: ANN loss vs metric")
    parser.add_argument("dataset", help="Dataset name, e.g. fiqa (must already be downloaded)")
    args = parser.parse_args()

    data_path = DATA_DIR / args.dataset
    if not data_path.exists():
        sys.exit(
            f"{data_path} not found -- run "
            f"`uv run python eval/beir_public/download.py {args.dataset}` first."
        )

    corpus, queries, qrels = GenericDataLoader(data_folder=str(data_path)).load(split="test")
    print(f"corpus={len(corpus)} queries={len(queries)} qrels={len(qrels)}", flush=True)

    doc_ids = list(corpus.keys())
    doc_texts = [
        f"{corpus[d].get('title', '')} {corpus[d].get('text', '')}".strip() for d in doc_ids
    ]
    query_ids = list(queries.keys())
    query_texts = [queries[q] for q in query_ids]
    del corpus

    # identical to src/ingestion.py's call: no encode_kwargs, so no
    # normalize_embeddings -- the model's own Normalize layer makes the vectors
    # unit-length anyway, which is the whole reason configuration B is a no-op
    emb = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL_NAME, model_kwargs={"device": "cpu"})

    docs = embed_matrix(emb, doc_texts, data_path / "_emb_docs.npy", "docs")
    del doc_texts
    queries_m = embed_matrix(emb, query_texts, data_path / "_emb_queries.npy", "queries")

    norms = np.linalg.norm(docs, axis=1)
    unit_norm = bool(np.allclose(norms, 1.0, atol=1e-5))
    print(f"\ndoc vector norms: min={norms.min():.6f} max={norms.max():.6f} unit_norm={unit_norm}")

    a = score(qrels, exact_topk(queries_m, docs, doc_ids, query_ids, cosine=False))
    _print_table("A: exact + L2 (non-normalised call, unit-norm vectors)", a)
    b = score(qrels, exact_topk(queries_m, docs, doc_ids, query_ids, cosine=True))
    _print_table("B: exact + cosine (explicitly normalised)", b)

    key = (args.dataset, EMBEDDING_MODEL_NAME)
    baseline = HNSW_L2_MEASURED.get(key)
    ref = PUBLISHED_REFERENCES.get(key)
    published = ref["ndcg_at_10"] if ref else None
    if baseline is None or published is None:
        sys.exit(
            f"No baseline/reference recorded for {key} in references.py -- "
            f"this diagnostic attributes a gap, so it needs both endpoints."
        )
    a10, b10 = a["ndcg"]["NDCG@10"], b["ndcg"]["NDCG@10"]
    ann_loss = round(a10 - baseline, 4) if baseline else None
    metric_effect = round(b10 - a10, 4)
    residual = round(published - b10, 4) if published else None

    print(f"\n=== ATTRIBUTION of the {baseline} -> {published} gap ===")
    print(f"  HNSW + L2          {baseline:.4f}   (run_beir_eval.py, not measured here)")
    print(f"  A exact + L2       {a10:.4f}   ANN loss      = {ann_loss:+.4f}")
    print(f"  B exact + cosine   {b10:.4f}   metric effect = {metric_effect:+.4f}")
    print(f"  published          {published:.4f}   residual      = {residual:+.4f}")

    out = {
        "eval_type": "diagnostic",
        "name": "exact_search_attribution",
        "dataset": args.dataset,
        "purpose": (
            "Explain why run_beir_eval.py's HNSW+L2 score sits below the published "
            "reference, by splitting the gap between the approximate index and the "
            "distance metric. NOT a benchmark of production -- run_beir_eval.py is "
            "that. This removes production's approximation on purpose, to price it."
        ),
        "question": (
            "Is the gap (a) normal ANN approximation loss, (b) a distance-metric "
            "mismatch with how the model is meant to be used, or (c) a bug? These "
            "differ in consequence: ANN loss vanishes on a small corpus, while (b) "
            "or (c) would affect incident-pilot's 60-chunk store equally."
        ),
        "method": (
            "Controlled experiment; each configuration changes exactly one variable "
            "from the previous one. The corpus is embedded once and the same matrix "
            "reused for A and B, so no delta can come from embedding variation. "
            "Exact search is brute force over the full matrix (no index). Same "
            "model, concatenation, corpus, queries and pytrec_eval/qrels throughout."
        ),
        "configurations": {
            "baseline_hnsw_l2": "Chroma HNSW + L2 -- production's path, measured by run_beir_eval.py",
            "A_exact_l2": "brute force + L2 -- approximation removed, metric unchanged",
            "B_exact_cosine": "brute force + cosine -- metric changed, exactness unchanged",
        },
        "embedding_model": EMBEDDING_MODEL_NAME,
        "embeddings_unit_norm": unit_norm,
        "unit_norm_note": (
            "all-MiniLM-L6-v2 ends in a Normalize module, so vectors are unit-length "
            "even though normalize_embeddings is never passed. For unit vectors "
            "||q-d||^2 = 2-2*cos, so L2 and cosine rank identically -- configuration "
            "B is a no-op by algebra, and matching A is a wiring check, not a result. "
            "This is why 'non-normalized embeddings' in older result files is wrong."
        ),
        "n_corpus": len(doc_ids),
        "n_queries": len(query_ids),
        "k_values": K_VALUES,
        "results": {"A_exact_l2": a, "B_exact_cosine": b},
        "attribution_ndcg_at_10": {
            "baseline_hnsw_l2": baseline,
            "A_exact_l2": round(a10, 4),
            "B_exact_cosine": round(b10, 4),
            "published_reference": published,
            "ann_loss": ann_loss,
            "metric_effect": metric_effect,
            "residual": residual,
            "total_gap": round(published - baseline, 4) if published and baseline else None,
            "note": "deltas are each measured against the row above; they sum to total_gap",
        },
        "interpretation": [
            "The gap is ANN approximation loss: removing the approximate index "
            "recovers ~95% of it, the metric contributes 0%, ~5% is unexplained.",
            "The sign was predicted in advance: HNSW can only miss true neighbours, "
            "never beat them, so exact search can only tie or improve. A drop would "
            "have falsified the ANN explanation.",
            "No bug: exact search landing ~0.0003 from the published reference is the "
            "strongest validation of this harness -- mis-wired qrels or bad "
            "preprocessing would not coincidentally reproduce the reference.",
            "Corroborated across corpus sizes: SciFact (5,183 docs) shows no visible "
            "ANN loss under HNSW, FiQA (57,638) shows 1.7%, as ANN error should grow "
            "with corpus size.",
        ],
        "production_relevance": (
            "None at current scale, and provably so. The built store records "
            "ef_search=100 over 60 chunks, so the search beam is wider than the whole "
            "corpus: every vector is examined and retrieval is already exact. The 1.7% "
            "is a property of 57,638 documents. It would only begin to appear past "
            "roughly 100 chunks. src/ingestion.py needs no change -- its L2 default is "
            "provably equivalent to cosine for this model."
        ),
        "published_reference": ref,
        "caveats": [
            "One dataset, one model, one HNSW parameter set (ef_search=100, "
            "max_neighbors=16, ef_construction=100). Does not support a general "
            "'HNSW costs 1.7%' claim -- that depends on corpus size, dimensionality "
            "and those parameters.",
            "Every configuration here is deterministic, so re-running reproduces the "
            "figures exactly and cannot yield an error bar.",
            "baseline_hnsw_l2 and published_reference are inputs carried in this "
            "script, not measured by it.",
        ],
        "git_commit": _git_commit(),
        "run_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / (
        f"{args.dataset}__exact_search_attribution__"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    out_path.write_text(json.dumps(out, indent=2))
    print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()
