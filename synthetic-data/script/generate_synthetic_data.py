"""
generate_synthetic_data.py

Generates the metrics file and the app-logs file for a synthetic incident, and
GUARANTEES they stay aligned by construction: app logs are never generated
independently of the metrics series -- every log line is derived FROM a
specific metrics point, so a log line's stated values (e.g. active_connections
in a pool-exhaustion error) always exactly match the metrics point at that
same timestamp. There is no separate random process for logs that could drift
out of sync with the metrics.

A validate_alignment() check runs at the end and asserts this -- if you ever
change the log-derivation logic and accidentally introduce a mismatch, this
script will fail loudly instead of silently producing inconsistent data.

Usage:
    python generate_synthetic_data.py
"""
import json
import random
from datetime import datetime, timedelta, timezone

random.seed(42)  # reproducible synthetic data

MAX_CONNECTIONS = 200
BASELINE_LATENCY_MS = 380
BASELINE_ERROR_PCT = 0.05
BASELINE_CONNECTIONS = 118


def generate_metrics_series(
    service: str,
    start: datetime,
    total_minutes: int,
    incident_start_offset_min: int,
    incident_peak_offset_min: int,
    incident_end_offset_min: int | None,  # None if still ongoing
    interval_min: int = 1,
):
    """The single source of truth. Every other artifact (logs) is derived
    from this list -- nothing downstream invents its own timestamps or
    values independently."""
    points = []
    n_steps = total_minutes // interval_min

    for i in range(n_steps + 1):
        t = start + timedelta(minutes=i * interval_min)
        mins_in = i * interval_min

        if mins_in < incident_start_offset_min:
            phase = "baseline"
        elif mins_in < incident_peak_offset_min:
            phase = "climbing"
        elif incident_end_offset_min is None or mins_in < incident_end_offset_min:
            phase = "plateau"
        else:
            phase = "recovering"

        if phase == "baseline":
            latency = BASELINE_LATENCY_MS + random.gauss(0, 15)
            error_rate = max(0, BASELINE_ERROR_PCT + random.gauss(0, 0.02))
            conns = BASELINE_CONNECTIONS + random.randint(-5, 5)
        elif phase == "climbing":
            progress = (mins_in - incident_start_offset_min) / max(
                1, (incident_peak_offset_min - incident_start_offset_min)
            )
            latency = BASELINE_LATENCY_MS + progress * 1450 + random.gauss(0, 40)
            error_rate = BASELINE_ERROR_PCT + progress * 5.8 + random.gauss(0, 0.15)
            conns = min(MAX_CONNECTIONS, int(BASELINE_CONNECTIONS + progress * (MAX_CONNECTIONS - BASELINE_CONNECTIONS)))
        elif phase == "plateau":
            latency = 1780 + random.gauss(0, 60)
            error_rate = max(0, 6.1 + random.gauss(0, 0.3))
            conns = MAX_CONNECTIONS
        else:  # recovering
            progress = (mins_in - incident_end_offset_min) / max(1, (total_minutes - incident_end_offset_min))
            latency = 1780 - progress * 1400 + random.gauss(0, 30)
            error_rate = max(0, 6.1 - progress * 6.0 + random.gauss(0, 0.1))
            conns = int(MAX_CONNECTIONS - progress * (MAX_CONNECTIONS - BASELINE_CONNECTIONS))

        points.append({
            "timestamp": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "service": service,
            "p99_latency_ms": round(max(0, latency), 1),
            "error_rate_pct": round(max(0, error_rate), 3),
            "active_connections": max(0, conns),
            "max_connections": MAX_CONNECTIONS,
            "cache_hit_ratio": round(0.95 + random.gauss(0, 0.01), 3),
            "phase": phase,  # dataset-layer only -- strip before query_logs returns this to the agent
        })
    return points


def derive_app_logs(metrics_series, service):
    """Derives log lines FROM the metrics series -- each log line's values
    are read directly off the metrics point at that timestamp, not generated
    independently. This is what guarantees alignment: a log line can never
    claim active_connections=200 at a timestamp where the metrics say 118,
    because the log line's value literally comes from the metrics point."""
    logs = []
    for p in metrics_series:
        if p["phase"] in ("climbing", "plateau") and p["active_connections"] >= 190:
            logs.append({
                "timestamp": p["timestamp"],
                "service": service,
                "level": "ERROR",
                "message": "could not obtain connection from pool within 5000ms",
                "active_connections": p["active_connections"],   # <- read from p, not re-rolled
                "max_connections": p["max_connections"],          # <- read from p, not re-rolled
            })
        if p["phase"] == "plateau" and random.random() < 0.3:
            logs.append({
                "timestamp": p["timestamp"],
                "service": service,
                "level": "WARN",
                "message": "request exceeded p99 SLO threshold (1500ms)",
                "p99_latency_ms": p["p99_latency_ms"],            # <- read from p, not re-rolled
            })
    return logs


def validate_alignment(metrics_series, logs):
    """Asserts every log line's values match the metrics point at the same
    timestamp -- proves the two files agree rather than just hoping they do."""
    by_ts = {p["timestamp"]: p for p in metrics_series}
    errors = []

    for line in logs:
        ts = line["timestamp"]
        if ts not in by_ts:
            errors.append(f"log at {ts} has no matching metrics timestamp")
            continue
        m = by_ts[ts]
        if "active_connections" in line and line["active_connections"] != m["active_connections"]:
            errors.append(f"{ts}: log active_connections={line['active_connections']} != metrics {m['active_connections']}")
        if "p99_latency_ms" in line and line["p99_latency_ms"] != m["p99_latency_ms"]:
            errors.append(f"{ts}: log p99_latency_ms={line['p99_latency_ms']} != metrics {m['p99_latency_ms']}")
        if line["level"] == "ERROR" and m["active_connections"] < 190:
            errors.append(f"{ts}: ERROR log present but metrics show active_connections={m['active_connections']} (< 190 threshold)")
        if line["level"] == "WARN" and m["p99_latency_ms"] <= 1500:
            errors.append(f"{ts}: WARN log present but metrics show p99_latency_ms={m['p99_latency_ms']} (<= 1500 threshold)")

    if errors:
        raise AssertionError(f"Alignment check FAILED ({len(errors)} issues):\n" + "\n".join(errors))
    print(f"  Alignment check PASSED: all {len(logs)} log lines match their metrics timestamp exactly.")


def build_incident(service, label, start, total_minutes, incident_start, incident_peak, incident_end,
                    incident_status, related_postmortem):
    metrics = generate_metrics_series(service, start, total_minutes, incident_start, incident_peak, incident_end)
    logs = derive_app_logs(metrics, service)
    validate_alignment(metrics, logs)

    metrics_path = f"metrics/{service}-{label}-metrics.json"
    logs_path = f"logs/{service}-{label}-app-logs.jsonl"

    with open(metrics_path, "w") as f:
        json.dump({
            "service": service,
            "incident_status": incident_status,
            "related_postmortem": related_postmortem,
            "series": metrics,
        }, f, indent=2)

    with open(logs_path, "w") as f:
        for line in logs:
            f.write(json.dumps(line) + "\n")

    print(f"  Wrote {metrics_path} ({len(metrics)} points) and {logs_path} ({len(logs)} lines)")
    return metrics, logs


if __name__ == "__main__":
    print("Past incident (2026-05-14, resolved):")
    build_incident(
        service="checkout-api", label="2026-05-14",
        start=datetime(2026, 5, 14, 13, 50, tzinfo=timezone.utc),
        total_minutes=70, incident_start=12, incident_peak=27, incident_end=48,
        incident_status="resolved",
        related_postmortem="postmortems/2026-05-checkout-outage.md",
    )

    print("\nCurrent incident (ongoing, relative to now):")
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    build_incident(
        service="checkout-api", label="current",
        start=now - timedelta(minutes=25),
        total_minutes=25, incident_start=8, incident_peak=20, incident_end=None,
        incident_status="ongoing",
        related_postmortem=None,
    )
