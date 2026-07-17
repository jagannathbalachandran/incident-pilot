"""
metrics_exporter.py

Defines all Prometheus metrics (gauges, counters, histograms) and the
update_all() function that syncs the current ScenarioState into Prometheus
gauge values every tick.

The `phase` field is deliberately NOT exposed as a Prometheus label so the
IncidentPilot agent must infer the incident stage from metric shapes.
"""

import logging

from prometheus_client import Gauge, Counter, Histogram, CollectorRegistry, generate_latest, CONTENT_TYPE_LATEST

from config import ScenarioState, SERVICE, MAX_CONNECTIONS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Collector Registry
# ---------------------------------------------------------------------------

REGISTRY = CollectorRegistry()

# ---------------------------------------------------------------------------
# Gauges (instantaneous values)
# ---------------------------------------------------------------------------

p99_latency = Gauge(
    "checkout_p99_latency_ms",
    "p99 request latency in milliseconds",
    ["service"],
    registry=REGISTRY,
)

error_rate = Gauge(
    "checkout_error_rate_pct",
    "Request error rate in percent",
    ["service"],
    registry=REGISTRY,
)

active_connections = Gauge(
    "checkout_active_connections",
    "Current active database connections",
    ["service"],
    registry=REGISTRY,
)

max_connections = Gauge(
    "checkout_max_connections",
    "Maximum database connections",
    ["service"],
    registry=REGISTRY,
)

cache_hit_ratio = Gauge(
    "checkout_cache_hit_ratio",
    "Redis cache hit ratio",
    ["service"],
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# Counters (cumulative, only increase)
# ---------------------------------------------------------------------------

errors_total = Counter(
    "checkout_errors_total",
    "Total errors by type",
    ["service", "error_type"],
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# Histograms (latency distribution)
# ---------------------------------------------------------------------------

request_duration_ms = Histogram(
    "checkout_request_duration_ms",
    "Request duration in milliseconds",
    ["service", "endpoint"],
    buckets=(100, 250, 500, 1000, 1500, 2000, 3000, 5000, 10000),
    registry=REGISTRY,
)


def update_all(state: ScenarioState) -> None:
    """Sync all Gauges from the current scenario state.

    Called every tick by the background loop.
    """
    svc = SERVICE

    p99_latency.labels(service=svc).set(state.p99_latency_ms)
    error_rate.labels(service=svc).set(state.error_rate_pct)
    active_connections.labels(service=svc).set(state.active_connections)
    max_connections.labels(service=svc).set(MAX_CONNECTIONS)
    cache_hit_ratio.labels(service=svc).set(state.cache_hit_ratio)

    # Increment error counter if pool errors are firing
    if state.pool_error_pct > 0:
        errors_total.labels(service=svc, error_type="pool_timeout").inc(1)
        logger.debug("Metrics: pool_timeout error incremented (pct=%.2f%%)",
                      state.pool_error_pct)

    # Increment error counter if fraud errors are firing
    if state.fraud_error_pct > 0:
        errors_total.labels(service=svc, error_type="fraud_svc_unavailable").inc(1)
        logger.debug("Metrics: fraud_svc_unavailable error incremented (pct=%.2f%%)",
                      state.fraud_error_pct)

    logger.debug("Metrics updated: latency=%.0f err=%.2f%% conns=%d cache_hit=%.3f",
                  state.p99_latency_ms, state.error_rate_pct,
                  state.active_connections, state.cache_hit_ratio)


def reset_all() -> None:
    """Reset all gauges to zero (used on startup/clear)."""
    svc = SERVICE
    p99_latency.labels(service=svc).set(0)
    error_rate.labels(service=svc).set(0)
    active_connections.labels(service=svc).set(0)
    max_connections.labels(service=svc).set(0)
    cache_hit_ratio.labels(service=svc).set(0)
    logger.info("All Prometheus gauges reset to zero")


def get_metrics_output() -> tuple:
    """Return (body, status_code, headers) for the Flask /metrics endpoint."""
    return generate_latest(REGISTRY), 200, {"Content-Type": CONTENT_TYPE_LATEST}
