"""Suite registry -- the extensibility point.

A suite is a named qrel-set + scoring function, registered under
(category, mode). Adding a new eval later means adding one SuiteSpec entry
here, not touching core.py. Every entry below reuses existing eval scoring
logic unchanged (eval_retrieval_single_source.py / eval_retrieval_hyde.py)
-- this module is wiring, not a reimplementation.
"""

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

# eval/benchmark/ -> eval/ , same sys.path-hack convention eval_retrieval_hyde.py
# already uses (one level up) to reach src/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from schemas import QrelItem, QueryEvalResult  # noqa: E402

from eval_retrieval_single_source import (  # noqa: E402
    K as _NO_HYDE_K,
    evaluate_query as _evaluate_query_no_hyde,
    load_vectorstore as _load_vectorstore,
)
from eval_retrieval_hyde import evaluate_query_hyde as _evaluate_query_hyde  # noqa: E402

from rag_qrels import QRELS as _SINGLE_SOURCE_QRELS  # noqa: E402
from rag_qrels_synthetic_robustness_checkout_hyde import (  # noqa: E402
    QRELS as _ROBUSTNESS_CHECKOUT_QRELS,
)
from rag_qrels_synthetic_robustness import QRELS as _ROBUSTNESS_ALL_QRELS  # noqa: E402

# auth-service/listing-service/payment-service only (36 queries) -- checkout-api's
# 36 are already covered by synthetic_robustness_queries above; this suite is
# scoped to what's genuinely new (never run through HyDE before) rather than
# re-testing checkout-api a second time at extra Groq cost for no new signal.
_ROBUSTNESS_OTHER_SERVICES_QRELS = [
    q for q in _ROBUSTNESS_ALL_QRELS if q.expected_source != "checkout-api-runbook.md"
]

# Module-level import only -- reads CHUNKS_PER_QUERY/MAX_RETRIEVED_CHUNKS without
# instantiating IncidentPilot (which needs GROQ_API_KEY and spawns an MCP
# subprocess; that only happens in _setup_incident_pilot below, at run time).
from incident_pilot import (  # noqa: E402
    CHUNKS_PER_QUERY as _HYDE_CHUNKS_PER_QUERY,
    HYDE_TEMPERATURE as _HYDE_TEMPERATURE,
    MAX_RETRIEVED_CHUNKS as _HYDE_MAX_RETRIEVED_CHUNKS,
)

from hybrid_retrieval import (  # noqa: E402
    HybridIndex,
    RRF_K as _RRF_K,
    evaluate_query_hyde_hybrid as _evaluate_query_hyde_hybrid,
    evaluate_query_no_hyde_hybrid as _evaluate_query_no_hyde_hybrid,
)

VECTORSTORE_DIR = Path(__file__).resolve().parent.parent.parent / "synthetic-data" / "vectorstore"
INGESTION_METADATA_PATH = VECTORSTORE_DIR / "_ingestion_metadata.json"


def _load_ingestion_metadata() -> dict:
    """What actually built the vectorstore currently on disk, stamped by
    ingestion.py at ingestion time -- not re-derived from current code,
    which could be ahead of what's actually been ingested. Missing file
    means either the vectorstore predates this tracking or ingestion.py
    hasn't been re-run since -- both worth surfacing, not silently ignoring.
    """
    if not INGESTION_METADATA_PATH.exists():
        return {"ingestion_metadata": "unavailable (vectorstore predates metadata tracking, or hasn't been rebuilt since)"}
    raw = json.loads(INGESTION_METADATA_PATH.read_text())
    # Disambiguate from the benchmark run's own top-level git_commit (the
    # commit the *benchmark* ran at, not the commit the *vectorstore* was
    # built at -- these can differ if the vectorstore wasn't rebuilt).
    raw["vectorstore_git_commit"] = raw.pop("git_commit", "unknown")
    return raw


def pipeline_config_for(mode: str, ctx: Any) -> dict:
    """Run-level config -- same for every suite in a run, so this is called
    once per run in core.py, not per suite. ctx shape depends on mode:
    no_hyde -> Chroma vectorstore; hyde -> IncidentPilot; no_hyde_bm25_hybrid
    -> HybridIndex; hyde_bm25_hybrid -> (IncidentPilot, HybridIndex).
    """
    config = _load_ingestion_metadata()
    if mode == "hyde":
        config["llm_model"] = ctx.model.model
        config["hyde_temperature"] = _HYDE_TEMPERATURE
    elif mode == "hyde_bm25_hybrid":
        pilot, _index = ctx
        config["llm_model"] = pilot.model.model
        config["hyde_temperature"] = _HYDE_TEMPERATURE
    if "bm25" in mode:
        config["hybrid_bm25"] = True
        config["rrf_k"] = _RRF_K
    return config


@dataclass
class SuiteSpec:
    qrels: list[QrelItem]
    setup: Callable[[], Any]
    evaluate_one: Callable[[Any, QrelItem], QueryEvalResult]
    retrieval_config: dict[str, int] | None = None  # set per entry below
    teardown: Callable[[Any], None] = lambda ctx: None  # noqa: E731


def _setup_incident_pilot() -> Any:
    from incident_pilot import IncidentPilot  # noqa: E402  (module already imported above)

    return IncidentPilot()


def _teardown_incident_pilot(pilot: Any) -> None:
    pilot.close()


def _setup_no_hyde_hybrid() -> HybridIndex:
    return HybridIndex(_load_vectorstore())


def _setup_hyde_hybrid() -> tuple:
    pilot = _setup_incident_pilot()
    return (pilot, HybridIndex(pilot.vectorstore))


def _teardown_hyde_hybrid(ctx: tuple) -> None:
    pilot, _index = ctx
    pilot.close()


# (category, mode, suite_name) -> SuiteSpec
#
# retrieval_config is captured from the *actual* module constants (not
# hardcoded here) so it always reflects what really ran -- if K, or
# CHUNKS_PER_QUERY/MAX_RETRIEVED_CHUNKS, change later (e.g. during the
# embedding-model swap), a new run's retrieval_config will differ from an
# older baseline's, and compare() flags that instead of silently comparing
# results retrieved at different depths.
SUITES: dict[tuple[str, str, str], SuiteSpec] = {
    ("rag_retrieval", "no_hyde", "single_source_queries"): SuiteSpec(
        qrels=_SINGLE_SOURCE_QRELS,
        setup=_load_vectorstore,
        evaluate_one=_evaluate_query_no_hyde,
        retrieval_config={"k": _NO_HYDE_K},
    ),
    ("rag_retrieval", "no_hyde", "synthetic_robustness_queries"): SuiteSpec(
        qrels=_ROBUSTNESS_CHECKOUT_QRELS,
        setup=_load_vectorstore,
        evaluate_one=_evaluate_query_no_hyde,
        retrieval_config={"k": _NO_HYDE_K},
    ),
    # auth-service/listing-service/payment-service only (36 queries) --
    # a separate suite from synthetic_robustness_queries (checkout-only, 36)
    # rather than redefining it, so existing baselines that reference the
    # checkout suite by name stay meaningful/comparable across time.
    ("rag_retrieval", "no_hyde", "synthetic_robustness_other_services"): SuiteSpec(
        qrels=_ROBUSTNESS_OTHER_SERVICES_QRELS,
        setup=_load_vectorstore,
        evaluate_one=_evaluate_query_no_hyde,
        retrieval_config={"k": _NO_HYDE_K},
    ),
    ("rag_retrieval", "hyde", "single_source_queries"): SuiteSpec(
        qrels=_SINGLE_SOURCE_QRELS,
        setup=_setup_incident_pilot,
        evaluate_one=_evaluate_query_hyde,
        teardown=_teardown_incident_pilot,
        retrieval_config={
            "chunks_per_query": _HYDE_CHUNKS_PER_QUERY,
            "max_retrieved_chunks": _HYDE_MAX_RETRIEVED_CHUNKS,
        },
    ),
    ("rag_retrieval", "hyde", "synthetic_robustness_queries"): SuiteSpec(
        qrels=_ROBUSTNESS_CHECKOUT_QRELS,
        setup=_setup_incident_pilot,
        evaluate_one=_evaluate_query_hyde,
        teardown=_teardown_incident_pilot,
        retrieval_config={
            "chunks_per_query": _HYDE_CHUNKS_PER_QUERY,
            "max_retrieved_chunks": _HYDE_MAX_RETRIEVED_CHUNKS,
        },
    ),
    ("rag_retrieval", "hyde", "synthetic_robustness_other_services"): SuiteSpec(
        qrels=_ROBUSTNESS_OTHER_SERVICES_QRELS,
        setup=_setup_incident_pilot,
        evaluate_one=_evaluate_query_hyde,
        teardown=_teardown_incident_pilot,
        retrieval_config={
            "chunks_per_query": _HYDE_CHUNKS_PER_QUERY,
            "max_retrieved_chunks": _HYDE_MAX_RETRIEVED_CHUNKS,
        },
    ),
    ("rag_retrieval", "no_hyde_bm25_hybrid", "single_source_queries"): SuiteSpec(
        qrels=_SINGLE_SOURCE_QRELS,
        setup=_setup_no_hyde_hybrid,
        evaluate_one=_evaluate_query_no_hyde_hybrid,
        retrieval_config={"k": _NO_HYDE_K, "rrf_k": _RRF_K},
    ),
    ("rag_retrieval", "no_hyde_bm25_hybrid", "synthetic_robustness_queries"): SuiteSpec(
        qrels=_ROBUSTNESS_CHECKOUT_QRELS,
        setup=_setup_no_hyde_hybrid,
        evaluate_one=_evaluate_query_no_hyde_hybrid,
        retrieval_config={"k": _NO_HYDE_K, "rrf_k": _RRF_K},
    ),
    ("rag_retrieval", "hyde_bm25_hybrid", "single_source_queries"): SuiteSpec(
        qrels=_SINGLE_SOURCE_QRELS,
        setup=_setup_hyde_hybrid,
        evaluate_one=_evaluate_query_hyde_hybrid,
        teardown=_teardown_hyde_hybrid,
        retrieval_config={
            "chunks_per_query": _HYDE_CHUNKS_PER_QUERY,
            "max_retrieved_chunks": _HYDE_MAX_RETRIEVED_CHUNKS,
            "rrf_k": _RRF_K,
        },
    ),
    ("rag_retrieval", "hyde_bm25_hybrid", "synthetic_robustness_queries"): SuiteSpec(
        qrels=_ROBUSTNESS_CHECKOUT_QRELS,
        setup=_setup_hyde_hybrid,
        evaluate_one=_evaluate_query_hyde_hybrid,
        teardown=_teardown_hyde_hybrid,
        retrieval_config={
            "chunks_per_query": _HYDE_CHUNKS_PER_QUERY,
            "max_retrieved_chunks": _HYDE_MAX_RETRIEVED_CHUNKS,
            "rrf_k": _RRF_K,
        },
    ),
}


def suites_for(category: str, mode: str) -> dict[str, SuiteSpec]:
    return {
        suite_name: spec
        for (cat, m, suite_name), spec in SUITES.items()
        if cat == category and m == mode
    }
