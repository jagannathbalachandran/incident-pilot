"""Suite registry -- the extensibility point.

A suite is a named qrel-set + scoring function, registered under
(category, mode). Adding a new eval later means adding one SuiteSpec entry
here, not touching core.py. Every entry below reuses existing eval scoring
logic unchanged (eval_retrieval_single_source.py / eval_retrieval_hyde.py)
-- this module is wiring, not a reimplementation.
"""

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

# Module-level import only -- reads CHUNKS_PER_QUERY/MAX_RETRIEVED_CHUNKS without
# instantiating IncidentPilot (which needs GROQ_API_KEY and spawns an MCP
# subprocess; that only happens in _setup_incident_pilot below, at run time).
from incident_pilot import (  # noqa: E402
    CHUNKS_PER_QUERY as _HYDE_CHUNKS_PER_QUERY,
    MAX_RETRIEVED_CHUNKS as _HYDE_MAX_RETRIEVED_CHUNKS,
)


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
}


def suites_for(category: str, mode: str) -> dict[str, SuiteSpec]:
    return {
        suite_name: spec
        for (cat, m, suite_name), spec in SUITES.items()
        if cat == category and m == mode
    }
