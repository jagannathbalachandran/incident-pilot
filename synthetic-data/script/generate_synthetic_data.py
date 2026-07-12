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

The week-long dataset (checkout-api-week-*) preserves the same guarantee with
the derivation arrow flipped: a bucket-state timeline is the single source of
truth, per-request CSV logs are sampled FROM it, and the metrics CSV is a pure
AGGREGATION of those logs (p99 / error rate) plus gauges copied from the same
timeline (active_connections, cache_hit_ratio). validate_week_alignment()
independently re-buckets the logs by timestamp and re-derives every metrics
row, so the two CSVs cannot disagree without the script failing.

Usage:
    python generate_synthetic_data.py
"""
import csv
import json
import math
import os
import random
from datetime import datetime, timedelta, timezone

# resolves to synthetic-data/ regardless of the caller's cwd
DATA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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

    metrics_path = os.path.join(DATA_DIR, "metrics", f"{service}-{label}-metrics.json")
    logs_path = os.path.join(DATA_DIR, "logs", f"{service}-{label}-app-logs.jsonl")

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


# ---------------------------------------------------------------------------
# Week-long per-request dataset (CSV logs + CSV metrics)
#
# Derivation is inverted relative to build_incident: the bucket-state timeline
# is the single source of truth, per-request logs are sampled from it, and the
# metrics are aggregated from the logs. validate_week_alignment() re-derives
# everything independently and asserts the two CSVs agree.
# ---------------------------------------------------------------------------

TS_FMT = "%Y-%m-%dT%H:%M:%SZ"

WEEK_SERVICE = "checkout-api"
WEEK_START = datetime(2026, 7, 4, 0, 0, tzinfo=timezone.utc)
BUCKET_MINUTES = 5
N_BUCKETS = 7 * 24 * (60 // BUCKET_MINUTES)  # 2016 five-minute buckets
BASE_REQUESTS_PER_BUCKET = 30  # mean requests per bucket before diurnal scaling

PODS = ["checkout-api-7c8d9", "checkout-api-5fd22", "checkout-api-91ab3"]
REGION = "ap-south-1"
USERS = [f"user-{i}" for i in range(1, 21)]
ENDPOINT = "POST /checkout"
TRACE_ID_BASE = 100_000
REQUEST_ID_BASE = 500_000

SUCCESS_MESSAGES = [
    ("Checkout completed successfully", 40),
    ("Payment authorized and order created", 25),
    ("Order confirmed", 20),
    ("Checkout request processed", 15),
]
# (status_code, message, weight, latency_lo_ms, latency_hi_ms)
BACKGROUND_ERRORS = [
    (400, "Invalid JSON payload", 14, 15, 70),
    (400, "Validation failed: paymentMethod missing", 8, 30, 90),
    (401, "JWT token expired", 8, 10, 40),
    (401, "Invalid bearer token", 5, 10, 40),
    (401, "Authorization header missing", 5, 10, 60),
    (409, "Inventory reservation conflict", 12, 100, 180),
    (500, "Database transaction rolled back", 8, 900, 1500),
    (500, "Unexpected exception in checkout pipeline", 5, 900, 1400),
    (500, "Unhandled NullPointer while creating order", 3, 900, 1400),
    (503, "fraud-scoring-svc unavailable", 4, 2000, 3100),
]
POOL_ERROR_MSG = "could not obtain connection from pool within 5000ms"
FRAUD_ERROR_MSG = "fraud-scoring-svc unavailable"
CACHE_WARN_MESSAGES = [
    "Redis cluster failover detected",
    "MOVED redirection error from cache node",
]

WEEK_INCIDENTS = [
    # Redis cache node failover: hit ratio steps down, warms back up; latency
    # step-change but no error spike (mirrors postmortem INC-4522).
    {
        "kind": "cache",
        "start": datetime(2026, 7, 5, 9, 15, tzinfo=timezone.utc),
        "recovery_start": datetime(2026, 7, 5, 9, 21, tzinfo=timezone.utc),
        "end": datetime(2026, 7, 5, 9, 33, tzinfo=timezone.utc),
        "floor_ratio": 0.41,
    },
    # fraud-scoring-svc outage: burst of 503s well over the 2% error alert.
    {
        "kind": "fraud",
        "start": datetime(2026, 7, 7, 9, 40, tzinfo=timezone.utc),
        "end": datetime(2026, 7, 7, 10, 10, tzinfo=timezone.utc),
    },
    # Postgres connection-pool exhaustion: gradual climb, plateau pinned at
    # max_connections, recovery (runbook Known Issue #1 / postmortem INC-4821).
    {
        "kind": "pool",
        "start": datetime(2026, 7, 9, 13, 30, tzinfo=timezone.utc),
        "plateau_start": datetime(2026, 7, 9, 13, 50, tzinfo=timezone.utc),
        "recovery_start": datetime(2026, 7, 9, 14, 10, tzinfo=timezone.utc),
        "end": datetime(2026, 7, 9, 14, 25, tzinfo=timezone.utc),
    },
]

LOG_FIELDS = [
    "timestamp", "level", "service", "trace_id", "request_id", "user_id",
    "endpoint", "status_code", "latency_ms", "message", "pod", "region",
]
METRIC_FIELDS = [
    "timestamp", "service", "p99_latency_ms", "error_rate_pct",
    "request_count", "error_count", "active_connections", "max_connections",
    "cache_hit_ratio",
]


def sample_poisson(lam, rng):
    """Knuth's algorithm; fine for the small lambdas used here (< ~60)."""
    threshold = math.exp(-lam)
    k, p = 0, 1.0
    while p > threshold:
        k += 1
        p *= rng.random()
    return k - 1


def percentile(sorted_vals, q):
    """Nearest-rank percentile. On small night-time buckets p99 collapses to
    the bucket max, which is fine for synthetic data."""
    if not sorted_vals:
        return 0
    return sorted_vals[max(0, math.ceil(q * len(sorted_vals)) - 1)]


def traffic_rate(t):
    """Diurnal request rate per bucket: trough ~03:00 UTC, peak ~14:30 UTC,
    slightly quieter on weekends."""
    hour = t.hour + t.minute / 60
    factor = 1.0 + 0.65 * math.sin(2 * math.pi * (hour - 8.5) / 24)
    if t.weekday() >= 5:
        factor *= 0.85
    return BASE_REQUESTS_PER_BUCKET * factor


def level_for(status_code, is_cache_warn=False):
    if is_cache_warn:
        return "WARN"
    if status_code >= 500:
        return "ERROR"
    if status_code >= 400:
        return "WARN"
    return "INFO"


def apply_incident_overlay(bucket, incident, t, rng):
    if incident["kind"] == "pool":
        if t < incident["plateau_start"]:
            progress = (t - incident["start"]) / (incident["plateau_start"] - incident["start"])
            bucket["phase"] = "pool_climb"
            conns = int(BASELINE_CONNECTIONS + progress * (MAX_CONNECTIONS - BASELINE_CONNECTIONS))
            bucket["success_median_ms"] *= 1 + progress * 4.2
        elif t < incident["recovery_start"]:
            bucket["phase"] = "pool_plateau"
            conns = MAX_CONNECTIONS
            bucket["success_median_ms"] *= 5.2
        else:
            progress = (t - incident["recovery_start"]) / (incident["end"] - incident["recovery_start"])
            bucket["phase"] = "pool_recovery"
            conns = int(MAX_CONNECTIONS - progress * (MAX_CONNECTIONS - BASELINE_CONNECTIONS))
            bucket["success_median_ms"] *= 1 + (1 - progress) * 4.2
        bucket["active_connections"] = conns
        # acquisition timeouts only appear once the pool is nearly exhausted,
        # so latency climbs before errors do (runbook Known Issue #1 shape)
        if conns >= 190:
            bucket["pool_error_pct"] = rng.uniform(4.0, 6.0) * (conns - 190) / (MAX_CONNECTIONS - 190)
    elif incident["kind"] == "fraud":
        bucket["phase"] = "fraud_outage"
        bucket["fraud_error_pct"] = rng.uniform(10.0, 15.0)
        bucket["success_median_ms"] *= 2.2
    elif incident["kind"] == "cache":
        if t < incident["recovery_start"]:
            hit = incident["floor_ratio"]
            bucket["phase"] = "cache_failover"
        else:
            progress = (t - incident["recovery_start"]) / (incident["end"] - incident["recovery_start"])
            hit = incident["floor_ratio"] + progress * (0.93 - incident["floor_ratio"])
            bucket["phase"] = "cache_warming"
        bucket["cache_hit_ratio"] = round(hit, 3)
        severity = (0.95 - hit) / (0.95 - incident["floor_ratio"])
        bucket["success_median_ms"] *= 1 + severity * 2.9
        if hit < 0.90:
            bucket["cache_warn_pct"] = 4.0


def build_week_state_timeline(week_start, n_buckets, rng):
    """The single source of truth for the week dataset: one state dict per
    5-minute bucket, carrying traffic rate, latency/error distributions, and
    the gauge values (connections, cache hit ratio)."""
    state = []
    for i in range(n_buckets):
        t = week_start + timedelta(minutes=i * BUCKET_MINUTES)
        bucket = {
            "bucket_start": t,
            "phase": "baseline",
            "expected_requests": traffic_rate(t),
            "success_median_ms": 240 + rng.uniform(-10, 10),
            "success_sigma": 0.20,
            "background_error_pct": rng.uniform(1.0, 2.5),
            "pool_error_pct": 0.0,
            "fraud_error_pct": 0.0,
            "cache_warn_pct": 0.0,
            "active_connections": BASELINE_CONNECTIONS + rng.randint(-5, 5),
            "cache_hit_ratio": round(min(0.99, 0.95 + rng.gauss(0, 0.008)), 3),
        }
        for incident in WEEK_INCIDENTS:
            if incident["start"] <= t < incident["end"]:
                apply_incident_overlay(bucket, incident, t, rng)
        state.append(bucket)
    return state


def sample_request(bucket, rng):
    """Returns (status_code, level, latency_ms, message) for one request,
    drawn from the bucket's state -- never from an independent process."""
    roll = rng.uniform(0, 100)
    if roll < bucket["pool_error_pct"]:
        # latency reflects the 5000ms pool acquire timeout from the May incident
        return 500, "ERROR", rng.randint(5000, 5400), POOL_ERROR_MSG
    roll -= bucket["pool_error_pct"]
    if roll < bucket["fraud_error_pct"]:
        return 503, "ERROR", rng.randint(2200, 3200), FRAUD_ERROR_MSG
    roll -= bucket["fraud_error_pct"]
    if roll < bucket["background_error_pct"]:
        status, message, _, lat_lo, lat_hi = rng.choices(
            BACKGROUND_ERRORS, weights=[e[2] for e in BACKGROUND_ERRORS]
        )[0]
        return status, level_for(status), rng.randint(lat_lo, lat_hi), message
    latency = int(rng.lognormvariate(math.log(bucket["success_median_ms"]), bucket["success_sigma"]))
    if bucket["cache_warn_pct"] and rng.uniform(0, 100) < bucket["cache_warn_pct"]:
        # request still succeeds, but logs the cache-failover symptom
        return 200, "WARN", latency, rng.choice(CACHE_WARN_MESSAGES)
    message = rng.choices(
        [m for m, _ in SUCCESS_MESSAGES], weights=[w for _, w in SUCCESS_MESSAGES]
    )[0]
    return 200, "INFO", latency, message


def generate_request_logs(state, rng):
    """One CSV row per request, sampled from the bucket states. Rows carry a
    private _bucket/_offset pair used for aggregation and deterministic
    ordering; the CSV writer drops them."""
    rows = []
    for idx, bucket in enumerate(state):
        n = sample_poisson(bucket["expected_requests"], rng)
        offsets = sorted(rng.uniform(0, BUCKET_MINUTES * 60) for _ in range(n))
        for offset in offsets:
            status, level, latency_ms, message = sample_request(bucket, rng)
            ts = bucket["bucket_start"] + timedelta(seconds=int(offset))
            rows.append({
                "timestamp": ts.strftime(TS_FMT),
                "level": level,
                "service": WEEK_SERVICE,
                "user_id": rng.choice(USERS),
                "endpoint": ENDPOINT,
                "status_code": status,
                "latency_ms": latency_ms,
                "message": message,
                "pod": rng.choice(PODS),
                "region": REGION,
                "_bucket": idx,
                "_offset": offset,
            })
    # float offset breaks same-second ties so trace/request IDs are sequential
    # in a deterministic time order, like the sample CSV
    rows.sort(key=lambda r: (r["_bucket"], r["_offset"]))
    for i, row in enumerate(rows):
        row["trace_id"] = f"tr-{TRACE_ID_BASE + i}"
        row["request_id"] = f"req-{REQUEST_ID_BASE + i}"
    return rows


def aggregate_week_metrics(state, logs):
    """Metrics are a pure aggregation of the logs (counts, error rate, p99)
    plus gauges copied from the same state timeline the logs came from."""
    per_bucket = [[] for _ in state]
    for row in logs:
        per_bucket[row["_bucket"]].append(row)

    metrics = []
    for bucket, rows in zip(state, per_bucket):
        count = len(rows)
        error_count = sum(1 for r in rows if r["status_code"] >= 400)
        # p99 over successful requests only: pool-timeout errors sit at ~5000ms
        # and would pin an all-request p99 there once their share passes 1%,
        # hiding the gradual-climb signal the runbook describes
        success_latencies = sorted(r["latency_ms"] for r in rows if r["status_code"] == 200)
        metrics.append({
            "timestamp": bucket["bucket_start"].strftime(TS_FMT),
            "service": WEEK_SERVICE,
            "p99_latency_ms": percentile(success_latencies, 0.99),
            "error_rate_pct": round(100 * error_count / count, 3) if count else 0.0,
            "request_count": count,
            "error_count": error_count,
            "active_connections": bucket["active_connections"],
            "max_connections": MAX_CONNECTIONS,
            "cache_hit_ratio": bucket["cache_hit_ratio"],
        })
    return metrics


def validate_week_alignment(state, logs, metrics):
    """Independently re-buckets the logs from their timestamp strings and
    re-derives every metrics row, then checks the incident narrative actually
    made it into the data (alert thresholds crossed, gauges consistent)."""
    errors = []
    bucket_seconds = BUCKET_MINUTES * 60
    recomputed = [[] for _ in state]

    for row in logs:
        ts = datetime.strptime(row["timestamp"], TS_FMT).replace(tzinfo=timezone.utc)
        idx = int((ts - WEEK_START).total_seconds() // bucket_seconds)
        if not 0 <= idx < len(state):
            errors.append(f"log at {row['timestamp']} falls outside the week window")
            continue
        recomputed[idx].append(row)
        bucket = state[idx]
        if row["message"] == POOL_ERROR_MSG and bucket["active_connections"] < 190:
            errors.append(
                f"{row['timestamp']}: pool-timeout log but active_connections="
                f"{bucket['active_connections']} (< 190)"
            )
        if row["message"] in CACHE_WARN_MESSAGES and bucket["cache_hit_ratio"] >= 0.90:
            errors.append(
                f"{row['timestamp']}: cache-failover log but cache_hit_ratio="
                f"{bucket['cache_hit_ratio']} (>= 0.90)"
            )

    for m, rows in zip(metrics, recomputed):
        count = len(rows)
        error_count = sum(1 for r in rows if r["status_code"] >= 400)
        success_latencies = sorted(r["latency_ms"] for r in rows if r["status_code"] == 200)
        expected = {
            "p99_latency_ms": percentile(success_latencies, 0.99),
            "error_rate_pct": round(100 * error_count / count, 3) if count else 0.0,
            "request_count": count,
            "error_count": error_count,
        }
        for key, value in expected.items():
            if m[key] != value:
                errors.append(f"{m['timestamp']}: metrics {key}={m[key]} != re-derived {value}")

    def metric_rows_in(start, end):
        return [
            m for m in metrics
            if start <= datetime.strptime(m["timestamp"], TS_FMT).replace(tzinfo=timezone.utc) < end
        ]

    def log_rows_in(start, end):
        out = []
        for rows in recomputed:
            out.extend(rows)
        return [
            r for r in out
            if start <= datetime.strptime(r["timestamp"], TS_FMT).replace(tzinfo=timezone.utc) < end
        ]

    for incident in WEEK_INCIDENTS:
        if incident["kind"] == "pool":
            plateau = metric_rows_in(incident["plateau_start"], incident["recovery_start"])
            if len(plateau) < 3 or any(m["p99_latency_ms"] <= 1500 for m in plateau):
                errors.append("pool incident: plateau lacks >=3 consecutive buckets with p99 > 1500ms")
            if any(m["active_connections"] != MAX_CONNECTIONS for m in plateau):
                errors.append("pool incident: plateau bucket not pinned at max_connections")
        elif incident["kind"] == "fraud":
            window = metric_rows_in(incident["start"], incident["end"])
            if sum(1 for m in window if m["error_rate_pct"] > 2.0) < 3:
                errors.append("fraud incident: fewer than 3 buckets over the 2% error-rate alert")
            fraud_503s = [
                r for r in log_rows_in(incident["start"], incident["end"])
                if r["status_code"] == 503 and r["message"] == FRAUD_ERROR_MSG
            ]
            if len(fraud_503s) < 15:
                errors.append(f"fraud incident: only {len(fraud_503s)} fraud 503 logs (expected >= 15)")
        elif incident["kind"] == "cache":
            window = metric_rows_in(incident["start"], incident["end"])
            floor = min(m["cache_hit_ratio"] for m in window)
            if not 0.38 <= floor <= 0.45:
                errors.append(f"cache incident: hit-ratio floor {floor} outside [0.38, 0.45]")
            total = sum(m["request_count"] for m in window)
            errs = sum(m["error_count"] for m in window)
            if total and 100 * errs / total > 3.5:
                errors.append("cache incident: error rate spiked (should stay at background level)")
            warns = [r for r in log_rows_in(incident["start"], incident["end"])
                     if r["message"] in CACHE_WARN_MESSAGES]
            if len(warns) < 2:
                errors.append(f"cache incident: only {len(warns)} cache WARN logs (expected >= 2)")

    if not 20_000 <= len(logs) <= 80_000:
        errors.append(f"total log rows {len(logs)} outside the 20k-80k target")
    if len(metrics) != N_BUCKETS:
        errors.append(f"{len(metrics)} metrics rows != {N_BUCKETS} buckets")
    if any(m["request_count"] == 0 for m in metrics):
        errors.append("empty metrics bucket (request_count == 0)")

    if errors:
        raise AssertionError(f"Week alignment check FAILED ({len(errors)} issues):\n" + "\n".join(errors))
    print(f"  Alignment check PASSED: {len(logs)} log rows re-aggregate exactly into {len(metrics)} metrics buckets.")


def write_csv(path, rows, fieldnames):
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_week_dataset():
    # dedicated RNG so the week dataset never consumes the global stream,
    # keeping the existing per-incident outputs byte-identical
    rng = random.Random(1337)
    state = build_week_state_timeline(WEEK_START, N_BUCKETS, rng)
    logs = generate_request_logs(state, rng)
    metrics = aggregate_week_metrics(state, logs)
    validate_week_alignment(state, logs, metrics)

    logs_path = os.path.join(DATA_DIR, "logs", f"{WEEK_SERVICE}-week-app-logs.csv")
    metrics_path = os.path.join(DATA_DIR, "metrics", f"{WEEK_SERVICE}-week-metrics.csv")
    write_csv(logs_path, logs, LOG_FIELDS)
    write_csv(metrics_path, metrics, METRIC_FIELDS)
    print(f"  Wrote {metrics_path} ({len(metrics)} buckets) and {logs_path} ({len(logs)} requests)")
    return logs, metrics


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

    print("\nWeek-long dataset (2026-07-04 .. 2026-07-10, 3 incidents):")
    build_week_dataset()
