"""Checkout-api-only subset of the synthetic robustness qrels, for the HyDE eval.

HyDE evaluation costs one real Groq LLM call per query (unlike the free,
instant task-1 vectorstore-only eval), so this first HyDE round is scoped
to checkout-api's 3 base queries / 36 tuples only, not the full 72-tuple
set across all 4 services -- see rag_qrels_synthetic_robustness.py for the
full set and the D3/D4/D5 tuple methodology.

Filters from the full set rather than duplicating entries, so this subset
always stays in sync if the checkout-api tuples there change.
"""

from rag_qrels_synthetic_robustness import QRELS as _ALL_QRELS

QRELS = [q for q in _ALL_QRELS if q.expected_source == "checkout-api-runbook.md"]
