"""Golden queries for task-1 RAG retrieval eval: single query, top-3, no HyDE.

Relevance requires BOTH: the retrieved chunk's `source` metadata equals
`expected_source`, AND at least one `must_contain` phrase appears in its
content (see evaluate_query() in eval_retrieval.py). Source is checked
first -- `must_contain` phrases don't all need to be unique across the
whole corpus (some, like the shared "Known Issue #1" template wording,
deliberately aren't), since the source match already rules out
same-phrase-wrong-doc false positives. See QrelItem's docstring in
schemas.py for why relevance isn't keyed on `section` metadata.

Sourced from synthetic-data/runbooks/ (the markdown corpus ingestion.py
actually indexes) -- not synthetic-data/real-runbooks/ (PDF/DOCX, unused).

Query 6 (postmortem root-cause query) held out for now -- its original
must_contain phrase ("cold-cache behavior") doesn't actually appear as a
contiguous string in the source doc (hard line-wrap splits it across two
lines); to be fixed and added back after the first 5 are validated.
"""

from schemas import QrelItem

QRELS: list[QrelItem] = [
    QrelItem(
        query="checkout-api p99 latency climbing, active_connections pinned at max_connections",
        expected_source="checkout-api-runbook.md",
        must_contain=["connection pool exhaustion"],
    ),
    QrelItem(
        query="checkout-api error rate climbing with 400 Bad Request responses",
        expected_source="checkout-api-runbook.md",
        must_contain=["request contract", "checkout-api-request-schema.md"],
    ),
    QrelItem(
        query="auth-service latency spike, cache_hit_ratio dropped suddenly",
        expected_source="auth-service-runbook.md",
        must_contain=["auth-session-cache.internal", "cache node fails"],
    ),
    QrelItem(
        query="listing-service sudden latency increase, cache_hit_ratio drop",
        expected_source="listing-service-runbook.md",
        must_contain=["connection pool dashboard", "cache hit rate"],
    ),
    QrelItem(
        query="payment-service connection pool exhaustion, could not obtain connection from pool",
        expected_source="payment-service-runbook.md",
        must_contain=["payment-pool.ini"],
    ),
]
