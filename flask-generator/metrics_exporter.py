"""
metrics_exporter.py

Defines all Prometheus metrics (gauges, counters, histograms) shared across
every simulated service, and the observe/set functions ``traffic.py`` calls
per span each tick to publish them. Every metric family carries a ``service``
label (and, where per-route granularity matters, an ``endpoint`` label) so
one shared registry serves all services in the topology catalog.

Neither ``phase`` nor any other incident-internal field is ever exposed here
-- the IncidentPilot agent must infer the incident stage from the shape of
these numbers, the same way an on-call engineer would.
"""

import logging

from prometheus_client import Gauge, Counter, Histogram, CollectorRegistry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Collector Registry
# ---------------------------------------------------------------------------

REGISTRY = CollectorRegistry()

# ---------------------------------------------------------------------------
# Gauges (instantaneous values, per service+endpoint)
# ---------------------------------------------------------------------------

svc_p99_latency_ms = Gauge(
    "svc_p99_latency_ms",
    "p99 request latency in milliseconds for this tick's traffic sample",
    ["service", "endpoint"],
    registry=REGISTRY,
)

svc_error_rate_pct = Gauge(
    "svc_error_rate_pct",
    "Request error rate in percent for this tick's traffic sample",
    ["service", "endpoint"],
    registry=REGISTRY,
)

# --- Per-service infra gauges (not per-endpoint -- pool/cache are shared) ---

svc_active_connections = Gauge(
    "svc_active_connections",
    "Current active database connections",
    ["service"],
    registry=REGISTRY,
)

svc_max_connections = Gauge(
    "svc_max_connections",
    "Maximum database connections",
    ["service"],
    registry=REGISTRY,
)

svc_cache_hit_ratio = Gauge(
    "svc_cache_hit_ratio",
    "Redis cache hit ratio",
    ["service"],
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# Counters (cumulative, only increase)
# ---------------------------------------------------------------------------

svc_requests_total = Counter(
    "svc_requests_total",
    "Total requests handled, by status code",
    ["service", "endpoint", "status_code"],
    registry=REGISTRY,
)

svc_errors_total = Counter(
    "svc_errors_total",
    "Total errors by type",
    ["service", "endpoint", "error_type"],
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# Histograms (latency distribution)
# ---------------------------------------------------------------------------

svc_request_duration_ms = Histogram(
    "svc_request_duration_ms",
    "Request duration in milliseconds (own + downstream calls)",
    ["service", "endpoint"],
    buckets=(100, 250, 500, 1000, 1500, 2000, 3000, 5000, 10000),
    registry=REGISTRY,
)

svc_upstream_duration_ms = Histogram(
    "svc_upstream_duration_ms",
    "Latency of a synchronous call from one service into a downstream service",
    ["service", "upstream"],
    buckets=(50, 100, 250, 500, 1000, 1500, 2000, 3000, 5000),
    registry=REGISTRY,
)


def observe_request(service: str, endpoint: str, status_code: int, latency_ms: float) -> None:
    """Record one completed request: counter + duration histogram."""
    svc_requests_total.labels(service=service, endpoint=endpoint, status_code=str(status_code)).inc()
    svc_request_duration_ms.labels(service=service, endpoint=endpoint).observe(latency_ms)


def inc_error(service: str, endpoint: str, error_type: str) -> None:
    """Increment the error counter, if this request actually errored."""
    if not error_type:
        return
    svc_errors_total.labels(service=service, endpoint=endpoint, error_type=error_type).inc()
    logger.debug("Metrics: %s error on %s%s", error_type, service, endpoint)


def observe_upstream(service: str, upstream: str, latency_ms: float) -> None:
    """Record how long a synchronous downstream call took, from the caller's side."""
    svc_upstream_duration_ms.labels(service=service, upstream=upstream).observe(latency_ms)


def set_endpoint_gauges(service: str, endpoint: str, p99_latency_ms: float, error_rate_pct: float) -> None:
    """Sync the per-(service, endpoint) p99/error-rate gauges for this tick's sample."""
    svc_p99_latency_ms.labels(service=service, endpoint=endpoint).set(p99_latency_ms)
    svc_error_rate_pct.labels(service=service, endpoint=endpoint).set(error_rate_pct)


def set_service_gauges(service: str, pool_active: int, pool_max: int, cache_hit: float) -> None:
    """Sync the per-service infra gauges (pool/cache) for this tick."""
    svc_active_connections.labels(service=service).set(pool_active)
    svc_max_connections.labels(service=service).set(pool_max)
    svc_cache_hit_ratio.labels(service=service).set(cache_hit)
