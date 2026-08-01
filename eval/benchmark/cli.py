"""CLI for the benchmark/baseline tracking system.

Usage:
    uv run python eval/benchmark/cli.py run --category rag_retrieval --mode no_hyde --repeats 1
    uv run python eval/benchmark/cli.py run --category rag_retrieval --mode hyde --repeats 1 --promote
    uv run python eval/benchmark/cli.py history --category rag_retrieval --mode hyde

`run` always prints a report, and auto-compares against the current baseline
if one exists. It never promotes silently -- pass --promote to promote the
run that was just executed after reviewing its report/comparison.
"""

import argparse

from core import (
    compare,
    list_baselines,
    load_current_baseline,
    print_comparison,
    print_run_report,
    promote,
    run_benchmark,
)


def cmd_run(args: argparse.Namespace) -> None:
    run = run_benchmark(
        args.category, args.mode, repeats=args.repeats, description=args.description, suites=args.suite
    )
    print_run_report(run)

    baseline = load_current_baseline(args.category, args.mode)
    if baseline is None:
        print("\nNo current baseline for this (category, mode) yet.")
    else:
        report = compare(baseline, run)
        print_comparison(report)

    if args.promote:
        promote(run, description=args.description)
    elif baseline is None:
        print("\nNo baseline exists yet -- re-run with --promote once you've reviewed this, "
              "to set it as the first baseline.")
    else:
        print("\nRun NOT promoted. Re-run with --promote once you've reviewed the comparison above.")


def cmd_promote_last(args: argparse.Namespace) -> None:
    # Promotes the most recently written run file for this (category, mode).
    from core import RUNS_ROOT
    from models import BenchmarkRun

    run_dir = RUNS_ROOT / args.category / args.mode
    files = sorted(run_dir.glob("*.json")) if run_dir.exists() else []
    if not files:
        raise SystemExit(f"No runs found for {args.category}/{args.mode}")
    latest = files[-1]
    run = BenchmarkRun.model_validate_json(latest.read_text())
    promote(run, description=args.description)


def cmd_history(args: argparse.Namespace) -> None:
    entries = list_baselines(args.category, args.mode)
    if not entries:
        print(f"No baselines for {args.category}/{args.mode} yet.")
        return
    for filename, record in entries:
        marker = "CURRENT" if filename == "current_benchmark.json" else "past"
        print(f"[{marker:7}] {record.promoted_at}  {filename}  git={record.git_commit[:12]}  "
              f"repeats={record.repeats}  -- {record.description}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark/baseline tracking CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Run a benchmark and optionally promote it")
    p_run.add_argument("--category", required=True)
    p_run.add_argument("--mode", required=True)
    p_run.add_argument("--repeats", type=int, default=1)
    p_run.add_argument("--description", default="")
    p_run.add_argument("--promote", action="store_true", help="Promote this run as the new baseline")
    p_run.add_argument("--suite", action="append", default=None,
                        help="Limit to specific suite(s), e.g. --suite synthetic_robustness_other_services. "
                             "Repeatable. Omit to run every suite registered for this (category, mode).")
    p_run.set_defaults(func=cmd_run)

    p_promote = sub.add_parser("promote", help="Promote the most recent run as the new baseline")
    p_promote.add_argument("--category", required=True)
    p_promote.add_argument("--mode", required=True)
    p_promote.add_argument("--description", default="")
    p_promote.set_defaults(func=cmd_promote_last)

    p_history = sub.add_parser("history", help="List all baselines for a (category, mode)")
    p_history.add_argument("--category", required=True)
    p_history.add_argument("--mode", required=True)
    p_history.set_defaults(func=cmd_history)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
