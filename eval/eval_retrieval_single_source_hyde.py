"""Runner: task-1 single-source qrels (checkout-api subset) against the
full RAG+HyDE pipeline via IncidentPilot.retrieve(). Filtered to
checkout-api to limit real Groq LLM calls for this first HyDE round -- see
eval_retrieval_hyde.py for the shared eval logic.

Usage:
    uv run python eval/eval_retrieval_single_source_hyde.py
"""

from eval_retrieval_hyde import run
from rag_qrels import QRELS as _ALL_QRELS

QRELS = [q for q in _ALL_QRELS if q.expected_source == "checkout-api-runbook.md"]

if __name__ == "__main__":
    run(QRELS)
