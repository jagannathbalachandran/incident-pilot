"""Synthetic robustness qrels: phrasing-variation stress test for RAG retrieval.

Every query in rag_qrels.py (the single-source baseline) is phrased once,
precisely, in symptom-observed mode. This file generates MULTIPLE synthetic
phrasings of the SAME underlying incident, to test whether retrieval still
finds the right chunk when the query is worded the way a real on-call
engineer actually writes under pressure -- not the clean, technical
phrasing a query is "supposed" to use.

Methodology -- each query is built from a tuple of independent dimensions:

  D1 (service)        -- fixed per base incident, e.g. checkout-api
  D2 (incident type)  -- fixed per base incident, e.g. pool_exhaustion
  D3 (service form)   -- how the service name is written:
                           canonical:    "checkout-api"
                           natural lang: "checkout service"
                           abbreviation: "checkout srv"
                           misspelling:  "chckout srv"
  D4 (phrasing mode)  -- symptom_observed (describes behavior, names no
                           cause) vs root_cause_named (SRE already names a
                           suspected mechanism, e.g. "pooling issue")
  D5 (symptom/cause    -- precise / vague / misspelled wording of whichever
       form)              of D4 applies. This eval only exercises vague and
                           misspelled forms deliberately -- precise phrasing
                           is already covered by rag_qrels.py's baseline, so
                           re-testing it here would be redundant. D3 x D4 x
                           D5 gives the full combinatorial tuple space; see
                           conversation history for the full 24-cell table
                           this was trimmed down from.

must_contain differs by D4, not just by base incident:
  - symptom_observed queries are deliberately ambiguous (e.g. "response
    time has gone up" could mean pool exhaustion OR cache failover -- the
    checkout-api runbook's own triage flow checks both dashboards before
    distinguishing them). So must_contain requires phrases from BOTH Known
    Issues -- a good retrieval should surface enough context to cover every
    plausible cause, not just the one this qrel happens to be "about".
  - root_cause_named queries have the SRE already naming a specific
    mechanism (a pooling/connection issue), which narrows the search --
    must_contain only requires the pool-exhaustion phrase for these.

Covers base query 1 (checkout-api / pool_exhaustion) and base query 2
(checkout-api / 4xx_request_contract) from rag_qrels.py, each expanded to
12 phrasing variants, plus a new base query 3 (checkout-api /
401_auth_issue) -- not one of the original 5, but a documented Known-Issue-
adjacent branch in checkout-api-runbook.md's error-rate-high triage
("401 Unauthorized -> auth token missing, expired, or invalid... recent
token-rotation or auth-service change") that had no dedicated qrel before.
Also covers the remaining 3 base queries from rag_qrels.py: auth-service /
cache_failover, listing-service / cache_failover, payment-service /
pool_exhaustion.

Note on must_contain for these three: auth-service and payment-service each
have only ONE Known Issue under their p99-latency-high triage branch (no
pool/cache counterpart to be ambiguous with -- auth-service explicitly has
no DB connection pool, payment-service has no cache), so their
symptom_observed rows use a single required phrase, unlike checkout-api and
listing-service (which both have two Known Issues and so need the
both-phrases ambiguity treatment).

All D5 values here are vague or misspelled only -- no precise/technical
phrasing, since that's already covered by rag_qrels.py's baseline queries
and re-testing it here would be redundant (same reasoning as the
checkout-api queries above).
"""

from schemas import QrelItem

QRELS: list[QrelItem] = [
    # =====================================================================
    # Base query 1: checkout-api / pool_exhaustion
    # =====================================================================

    # --- symptom_observed: ambiguous symptom, must find BOTH known issues ---

    # D3=canonical, D4=symptom_observed, D5=vague
    QrelItem(
        query="checkout-api response time has gone up",
        expected_source="checkout-api-runbook.md",
        must_contain=["connection pool exhaustion", "cache node failover"],
    ),
    # D3=canonical, D4=symptom_observed, D5=misspelled
    QrelItem(
        query="checkout-api latencyy has increased",
        expected_source="checkout-api-runbook.md",
        must_contain=["connection pool exhaustion", "cache node failover"],
    ),
    # D3=natural language, D4=symptom_observed, D5=vague
    QrelItem(
        query="checkout service response time has gone up",
        expected_source="checkout-api-runbook.md",
        must_contain=["connection pool exhaustion", "cache node failover"],
    ),
    # D3=natural language, D4=symptom_observed, D5=misspelled
    QrelItem(
        query="checkout service latenci has spiked",
        expected_source="checkout-api-runbook.md",
        must_contain=["connection pool exhaustion", "cache node failover"],
    ),
    # D3=abbreviation, D4=symptom_observed, D5=vague
    QrelItem(
        query="checkout srv feels slow",
        expected_source="checkout-api-runbook.md",
        must_contain=["connection pool exhaustion", "cache node failover"],
    ),
    # D3=abbreviation, D4=symptom_observed, D5=misspelled
    QrelItem(
        query="checkout srv resposne time is high",
        expected_source="checkout-api-runbook.md",
        must_contain=["connection pool exhaustion", "cache node failover"],
    ),
    # D3=misspelling, D4=symptom_observed, D5=vague
    QrelItem(
        query="chckout srv taking longer than usual",
        expected_source="checkout-api-runbook.md",
        must_contain=["connection pool exhaustion", "cache node failover"],
    ),
    # D3=misspelling, D4=symptom_observed, D5=misspelled
    QrelItem(
        query="chckout srv response time has increaed",
        expected_source="checkout-api-runbook.md",
        must_contain=["connection pool exhaustion", "cache node failover"],
    ),

    # --- root_cause_named: SRE already narrowed it to a pooling issue ---

    # D3=canonical, D4=root_cause_named, D5=vague
    QrelItem(
        query="checkout-api might be some kind of connection/pooling issue",
        expected_source="checkout-api-runbook.md",
        must_contain=["connection pool exhaustion"],
    ),
    # D3=natural language, D4=root_cause_named, D5=vague
    QrelItem(
        query="checkout service connections maxed out maybe",
        expected_source="checkout-api-runbook.md",
        must_contain=["connection pool exhaustion"],
    ),
    # D3=abbreviation, D4=root_cause_named, D5=vague
    QrelItem(
        query="checkout srv think its a pool problem",
        expected_source="checkout-api-runbook.md",
        must_contain=["connection pool exhaustion"],
    ),
    # D3=misspelling, D4=root_cause_named, D5=vague
    QrelItem(
        query="chckout srv possibly a db connection issue",
        expected_source="checkout-api-runbook.md",
        must_contain=["connection pool exhaustion"],
    ),

    # =====================================================================
    # Base query 2: checkout-api / 4xx_request_contract
    #
    # A generic "errors/failures" symptom is ambiguous between the 400
    # (request contract) and 401 (auth token) branches -- checkout-api-
    # runbook.md documents both under the same error-rate-high triage
    # section, so symptom_observed rows must find phrases from both;
    # root_cause_named rows (SRE already says "bad request"/"schema
    # issue") only need the request-contract phrase.
    # =====================================================================

    # --- symptom_observed: ambiguous symptom, must find BOTH 4xx and 401 ---

    # D3=canonical, D4=symptom_observed, D5=vague
    QrelItem(
        query="checkout-api is throwing a lot of failed requests",
        expected_source="checkout-api-runbook.md",
        must_contain=["request contract", "auth token missing, expired, or invalid"],
    ),
    # D3=canonical, D4=symptom_observed, D5=misspelled
    QrelItem(
        query="checkout-api seeing alot of erors",
        expected_source="checkout-api-runbook.md",
        must_contain=["request contract", "auth token missing, expired, or invalid"],
    ),
    # D3=natural language, D4=symptom_observed, D5=vague
    QrelItem(
        query="checkout service orders are failing",
        expected_source="checkout-api-runbook.md",
        must_contain=["request contract", "auth token missing, expired, or invalid"],
    ),
    # D3=natural language, D4=symptom_observed, D5=misspelled
    QrelItem(
        query="checkout service requessts are failing",
        expected_source="checkout-api-runbook.md",
        must_contain=["request contract", "auth token missing, expired, or invalid"],
    ),
    # D3=abbreviation, D4=symptom_observed, D5=vague
    QrelItem(
        query="checkout srv lot of customers cant checkout",
        expected_source="checkout-api-runbook.md",
        must_contain=["request contract", "auth token missing, expired, or invalid"],
    ),
    # D3=abbreviation, D4=symptom_observed, D5=misspelled
    QrelItem(
        query="checkout srv checkuot is throwing errors",
        expected_source="checkout-api-runbook.md",
        must_contain=["request contract", "auth token missing, expired, or invalid"],
    ),
    # D3=misspelling, D4=symptom_observed, D5=vague
    QrelItem(
        query="chckout srv getting a lot of failed requests",
        expected_source="checkout-api-runbook.md",
        must_contain=["request contract", "auth token missing, expired, or invalid"],
    ),
    # D3=misspelling, D4=symptom_observed, D5=misspelled
    QrelItem(
        query="chckout srv seeing failuers on orders",
        expected_source="checkout-api-runbook.md",
        must_contain=["request contract", "auth token missing, expired, or invalid"],
    ),

    # --- root_cause_named: SRE already narrowed it to request/schema issue ---

    # D3=canonical, D4=root_cause_named, D5=vague
    QrelItem(
        query="checkout-api might be a bad request or schema issue",
        expected_source="checkout-api-runbook.md",
        must_contain=["request contract"],
    ),
    # D3=natural language, D4=root_cause_named, D5=vague
    QrelItem(
        query="checkout service think its a request format problem",
        expected_source="checkout-api-runbook.md",
        must_contain=["request contract"],
    ),
    # D3=abbreviation, D4=root_cause_named, D5=vague
    QrelItem(
        query="checkout srv maybe a validation issue with the payload",
        expected_source="checkout-api-runbook.md",
        must_contain=["request contract"],
    ),
    # D3=misspelling, D4=root_cause_named, D5=vague
    QrelItem(
        query="chckout srv possibly a request contract problem",
        expected_source="checkout-api-runbook.md",
        must_contain=["request contract"],
    ),

    # =====================================================================
    # Base query 3: checkout-api / 401_auth_issue
    #
    # Same ambiguity logic as base query 2, mirrored: a generic "errors"
    # symptom can't distinguish the 400 (request contract) branch from the
    # 401 (auth token) branch -- both live under the same error-rate-high
    # triage step. symptom_observed rows require both phrases;
    # root_cause_named rows (SRE already says "auth"/"token" issue) only
    # need the auth-token phrase.
    # =====================================================================

    # --- symptom_observed: ambiguous symptom, must find BOTH 4xx and 401 ---

    # D3=canonical, D4=symptom_observed, D5=vague
    QrelItem(
        query="checkout-api getting a lot of login failures",
        expected_source="checkout-api-runbook.md",
        must_contain=["request contract", "auth token missing, expired, or invalid"],
    ),
    # D3=canonical, D4=symptom_observed, D5=misspelled
    QrelItem(
        query="checkout-api seeing atuh errors",
        expected_source="checkout-api-runbook.md",
        must_contain=["request contract", "auth token missing, expired, or invalid"],
    ),
    # D3=natural language, D4=symptom_observed, D5=vague
    QrelItem(
        query="checkout service users cant log in",
        expected_source="checkout-api-runbook.md",
        must_contain=["request contract", "auth token missing, expired, or invalid"],
    ),
    # D3=natural language, D4=symptom_observed, D5=misspelled
    QrelItem(
        query="checkout service usres cant log in",
        expected_source="checkout-api-runbook.md",
        must_contain=["request contract", "auth token missing, expired, or invalid"],
    ),
    # D3=abbreviation, D4=symptom_observed, D5=vague
    QrelItem(
        query="checkout srv seeing auth errors",
        expected_source="checkout-api-runbook.md",
        must_contain=["request contract", "auth token missing, expired, or invalid"],
    ),
    # D3=abbreviation, D4=symptom_observed, D5=misspelled
    QrelItem(
        query="checkout srv getting loginn failures",
        expected_source="checkout-api-runbook.md",
        must_contain=["request contract", "auth token missing, expired, or invalid"],
    ),
    # D3=misspelling, D4=symptom_observed, D5=vague
    QrelItem(
        query="chckout srv customers getting logged out randomly",
        expected_source="checkout-api-runbook.md",
        must_contain=["request contract", "auth token missing, expired, or invalid"],
    ),
    # D3=misspelling, D4=symptom_observed, D5=misspelled
    QrelItem(
        query="chckout srv seeing unauthorizd errors",
        expected_source="checkout-api-runbook.md",
        must_contain=["request contract", "auth token missing, expired, or invalid"],
    ),

    # --- root_cause_named: SRE already narrowed it to a token/auth issue ---

    # D3=canonical, D4=root_cause_named, D5=vague
    QrelItem(
        query="checkout-api might be a token or auth issue",
        expected_source="checkout-api-runbook.md",
        must_contain=["auth token missing, expired, or invalid"],
    ),
    # D3=natural language, D4=root_cause_named, D5=vague
    QrelItem(
        query="checkout service think its an expired token problem",
        expected_source="checkout-api-runbook.md",
        must_contain=["auth token missing, expired, or invalid"],
    ),
    # D3=abbreviation, D4=root_cause_named, D5=vague
    QrelItem(
        query="checkout srv maybe auth tokens are stale",
        expected_source="checkout-api-runbook.md",
        must_contain=["auth token missing, expired, or invalid"],
    ),
    # D3=misspelling, D4=root_cause_named, D5=vague
    QrelItem(
        query="chckout srv possibly a token rotation issue",
        expected_source="checkout-api-runbook.md",
        must_contain=["auth token missing, expired, or invalid"],
    ),

    # =====================================================================
    # Base query 4: auth-service / cache_failover
    #
    # auth-service has only ONE Known Issue under p99-latency-high (session-
    # store/Redis failover) -- no pool-exhaustion counterpart to be
    # ambiguous with (auth-service has no DB connection pool). So
    # symptom_observed and root_cause_named rows use the same single
    # required phrase; there's no second known issue to test coverage of.
    # =====================================================================

    # D3=canonical, D4=symptom_observed, D5=vague
    QrelItem(
        query="auth-service response time has gone up",
        expected_source="auth-service-runbook.md",
        must_contain=["cache node fails"],
    ),
    # D3=canonical, D4=symptom_observed, D5=misspelled
    QrelItem(
        query="auth-service latencyy has increased",
        expected_source="auth-service-runbook.md",
        must_contain=["cache node fails"],
    ),
    # D3=natural language, D4=symptom_observed, D5=vague
    QrelItem(
        query="auth service feels slow",
        expected_source="auth-service-runbook.md",
        must_contain=["cache node fails"],
    ),
    # D3=natural language, D4=symptom_observed, D5=misspelled
    QrelItem(
        query="auth service resposne time is high",
        expected_source="auth-service-runbook.md",
        must_contain=["cache node fails"],
    ),
    # D3=abbreviation, D4=symptom_observed, D5=vague
    QrelItem(
        query="auth svc taking longer than usual to log in",
        expected_source="auth-service-runbook.md",
        must_contain=["cache node fails"],
    ),
    # D3=abbreviation, D4=symptom_observed, D5=misspelled
    QrelItem(
        query="auth svc logins taking to long",
        expected_source="auth-service-runbook.md",
        must_contain=["cache node fails"],
    ),
    # D3=misspelling, D4=symptom_observed, D5=vague
    QrelItem(
        query="athu service response time has gone up",
        expected_source="auth-service-runbook.md",
        must_contain=["cache node fails"],
    ),
    # D3=misspelling, D4=symptom_observed, D5=misspelled
    QrelItem(
        query="athu service latencyy has increased",
        expected_source="auth-service-runbook.md",
        must_contain=["cache node fails"],
    ),
    # D3=canonical, D4=root_cause_named, D5=vague
    QrelItem(
        query="auth-service might be a cache or redis issue",
        expected_source="auth-service-runbook.md",
        must_contain=["cache node fails"],
    ),
    # D3=natural language, D4=root_cause_named, D5=vague
    QrelItem(
        query="auth service think session store is having problems",
        expected_source="auth-service-runbook.md",
        must_contain=["cache node fails"],
    ),
    # D3=abbreviation, D4=root_cause_named, D5=vague
    QrelItem(
        query="auth svc maybe the cache failed over",
        expected_source="auth-service-runbook.md",
        must_contain=["cache node fails"],
    ),
    # D3=misspelling, D4=root_cause_named, D5=vague
    QrelItem(
        query="athu service possibly a redis failover",
        expected_source="auth-service-runbook.md",
        must_contain=["cache node fails"],
    ),

    # =====================================================================
    # Base query 5: listing-service / cache_failover
    #
    # listing-service has BOTH Known Issue #1 (pool exhaustion) and Known
    # Issue #2 (cache node failover) under p99-latency-high, same structure
    # as checkout-api -- symptom_observed rows require both phrases,
    # root_cause_named rows (SRE says "cache"/"redis") narrow to just the
    # cache-failover phrase.
    # =====================================================================

    # D3=canonical, D4=symptom_observed, D5=vague
    QrelItem(
        query="listing-service response time has gone up",
        expected_source="listing-service-runbook.md",
        must_contain=["connection pool exhaustion", "cache node failover"],
    ),
    # D3=canonical, D4=symptom_observed, D5=misspelled
    QrelItem(
        query="listing-service latencyy has increased",
        expected_source="listing-service-runbook.md",
        must_contain=["connection pool exhaustion", "cache node failover"],
    ),
    # D3=natural language, D4=symptom_observed, D5=vague
    QrelItem(
        query="listing service feels slow",
        expected_source="listing-service-runbook.md",
        must_contain=["connection pool exhaustion", "cache node failover"],
    ),
    # D3=natural language, D4=symptom_observed, D5=misspelled
    QrelItem(
        query="listing service resposne time is high",
        expected_source="listing-service-runbook.md",
        must_contain=["connection pool exhaustion", "cache node failover"],
    ),
    # D3=abbreviation, D4=symptom_observed, D5=vague
    QrelItem(
        query="listing svc taking longer to load listings",
        expected_source="listing-service-runbook.md",
        must_contain=["connection pool exhaustion", "cache node failover"],
    ),
    # D3=abbreviation, D4=symptom_observed, D5=misspelled
    QrelItem(
        query="listing svc listngs are slow to load",
        expected_source="listing-service-runbook.md",
        must_contain=["connection pool exhaustion", "cache node failover"],
    ),
    # D3=misspelling, D4=symptom_observed, D5=vague
    QrelItem(
        query="listign service response time has gone up",
        expected_source="listing-service-runbook.md",
        must_contain=["connection pool exhaustion", "cache node failover"],
    ),
    # D3=misspelling, D4=symptom_observed, D5=misspelled
    QrelItem(
        query="listign service latencyy has increased",
        expected_source="listing-service-runbook.md",
        must_contain=["connection pool exhaustion", "cache node failover"],
    ),
    # D3=canonical, D4=root_cause_named, D5=vague
    QrelItem(
        query="listing-service might be a cache/redis issue",
        expected_source="listing-service-runbook.md",
        must_contain=["cache node failover"],
    ),
    # D3=natural language, D4=root_cause_named, D5=vague
    QrelItem(
        query="listing service think its a cache failover",
        expected_source="listing-service-runbook.md",
        must_contain=["cache node failover"],
    ),
    # D3=abbreviation, D4=root_cause_named, D5=vague
    QrelItem(
        query="listing svc possibly a redis problem",
        expected_source="listing-service-runbook.md",
        must_contain=["cache node failover"],
    ),
    # D3=misspelling, D4=root_cause_named, D5=vague
    QrelItem(
        query="listign service maybe cache nodes failed",
        expected_source="listing-service-runbook.md",
        must_contain=["cache node failover"],
    ),

    # =====================================================================
    # Base query 6: payment-service / pool_exhaustion
    #
    # payment-service has only ONE Known Issue under p99-latency-high (pool
    # exhaustion) -- no cache counterpart (payment-service has no cache
    # dependency in this corpus). Same single-phrase treatment as
    # auth-service above.
    # =====================================================================

    # D3=canonical, D4=symptom_observed, D5=vague
    QrelItem(
        query="payment-service response time has gone up",
        expected_source="payment-service-runbook.md",
        must_contain=["connection pool exhaustion"],
    ),
    # D3=canonical, D4=symptom_observed, D5=misspelled
    QrelItem(
        query="payment-service latencyy has increased",
        expected_source="payment-service-runbook.md",
        must_contain=["connection pool exhaustion"],
    ),
    # D3=natural language, D4=symptom_observed, D5=vague
    QrelItem(
        query="payment service feels slow",
        expected_source="payment-service-runbook.md",
        must_contain=["connection pool exhaustion"],
    ),
    # D3=natural language, D4=symptom_observed, D5=misspelled
    QrelItem(
        query="payment service resposne time is high",
        expected_source="payment-service-runbook.md",
        must_contain=["connection pool exhaustion"],
    ),
    # D3=abbreviation, D4=symptom_observed, D5=vague
    QrelItem(
        query="payment svc taking longer to process payments",
        expected_source="payment-service-runbook.md",
        must_contain=["connection pool exhaustion"],
    ),
    # D3=abbreviation, D4=symptom_observed, D5=misspelled
    QrelItem(
        query="payment svc paymnets are taking long",
        expected_source="payment-service-runbook.md",
        must_contain=["connection pool exhaustion"],
    ),
    # D3=misspelling, D4=symptom_observed, D5=vague
    QrelItem(
        query="paymnet service response time has gone up",
        expected_source="payment-service-runbook.md",
        must_contain=["connection pool exhaustion"],
    ),
    # D3=misspelling, D4=symptom_observed, D5=misspelled
    QrelItem(
        query="paymnet service latencyy has increased",
        expected_source="payment-service-runbook.md",
        must_contain=["connection pool exhaustion"],
    ),
    # D3=canonical, D4=root_cause_named, D5=vague
    QrelItem(
        query="payment-service might be some kind of connection/pooling issue",
        expected_source="payment-service-runbook.md",
        must_contain=["connection pool exhaustion"],
    ),
    # D3=natural language, D4=root_cause_named, D5=vague
    QrelItem(
        query="payment service think its a pool problem",
        expected_source="payment-service-runbook.md",
        must_contain=["connection pool exhaustion"],
    ),
    # D3=abbreviation, D4=root_cause_named, D5=vague
    QrelItem(
        query="payment svc possibly a db connection issue",
        expected_source="payment-service-runbook.md",
        must_contain=["connection pool exhaustion"],
    ),
    # D3=misspelling, D4=root_cause_named, D5=vague
    QrelItem(
        query="paymnet service maybe connections are maxed",
        expected_source="payment-service-runbook.md",
        must_contain=["connection pool exhaustion"],
    ),
]
