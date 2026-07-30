"""Runner: synthetic robustness qrels (checkout-api subset, 36 tuples)
against the full RAG+HyDE pipeline via IncidentPilot.retrieve(). See
eval_retrieval_hyde.py for the shared eval logic and
rag_qrels_synthetic_robustness_checkout_hyde.py for the qrel scoping
rationale.

Usage:
    uv run python eval/eval_retrieval_robustness_hyde.py
"""

from eval_retrieval_hyde import run
from rag_qrels_synthetic_robustness_checkout_hyde import QRELS

if __name__ == "__main__":
    run(QRELS)
