"""Checks incident-pilot's production embedding config against a small public
BEIR dataset, scored with BEIR's standard NDCG/MAP/Recall/Precision metrics
(via pytrec_eval) against that dataset's own published qrels.

Deliberately does NOT use BEIR's own retrieval wrapper (DenseRetrievalExactSearch
/ SentenceBERT, which only offer cos_sim or dot score functions). Instead it
builds a throwaway Chroma vector store from the BEIR corpus using the exact
same call src/ingestion.py makes --
HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL_NAME, model_kwargs={"device":
"cpu"}), no normalize_embeddings -- and retrieves with the same
similarity_search_with_score() call incident_pilot.py's retrieval path uses.
That means the ranking being scored here is literally what src/ingestion.py +
src/incident_pilot.py would produce (same L2-on-non-normalized-vectors
behavior, since no hnsw:space override is set), not an approximation under a
BEIR-supplied score function. BEIR is used purely as the deterministic SCORER
over the dataset's qrels, not as the retriever.

Usage:
    uv run python eval/beir_public/download.py scifact   # once
    uv run python eval/beir_public/run_beir_eval.py scifact --k 1,3,5,10
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from beir.datasets.data_loader import GenericDataLoader
from beir.retrieval.evaluation import EvaluateRetrieval
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
from ingestion import EMBEDDING_MODEL_NAME  # noqa: E402  single source of truth for the model name

DATA_DIR = Path(__file__).resolve().parent / "_data"
RESULTS_DIR = Path(__file__).resolve().parent / "results"


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:
        return "unknown"


def load_prod_embeddings(model_name: str = EMBEDDING_MODEL_NAME) -> HuggingFaceEmbeddings:
    """Mirrors src/ingestion.py's build_vectorstore() embedding call verbatim
    -- same model, same device, no normalize_embeddings -- so this tests what
    actually ships, not a hypothetical "best" config.

    `model_name` is overridable (via --model) purely as a fluke check: if
    swapping in a deliberately weaker model doesn't change the score, that
    means something upstream (qrels/results wiring) is broken in a way that
    makes the scorer insensitive to the embeddings actually used -- not that
    the real model is unusually good.
    """
    return HuggingFaceEmbeddings(model_name=model_name, model_kwargs={"device": "cpu"})


def build_corpus_vectorstore(corpus: dict, embeddings: HuggingFaceEmbeddings) -> Chroma:
    """Indexes the BEIR corpus in ONE from_documents() call -- mirroring the
    single call src/ingestion.py's build_vectorstore() makes -- not in smaller
    manual batches.

    from_documents() -> from_texts() already splits its input via
    chromadb.utils.batch_utils.create_batches at the client's max_batch_size
    (5,461 here), calling embed_documents() once per batch, so one call never
    embeds the whole corpus in a single pass.

    Manual re-batching at 128 docs/call was tried and reverted. It is a priced
    tradeoff, not a free win, and it was reverted for FIDELITY, not for scores:
      * it lowers peak memory ~15% (full FiQA: 1,605 MB -> 1,366 MB) by capping
        the transient list[list[float]] that embed_documents() returns
      * but it perturbs HNSW graph construction, which depends on how
        insertions are batched, shifting scores ~0.5% in a DATASET-DEPENDENT
        direction. Each figure below was reproduced twice on identical code
        and environment:
            SciFact NDCG@10   0.6451 unbatched -> 0.6418 batched   (-0.5%)
            FiQA    NDCG@10   0.3626 unbatched -> 0.3649 batched   (+0.6%)
    Neither is "more correct" -- both are approximate (HNSW) rankings, and the
    direction of the shift is not predictable. Unbatched wins only because it
    is the call production actually makes, keeping this script a measurement of
    src/ingestion.py's real path. If you ever need the 240 MB, re-batching is
    defensible -- but re-baseline both datasets first, because the numbers move.

    Memory: peak working set for the full 57,638-doc FiQA run is 1,605 MB, of
    which ~900 MB is fixed and independent of corpus size (torch ~400, MiniLM
    ~138, the BEIR corpus dict ~68, Chroma/HNSW init and peak overshoot the
    rest); only the remainder scales with the corpus, and sublinearly. An
    earlier attempt was killed with ~0.9-1.2 GB free and completed unchanged
    once ~1 GB was freed elsewhere -- the cause was starvation, not batch size.
    Keep ~2 GB free; a full FiQA run takes ~40 min.
    """
    documents = [
        Document(
            page_content=f"{doc.get('title', '')} {doc.get('text', '')}".strip(),
            metadata={"doc_id": doc_id},
        )
        for doc_id, doc in corpus.items()
    ]
    if not documents:
        raise ValueError(
            "Empty corpus -- nothing to index. Check that the dataset downloaded "
            "correctly and that the split exists."
        )

    # In-memory only (no persist_directory) -- a throwaway index for one run,
    # not a store anything else reads.
    return Chroma.from_documents(documents=documents, embedding=embeddings)


def retrieve_all(vectorstore: Chroma, queries: dict, top_k: int) -> dict:
    results: dict[str, dict[str, float]] = {}
    for query_id, query_text in queries.items():
        hits = vectorstore.similarity_search_with_score(query_text, k=top_k)
        # Chroma's score here is L2 distance (lower = closer) -- the same
        # metric src/ingestion.py's vectorstore uses by default (no
        # hnsw:space override). Negate so higher = more relevant, matching
        # what pytrec_eval expects; this only needs to preserve rank order,
        # not be a calibrated similarity score.
        results[query_id] = {doc.metadata["doc_id"]: -score for doc, score in hits}
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", help="Dataset name, e.g. scifact (must already be downloaded)")
    parser.add_argument("--k", default="1,3,5,10", help="Comma-separated k values, e.g. 1,3,5,10")
    parser.add_argument(
        "--model",
        default=EMBEDDING_MODEL_NAME,
        help="Override embedding model (fluke check / model comparison). "
             "Defaults to the production model (EMBEDDING_MODEL_NAME).",
    )
    args = parser.parse_args()

    k_values = [int(k) for k in args.k.split(",")]
    is_prod_config = args.model == EMBEDDING_MODEL_NAME
    config_label = "prod_config" if is_prod_config else f"override__{args.model.replace('/', '_')}"
    data_path = DATA_DIR / args.dataset
    if not data_path.exists():
        sys.exit(
            f"{data_path} not found -- run "
            f"`uv run python eval/beir_public/download.py {args.dataset}` first."
        )

    print(f"Loading {args.dataset} (test split)...")
    corpus, queries, qrels = GenericDataLoader(data_folder=str(data_path)).load(split="test")
    print(f"corpus={len(corpus)} queries={len(queries)} qrels={len(qrels)}")

    print(f"\nLoading embedding model ({args.model}, {config_label})...")
    embeddings = load_prod_embeddings(args.model)

    print("Embedding corpus (slow step)...")
    vectorstore = build_corpus_vectorstore(corpus, embeddings)

    print("Running queries...")
    top_k = max(k_values)
    results = retrieve_all(vectorstore, queries, top_k)

    evaluator = EvaluateRetrieval(None, k_values=k_values)
    ndcg, _map, recall, precision = evaluator.evaluate(qrels, results, k_values)

    print(f"\n=== {args.dataset} -- {config_label} ({args.model}, L2, non-normalized) ===")
    header = f"{'k':>4} {'NDCG':>8} {'MAP':>8} {'Recall':>8} {'Precision':>10}"
    print(header)
    for k in k_values:
        print(
            f"{k:>4} {ndcg[f'NDCG@{k}']:>8.4f} {_map[f'MAP@{k}']:>8.4f} "
            f"{recall[f'Recall@{k}']:>8.4f} {precision[f'P@{k}']:>10.4f}"
        )

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / (
        f"{args.dataset}__{config_label}__"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    out_path.write_text(
        json.dumps(
            {
                "dataset": args.dataset,
                "config": config_label,
                "embedding_model": args.model,
                "distance": "l2 (chroma implicit default, non-normalized embeddings)",
                "k_values": k_values,
                "n_corpus": len(corpus),
                "n_queries": len(queries),
                "git_commit": _git_commit(),
                "ndcg": ndcg,
                "map": _map,
                "recall": recall,
                "precision": precision,
            },
            indent=2,
        )
    )
    print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()
