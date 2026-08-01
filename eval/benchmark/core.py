"""Benchmark orchestration: run, compare, promote.

Storage layout (see eval/benchmark's plan for the full rationale):

    eval/benchmarks/runs/<category>/<mode>/<timestamp>.json
        -- every run ever executed, promoted or not.
    eval/benchmarks/baselines/<category>/<mode>/
        current_benchmark.json       -- the ONE active baseline
        <timestamp>__<slug>.json     -- past baselines (demoted on supersession)
"""

from __future__ import annotations

import re
import statistics
import subprocess
import uuid
from pathlib import Path

from models import (
    BaselineRecord,
    BenchmarkRun,
    MetricStats,
    QueryMetricRecord,
    SuiteAggregate,
    SuiteRunResult,
    utcnow_iso,
)
from registry import pipeline_config_for, suites_for

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BENCHMARKS_ROOT = REPO_ROOT / "eval" / "benchmarks"
RUNS_ROOT = BENCHMARKS_ROOT / "runs"
BASELINES_ROOT = BENCHMARKS_ROOT / "baselines"

CURRENT_BASELINE_NAME = "current_benchmark.json"


# ---------------------------------------------------------------------------
# git metadata
# ---------------------------------------------------------------------------

def _git_commit() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def _git_dirty() -> bool:
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout
    return bool(status.strip())


def _slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len] or "untitled"


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

def run_benchmark(category: str, mode: str, repeats: int = 1, description: str = "") -> BenchmarkRun:
    specs = suites_for(category, mode)
    if not specs:
        raise ValueError(f"No suites registered for category={category!r} mode={mode!r}")

    suite_aggregates: dict[str, SuiteAggregate] = {}
    # pooled_by_repeat[r] = list of QueryMetricRecord across all suites for repeat r
    pooled_by_repeat: list[list[QueryMetricRecord]] = [[] for _ in range(repeats)]
    pipeline_config: dict | None = None

    for suite_name, spec in specs.items():
        ctx = spec.setup()
        try:
            if pipeline_config is None:
                # Same for every suite in a run (same vectorstore, same LLM
                # config) -- captured once, from whichever suite runs first.
                pipeline_config = pipeline_config_for(mode, ctx)

            repeat_results: list[SuiteRunResult] = []
            for r in range(repeats):
                records = []
                for qrel in spec.qrels:
                    result = spec.evaluate_one(ctx, qrel)
                    metrics = {
                        "precision": result.precision,
                        "recall": result.recall,
                        "reciprocal_rank": result.reciprocal_rank,
                    }
                    records.append(QueryMetricRecord(
                        query=result.query,
                        identifier=result.expected_source,
                        metrics=metrics,
                    ))
                summary_metrics = _mean_metrics(records)
                repeat_results.append(SuiteRunResult(records=records, summary_metrics=summary_metrics))
                pooled_by_repeat[r].extend(records)
        finally:
            spec.teardown(ctx)

        aggregate_metrics = _aggregate_across_repeats([rr.summary_metrics for rr in repeat_results])
        per_query_aggregate = _per_query_aggregate(repeat_results)

        suite_aggregates[suite_name] = SuiteAggregate(
            suite_name=suite_name,
            n_queries=len(spec.qrels),
            retrieval_config=spec.retrieval_config or {},
            repeats=repeat_results,
            aggregate_metrics=aggregate_metrics,
            per_query_aggregate=per_query_aggregate,
        )

    pooled_repeat_summaries = [_mean_metrics(records) for records in pooled_by_repeat]
    summary = _aggregate_across_repeats(pooled_repeat_summaries)

    run = BenchmarkRun(
        run_id=f"{category}__{mode}__{uuid.uuid4().hex[:8]}",
        category=category,
        mode=mode,
        repeats=repeats,
        git_commit=_git_commit(),
        git_dirty=_git_dirty(),
        description=description,
        pipeline_config=pipeline_config or {},
        suites=suite_aggregates,
        summary=summary,
    )

    out_dir = RUNS_ROOT / category / mode
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{run.created_at.replace(':', '').replace('-', '')}__{uuid.uuid4().hex[:6]}.json"
    out_path.write_text(run.model_dump_json(indent=2))
    print(f"Run saved to {out_path}")

    return run


def _mean_metrics(records: list[QueryMetricRecord]) -> dict[str, float]:
    if not records:
        return {}
    keys = records[0].metrics.keys()
    return {k: statistics.fmean(r.metrics[k] for r in records) for k in keys}


def _aggregate_across_repeats(per_repeat_summaries: list[dict[str, float]]) -> dict[str, MetricStats]:
    if not per_repeat_summaries:
        return {}
    keys = per_repeat_summaries[0].keys()
    return {k: MetricStats.from_values([s[k] for s in per_repeat_summaries]) for k in keys}


def _per_query_aggregate(repeat_results: list[SuiteRunResult]) -> dict[str, dict[str, MetricStats]]:
    by_query: dict[str, dict[str, list[float]]] = {}
    for rr in repeat_results:
        for rec in rr.records:
            bucket = by_query.setdefault(rec.query, {})
            for metric_key, value in rec.metrics.items():
                bucket.setdefault(metric_key, []).append(value)
    return {
        query: {metric_key: MetricStats.from_values(values) for metric_key, values in metrics.items()}
        for query, metrics in by_query.items()
    }


# ---------------------------------------------------------------------------
# baseline load / list
# ---------------------------------------------------------------------------

def load_current_baseline(category: str, mode: str) -> BaselineRecord | None:
    path = BASELINES_ROOT / category / mode / CURRENT_BASELINE_NAME
    if not path.exists():
        return None
    return BaselineRecord.model_validate_json(path.read_text())


def list_baselines(category: str, mode: str) -> list[tuple[str, BaselineRecord]]:
    """Every baseline file for (category, mode), (filename, record), oldest first."""
    directory = BASELINES_ROOT / category / mode
    if not directory.exists():
        return []
    entries = []
    for path in sorted(directory.glob("*.json")):
        entries.append((path.name, BaselineRecord.model_validate_json(path.read_text())))
    entries.sort(key=lambda pair: pair[1].promoted_at)
    return entries


# ---------------------------------------------------------------------------
# promote
# ---------------------------------------------------------------------------

def promote(run: BenchmarkRun, description: str = "") -> BaselineRecord:
    baseline_dir = BASELINES_ROOT / run.category / run.mode
    baseline_dir.mkdir(parents=True, exist_ok=True)
    current_path = baseline_dir / CURRENT_BASELINE_NAME

    superseded_id = None
    if current_path.exists():
        old = BaselineRecord.model_validate_json(current_path.read_text())
        superseded_id = old.baseline_id
        demoted_name = f"{old.promoted_at.replace(':', '').replace('-', '')}__{_slugify(old.description)}.json"
        current_path.rename(baseline_dir / demoted_name)
        print(f"Demoted previous baseline to {baseline_dir / demoted_name}")

    baseline = BaselineRecord(
        **run.model_dump(),
        baseline_id=f"baseline__{run.category}__{run.mode}__{uuid.uuid4().hex[:8]}",
        superseded_baseline_id=superseded_id,
    )
    if description:
        baseline.description = description
    current_path.write_text(baseline.model_dump_json(indent=2))
    print(f"Promoted new baseline: {current_path}")
    return baseline


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------

def compare(baseline: BaselineRecord, new_run: BenchmarkRun) -> dict:
    """Per-suite and pooled-summary comparison, matched by query string.
    Only overlapping queries contribute to deltas; new/missing queries are
    reported separately, never silently dropped or silently included.
    """
    pipeline_config_mismatch = (
        baseline.pipeline_config != new_run.pipeline_config
        and (baseline.pipeline_config or new_run.pipeline_config)
    )
    report: dict = {
        "suites": {},
        "summary": None,
        "pipeline_config_mismatch": pipeline_config_mismatch,
        "baseline_pipeline_config": baseline.pipeline_config,
        "new_pipeline_config": new_run.pipeline_config,
    }

    for suite_name, new_suite in new_run.suites.items():
        if suite_name not in baseline.suites:
            report["suites"][suite_name] = {"status": "new_suite", "message": "no baseline to compare against"}
            continue

        base_suite = baseline.suites[suite_name]
        config_mismatch = (
            base_suite.retrieval_config != new_suite.retrieval_config
            and (base_suite.retrieval_config or new_suite.retrieval_config)
        )

        base_queries = set(base_suite.per_query_aggregate.keys())
        new_queries = set(new_suite.per_query_aggregate.keys())
        overlap = base_queries & new_queries

        if not overlap:
            report["suites"][suite_name] = {
                "status": "no_overlap",
                "new_only": sorted(new_queries - base_queries),
                "missing_only": sorted(base_queries - new_queries),
                "config_mismatch": config_mismatch,
                "baseline_config": base_suite.retrieval_config,
                "new_config": new_suite.retrieval_config,
            }
            continue

        metric_keys = next(iter(new_suite.per_query_aggregate.values())).keys()
        deltas = {}
        for metric_key in metric_keys:
            base_vals = [base_suite.per_query_aggregate[q][metric_key].mean for q in overlap]
            new_vals = [new_suite.per_query_aggregate[q][metric_key].mean for q in overlap]
            base_mean = statistics.fmean(base_vals)
            new_mean = statistics.fmean(new_vals)
            deltas[metric_key] = {
                "baseline": base_mean,
                "new": new_mean,
                "delta": new_mean - base_mean,
            }

        report["suites"][suite_name] = {
            "status": "compared",
            "n_overlap": len(overlap),
            "n_new_only": len(new_queries - base_queries),
            "n_missing_only": len(base_queries - new_queries),
            "new_only": sorted(new_queries - base_queries),
            "missing_only": sorted(base_queries - new_queries),
            "deltas": deltas,
            "config_mismatch": config_mismatch,
            "baseline_config": base_suite.retrieval_config,
            "new_config": new_suite.retrieval_config,
        }

    # Pooled summary: only over suites that were actually compared, pooling
    # their overlapping-query values (still weighted naturally by query count).
    compared = {name: r for name, r in report["suites"].items() if r["status"] == "compared"}
    if compared:
        metric_keys = next(iter(compared.values()))["deltas"].keys()
        summary_deltas = {}
        for metric_key in metric_keys:
            base_mean = statistics.fmean(r["deltas"][metric_key]["baseline"] for r in compared.values())
            new_mean = statistics.fmean(r["deltas"][metric_key]["new"] for r in compared.values())
            summary_deltas[metric_key] = {"baseline": base_mean, "new": new_mean, "delta": new_mean - base_mean}
        report["summary"] = summary_deltas

    return report


def print_comparison(report: dict) -> None:
    print("\n=== Comparison vs. current baseline ===")
    if report.get("pipeline_config_mismatch"):
        print("!! PIPELINE CONFIG CHANGED vs. baseline (embedding model / hnsw_space / llm_model / "
              "vectorstore not re-ingested since / etc.) -- deltas below are NOT apples-to-apples !!")
        print(f"   baseline: {report['baseline_pipeline_config']}")
        print(f"   new:      {report['new_pipeline_config']}")
    for suite_name, r in report["suites"].items():
        print(f"\n[{suite_name}]")
        if r["status"] == "new_suite":
            print("  new suite -- no baseline to compare against")
            continue
        if r.get("config_mismatch"):
            print(f"  !! RETRIEVAL CONFIG CHANGED vs. baseline -- deltas below are NOT apples-to-apples !!")
            print(f"     baseline: {r['baseline_config']}   new: {r['new_config']}")
        if r["status"] == "no_overlap":
            print(f"  no overlapping queries (new: {len(r['new_only'])}, missing: {len(r['missing_only'])})")
            continue
        for metric_key, d in r["deltas"].items():
            arrow = "up" if d["delta"] > 1e-9 else ("down" if d["delta"] < -1e-9 else "flat")
            print(f"  {metric_key:<18} baseline={d['baseline']:.3f}  new={d['new']:.3f}  delta={d['delta']:+.3f} ({arrow})")
        if r["n_new_only"]:
            print(f"  + {r['n_new_only']} new quer{'y' if r['n_new_only']==1 else 'ies'} not in baseline (not compared): {r['new_only']}")
        if r["n_missing_only"]:
            print(f"  - {r['n_missing_only']} quer{'y' if r['n_missing_only']==1 else 'ies'} in baseline missing from this run (not compared): {r['missing_only']}")

    if report["summary"]:
        print("\n[pooled summary, compared suites only]")
        for metric_key, d in report["summary"].items():
            arrow = "up" if d["delta"] > 1e-9 else ("down" if d["delta"] < -1e-9 else "flat")
            print(f"  {metric_key:<18} baseline={d['baseline']:.3f}  new={d['new']:.3f}  delta={d['delta']:+.3f} ({arrow})")


def print_run_report(run: BenchmarkRun) -> None:
    print(f"\n=== Benchmark run: {run.category}/{run.mode} (repeats={run.repeats}) ===")
    print(f"git_commit={run.git_commit[:12]} dirty={run.git_dirty}")
    print(f"pipeline_config={run.pipeline_config}")
    for suite_name, suite in run.suites.items():
        print(f"\n[{suite_name}] n_queries={suite.n_queries} retrieval_config={suite.retrieval_config}")
        for metric_key, stats in suite.aggregate_metrics.items():
            print(f"  {metric_key:<18} mean={stats.mean:.3f} std={stats.std:.3f} min={stats.min:.3f} max={stats.max:.3f}")
    print("\n[summary]")
    for metric_key, stats in run.summary.items():
        print(f"  {metric_key:<18} mean={stats.mean:.3f} std={stats.std:.3f} min={stats.min:.3f} max={stats.max:.3f}")
