"""Runner for the synthetic robustness qrels (rag_qrels_synthetic_robustness.py).

Reuses evaluate_query/load_vectorstore/print_report from
eval_retrieval_single_source.py unchanged -- the scoring logic (source+phrase
relevance, parent_content substitution, distinct-content dedup) is identical,
only the qrel set differs. See rag_qrels_synthetic_robustness.py's module
docstring for how these queries were generated.

Usage:
    uv run python eval/eval_retrieval_robustness.py
"""

from eval_retrieval_single_source import K, evaluate_query, load_vectorstore, print_report
from rag_qrels_synthetic_robustness import QRELS
from schemas import RagEvalReport


def main() -> RagEvalReport:
    vectorstore = load_vectorstore()
    results = [evaluate_query(vectorstore, qrel) for qrel in QRELS]

    n = len(results)
    report = RagEvalReport(
        k=K,
        results=results,
        mean_precision=sum(r.precision for r in results) / n,
        mean_recall=sum(r.recall for r in results) / n,
        mrr=sum(r.reciprocal_rank for r in results) / n,
    )

    print_report(report)
    return report


if __name__ == "__main__":
    main()
