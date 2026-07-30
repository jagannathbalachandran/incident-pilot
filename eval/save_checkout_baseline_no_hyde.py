"""Saves a no-HyDE (task-1, vectorstore-only) JSON baseline for checkout-api
queries, so it can be diffed against the HyDE-pipeline results later.

Covers both checkout-api qrel sets:
  - rag_qrels.py's 2 checkout-api entries (single-source baseline)
  - rag_qrels_synthetic_robustness_checkout_hyde.py's 36 entries (synthetic
    phrasing-robustness set, checkout-api subset)

Usage:
    uv run python eval/save_checkout_baseline_no_hyde.py
"""

import json
from pathlib import Path

from eval_retrieval_single_source import evaluate_query, load_vectorstore, K
from rag_qrels import QRELS as _SINGLE_SOURCE_ALL
from rag_qrels_synthetic_robustness_checkout_hyde import QRELS as ROBUSTNESS_CHECKOUT_QRELS
from schemas import RagEvalReport

RESULTS_DIR = Path(__file__).parent / "results"

SINGLE_SOURCE_CHECKOUT_QRELS = [
    q for q in _SINGLE_SOURCE_ALL if q.expected_source == "checkout-api-runbook.md"
]


def build_report(vectorstore, qrels) -> RagEvalReport:
    results = [evaluate_query(vectorstore, qrel) for qrel in qrels]
    n = len(results)
    return RagEvalReport(
        k=K,
        results=results,
        mean_precision=sum(r.precision for r in results) / n,
        mean_recall=sum(r.recall for r in results) / n,
        mrr=sum(r.reciprocal_rank for r in results) / n,
    )


def main() -> None:
    RESULTS_DIR.mkdir(exist_ok=True)
    vectorstore = load_vectorstore()

    single_source_report = build_report(vectorstore, SINGLE_SOURCE_CHECKOUT_QRELS)
    robustness_report = build_report(vectorstore, ROBUSTNESS_CHECKOUT_QRELS)

    single_source_path = RESULTS_DIR / "checkout_single_source_no_hyde.json"
    robustness_path = RESULTS_DIR / "checkout_robustness_no_hyde.json"

    single_source_path.write_text(single_source_report.model_dump_json(indent=2))
    robustness_path.write_text(robustness_report.model_dump_json(indent=2))

    print(f"single-source ({len(SINGLE_SOURCE_CHECKOUT_QRELS)} queries): "
          f"mean_precision={single_source_report.mean_precision:.3f} "
          f"mean_recall={single_source_report.mean_recall:.3f} "
          f"mrr={single_source_report.mrr:.3f}")
    print(f"  -> saved to {single_source_path}")

    print(f"robustness ({len(ROBUSTNESS_CHECKOUT_QRELS)} queries): "
          f"mean_precision={robustness_report.mean_precision:.3f} "
          f"mean_recall={robustness_report.mean_recall:.3f} "
          f"mrr={robustness_report.mrr:.3f}")
    print(f"  -> saved to {robustness_path}")


if __name__ == "__main__":
    main()
