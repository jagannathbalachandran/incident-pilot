"""Shared HyDE-pipeline eval core.

Evaluates qrels against IncidentPilot.retrieve() -- the real, shipped
HyDE-expand -> search -> dedupe -> rank -> cap-to-MAX_RETRIEVED_CHUNKS
pipeline, not a reimplementation of it. Same source+phrase relevance logic
as eval_retrieval_single_source.py's task-1 eval, adapted for two real
differences at this layer:

  - k varies per query, up to MAX_RETRIEVED_CHUNKS (6): HyDE's 6 sub-queries
    can produce anywhere from 0 to 6 unique deduplicated chunks, unlike
    task-1's fixed similarity_search_with_score(k=3). Precision divides by
    however many chunks actually came back for that query, not a constant.
  - no per-chunk similarity score: retrieve()'s public return is
    list[{source, section, content}], no score field. RR still works since
    the list is already rank-ordered by _retrieve_with_queries' internal
    sort; RetrievedChunk.score is just left unset here.

Each query costs one real Groq LLM call (the HyDE expansion) -- not free or
instant like the task-1 eval. Run deliberately, not on every commit.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from incident_pilot import MAX_RETRIEVED_CHUNKS, IncidentPilot  # noqa: E402

from schemas import QrelItem, QueryEvalResult, RagEvalReport, RetrievedChunk  # noqa: E402
from source_aliases import matches_expected_source  # noqa: E402


def evaluate_query_hyde(pilot: IncidentPilot, qrel: QrelItem) -> QueryEvalResult:
    chunks = pilot.retrieve(qrel.query)

    retrieved = [
        RetrievedChunk(
            source=c.get("source", "unknown"),
            section=c.get("section", "unknown"),
            content=c.get("content", ""),
            rank=rank,
        )
        for rank, c in enumerate(chunks, start=1)
    ]

    # Same source-checked-first, then-phrase relevance logic as task 1.
    matched_phrases: list[str] = []
    first_relevant_rank = None
    for chunk in retrieved:
        if not matches_expected_source(chunk.source, qrel.expected_source):
            continue
        for phrase in qrel.must_contain:
            if phrase in chunk.content and phrase not in matched_phrases:
                matched_phrases.append(phrase)
                if first_relevant_rank is None:
                    first_relevant_rank = chunk.rank

    relevant_contents = {
        chunk.content for chunk in retrieved
        if matches_expected_source(chunk.source, qrel.expected_source)
        and any(phrase in chunk.content for phrase in qrel.must_contain)
    }
    relevant_retrieved = len(relevant_contents)

    precision = (relevant_retrieved / len(retrieved)) if retrieved else 0.0

    return QueryEvalResult(
        query=qrel.query,
        expected_source=qrel.expected_source,
        retrieved=retrieved,
        matched_phrases=matched_phrases,
        precision=precision,
        recall=len(matched_phrases) / len(qrel.must_contain),
        reciprocal_rank=(1 / first_relevant_rank) if first_relevant_rank else 0.0,
    )


def print_report_hyde(report: RagEvalReport) -> None:
    print(f"RAG+HyDE Retrieval Eval -- full pipeline "
          f"(HyDE expand -> search -> dedupe -> rank -> cap {report.k})\n")
    print(
        f"k varies per query here (up to {report.k}), unlike the task-1 eval's\n"
        "fixed k=3 -- HyDE's sub-queries can produce anywhere from 0 to the cap\n"
        "in unique deduplicated chunks, so Precision divides by however many\n"
        "chunks actually came back for that query, not a constant. Each query\n"
        "cost one real Groq LLM call (the HyDE expansion).\n"
    )

    query_w = 55
    header = f"{'Query':<{query_w}} {'Precision':>10} {'Recall':>8} {'RR':>8}"
    print(header)
    print("-" * len(header))
    for r in report.results:
        q = r.query if len(r.query) <= query_w else r.query[: query_w - 3] + "..."
        print(f"{q:<{query_w}} {r.precision:>10.3f} {r.recall:>8.3f} {r.reciprocal_rank:>8.3f}")
    print("-" * len(header))
    print(f"{'Mean / MRR':<{query_w}} {report.mean_precision:>10.3f} {report.mean_recall:>8.3f} {report.mrr:>8.3f}")


def run(qrels: list[QrelItem]) -> RagEvalReport:
    pilot = IncidentPilot()
    try:
        results = [evaluate_query_hyde(pilot, qrel) for qrel in qrels]
    finally:
        pilot.close()

    n = len(results)
    report = RagEvalReport(
        k=MAX_RETRIEVED_CHUNKS,
        results=results,
        mean_precision=sum(r.precision for r in results) / n,
        mean_recall=sum(r.recall for r in results) / n,
        mrr=sum(r.reciprocal_rank for r in results) / n,
    )

    print_report_hyde(report)
    return report
