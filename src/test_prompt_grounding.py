"""
Benchmark runner for the grounding/fabrication regression case found in
prior prompt-tuning sessions (see prompts/system_prompt.md history) --
tests whether ChatGroq's `temperature`/`reasoning_effort` params affect
grounding on:

  "checkout api is slow for the last 1 minute, what's the issue?" -- during
  pool-exhaustion plateau. Known failure mode: model takes real retrieved
  text (a fixed pool-size ceiling, a "Not started" postmortem backlog item)
  and recontextualizes it as an available live mitigation, or invents
  precise-sounding operational detail (dashboard paths, commands) with no
  basis in the retrieved chunks.

Each run: health-checks the supporting services, then repeats REPEATS times:
trigger a fresh pool incident (auto_resolve=False so it can't expire mid-
query), wait past the climbing phase, run the query, capture the full trace
(RAG chunks, tool calls with args/results, timings, response text), resolve
the incident, repeat. Writes one JSON file to eval/results/ per invocation,
labeled by the model config actually detected on IncidentPilot's ChatGroq
instance (not a hand-typed label -- avoids mislabeling a run).

No automated grounding score -- unlike eval/benchmark's precision/recall
metrics, judging whether a claim is fabricated requires checking it by hand
against the actual runbook/postmortem source text. This just captures
faithfully so that comparison can be done afterward, across two of these
JSON files.

Not a permanent regression suite -- ad hoc for this comparison.
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from pydantic import BaseModel

from logging_config import setup_logging
from incident_pilot import IncidentPilot

FLASK_API = "http://localhost:5001"
HEALTH_CHECKS = {
    "flask-generator": f"{FLASK_API}/health",
    "prometheus": "http://localhost:9090/-/ready",
    "loki": "http://localhost:3100/ready",
    "grafana": "http://admin:admin@localhost:3000/api/health",
}

QUERY = "checkout api is slow for the last 1 minute, what's the issue?"
REPEATS = 3
# POOL_CLIMBING_MINUTES=15 in flask-generator/config.py (accelerated mode,
# 1 tick = 1 real second) -- wait a bit past that so the query reliably
# lands in the plateau, not mid-climb.
CLIMB_WAIT_SECONDS = 20

RESULTS_DIR = Path(__file__).resolve().parent.parent / "eval" / "results"


class ToolCallRecord(BaseModel):
    name: str
    args: dict
    result: dict


class ChunkRecord(BaseModel):
    source: str
    section: str
    content: str


class TimingRecord(BaseModel):
    phase: str
    duration_ms: float


class RepeatResult(BaseModel):
    repeat_index: int
    query: str
    hyde_queries: list[str]
    chunks: list[ChunkRecord]
    tool_calls: list[ToolCallRecord]
    data_source: str
    timings: list[TimingRecord]
    response: str


class BenchmarkRun(BaseModel):
    started_at: str
    model_name: str
    temperature: float | None
    reasoning_effort: str | None
    repeats: list[RepeatResult]


def _preflight() -> None:
    for name, url in HEALTH_CHECKS.items():
        try:
            resp = requests.get(url, timeout=5)
            ok = resp.status_code == 200
        except requests.RequestException:
            ok = False
        if not ok:
            raise RuntimeError(
                f"Preflight failed: {name} ({url}) is not healthy -- "
                f"check `docker compose ps` before running this benchmark."
            )
    print("[preflight] flask-generator, prometheus, loki, grafana all healthy")


def _trigger_pool_incident() -> None:
    resp = requests.post(
        f"{FLASK_API}/api/incidents/pool/trigger",
        json={"service": "checkout-api", "auto_resolve": False},
        timeout=10,
    )
    resp.raise_for_status()


def _resolve_pool_incident() -> None:
    resp = requests.post(f"{FLASK_API}/api/incidents/pool/resolve", timeout=10)
    resp.raise_for_status()


def _run_once(pilot: IncidentPilot, repeat_index: int) -> RepeatResult:
    print(f"\n[repeat {repeat_index}] triggering pool incident for checkout-api...")
    _trigger_pool_incident()
    print(f"[repeat {repeat_index}] waiting {CLIMB_WAIT_SECONDS}s past the climbing phase...")
    time.sleep(CLIMB_WAIT_SECONDS)

    print(f"[repeat {repeat_index}] running query...")
    response = pilot.query(QUERY)
    trace = pilot.get_trace()

    print(f"[repeat {repeat_index}] resolving incident...")
    _resolve_pool_incident()

    return RepeatResult(
        repeat_index=repeat_index,
        query=QUERY,
        hyde_queries=trace.get("queries", []),
        chunks=[ChunkRecord(**c) for c in trace.get("chunks", [])],
        tool_calls=[ToolCallRecord(**t) for t in trace.get("tool_calls", [])],
        data_source=trace.get("source", "unknown"),
        timings=[TimingRecord(**t) for t in trace.get("timings", [])],
        response=response,
    )


def main() -> None:
    setup_logging()
    _preflight()

    pilot = IncidentPilot()
    try:
        # Auto-detect the actual config rather than trusting a hand-typed
        # label -- e.g. temperature=0 is stored/sent by ChatGroq as 1e-08,
        # not literal 0.
        temperature = getattr(pilot.model, "temperature", None)
        reasoning_effort = getattr(pilot.model, "reasoning_effort", None)
        print(f"[config] model={pilot._model_name} temperature={temperature} "
              f"reasoning_effort={reasoning_effort}")

        repeats = [_run_once(pilot, i) for i in range(1, REPEATS + 1)]
    finally:
        pilot.close()

    run = BenchmarkRun(
        started_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        model_name=pilot._model_name,
        temperature=temperature,
        reasoning_effort=reasoning_effort,
        repeats=repeats,
    )

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    label = f"reasoning_{reasoning_effort or 'default'}"
    out_path = RESULTS_DIR / f"grounding_benchmark_{label}_{int(time.time())}.json"
    out_path.write_text(run.model_dump_json(indent=2), encoding="utf-8")
    print(f"\n[saved] {out_path}")


if __name__ == "__main__":
    main()
