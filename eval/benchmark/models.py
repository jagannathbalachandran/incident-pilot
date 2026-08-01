"""Pydantic schemas for the benchmark/baseline tracking system.

Deliberately generic on metrics (dict[str, float], not hardcoded
precision/recall/mrr fields) so the same shapes work for today's
"rag_retrieval" category and future categories (answer_correctness,
answer_groundedness) with entirely different metric names. See
eval/benchmark/registry.py for how a category+mode+suite maps to an actual
qrel set and scoring function, and eval/benchmark/core.py for how these
get produced, compared, and promoted.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timezone

from pydantic import BaseModel, Field


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class MetricStats(BaseModel):
    """Aggregate of one metric across repeats (or across pooled records)."""

    mean: float
    std: float
    min: float
    max: float
    n: int

    @classmethod
    def from_values(cls, values: list[float]) -> "MetricStats":
        if not values:
            raise ValueError("MetricStats.from_values() called with no values")
        return cls(
            mean=statistics.fmean(values),
            std=statistics.pstdev(values) if len(values) > 1 else 0.0,
            min=min(values),
            max=max(values),
            n=len(values),
        )


class QueryMetricRecord(BaseModel):
    """One query's result within one repeat of one suite."""

    query: str
    identifier: str = Field(
        description="What this query was checked against -- e.g. expected_source "
        "for rag_retrieval. Not used for matching (matching is by `query`); "
        "carried through for readability/debugging."
    )
    metrics: dict[str, float]


class SuiteRunResult(BaseModel):
    """One repeat's results for one suite."""

    records: list[QueryMetricRecord]
    summary_metrics: dict[str, float] = Field(
        description="Mean of each metric key across this repeat's records."
    )


class SuiteAggregate(BaseModel):
    """A suite's results across all repeats of one BenchmarkRun."""

    suite_name: str
    n_queries: int
    retrieval_config: dict[str, int] = Field(
        default_factory=dict,
        description="e.g. {'k': 3} or {'chunks_per_query': 3, 'max_retrieved_chunks': 6} -- "
        "captured from the actual constants at run time, so compare() can flag a run "
        "against a baseline retrieved at a different depth instead of comparing them "
        "as if they were apples-to-apples.",
    )
    repeats: list[SuiteRunResult]
    aggregate_metrics: dict[str, MetricStats] = Field(
        description="Per-metric stats across repeats' summary_metrics."
    )
    per_query_aggregate: dict[str, dict[str, MetricStats]] = Field(
        default_factory=dict,
        description="query -> metric -> stats across repeats, to see which "
        "specific queries are unstable run-to-run, not just the aggregate.",
    )


class BenchmarkRun(BaseModel):
    """One full execution of a (category, mode)'s registered suites.

    Written into eval/benchmarks/runs/<category>/<mode>/ on every run,
    whether or not it's later promoted to a baseline.
    """

    run_id: str
    category: str
    mode: str
    created_at: str = Field(default_factory=utcnow_iso)
    repeats: int
    git_commit: str
    git_dirty: bool
    description: str = ""
    pipeline_config: dict[str, str | int | float | None] = Field(
        default_factory=dict,
        description="Run-level (not per-suite -- shared by every suite in a run), e.g. "
        "embedding_model, hnsw_space, vectorstore_chunk_count (from the vectorstore's own "
        "_ingestion_metadata.json, not re-derived from current code) plus llm_model/"
        "hyde_temperature (hyde mode only, read live from the running IncidentPilot).",
    )
    suites: dict[str, SuiteAggregate]
    summary: dict[str, MetricStats] = Field(
        description="Pooled across all suites' records (weighted by query "
        "count naturally, since pooling happens at the record level, not "
        "by averaging suite-level means)."
    )


class BaselineRecord(BenchmarkRun):
    """A BenchmarkRun that's been promoted. Written into
    eval/benchmarks/baselines/<category>/<mode>/ -- exactly one file per
    (category, mode) is ever named current_benchmark.json; every other file
    in that folder is a past baseline (a demoted current_benchmark.json,
    renamed to a timestamped filename when superseded).
    """

    baseline_id: str
    promoted_at: str = Field(default_factory=utcnow_iso)
    superseded_baseline_id: str | None = None
