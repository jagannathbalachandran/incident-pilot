"""
traffic.py

Every tick, generates a batch of synthetic user journeys (login -> browse
listings -> checkout -> payment -> logout). Each journey is one distributed
trace: it mints a ``trace_id``/``user_id`` and walks the ``topology`` call
graph, creating a child span per hop with its own ``span_id``/``request_id``
and a ``parent_span_id`` linking the tree.

Each span's latency and status are drawn from the target service's CURRENT
health (``IncidentEngine.health_for``), so metrics and logs are a
*consequence* of (un)healthy services carrying real traffic. A downstream
5xx failure propagates up the trace as an ``upstream_error``, which is what
makes an incident on one service visibly cascade into its callers' own
numbers -- with no special-cased cross-service logic anywhere. p99/error
gauges are computed per (service, endpoint) from the tick's own span sample.
"""

import json
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

import topology
import metrics_exporter as mx
import log_generator
from config import JOURNEYS_PER_TICK, N_USERS, RNG, LATENCY_SLO_WARN_MSG, UPSTREAM_ERROR_MSG
from incident_scenarios import IncidentEngine

logger = logging.getLogger(__name__)

SUCCESS_MSG = {
    ("auth-service", "/login"): "user login successful",
    ("auth-service", "/logout"): "user logout successful",
    ("auth-service", "/validate-session"): "session validated",
    ("listing-service", "/listings"): "listings returned",
    ("checkout-api", "/checkout"): "checkout initiated, inventory reserved",
    ("checkout-api", "/payment"): "payment authorized and order created",
    ("inventory-svc", "/reserve"): "inventory reserved",
    ("fraud-scoring-svc", "/score"): "fraud score computed",
    ("payment-service", "/charge"): "payment charged",
}


def _level_for(status: int) -> str:
    if status >= 500:
        return "ERROR"
    if status >= 400:
        return "WARN"
    return "INFO"


class TrafficGenerator:
    """Walks the topology's canonical JOURNEY every tick, producing one
    distributed trace per simulated user plus the metrics/logs that trace
    implies."""

    def __init__(self, engine: IncidentEngine):
        self.engine = engine

    @staticmethod
    def _next_id(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex[:10]}"

    def tick(self, now: Optional[datetime] = None) -> list:
        """Run one tick of traffic. Returns the log lines produced (JSON dicts).

        Also observes histograms/counters and sets per-endpoint + per-service
        gauges as a side effect. ``now`` is the shared tick timestamp so the
        log content timestamp aligns with the Loki index timestamp.
        """
        now = now or datetime.now(timezone.utc)
        ts_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        logs: list = []
        # accumulator[(service, endpoint)] = list[(latency_ms, status_code)]
        acc: dict = defaultdict(list)

        for _ in range(JOURNEYS_PER_TICK):
            trace_id = self._next_id("tr")
            user_id = f"user-{RNG.randint(1, N_USERS)}"
            for service, path in topology.JOURNEY:
                status, _lat, _sid = self._run_span(
                    trace_id, user_id, service, path,
                    parent_span_id="", ts_iso=ts_iso, logs=logs, acc=acc,
                )
                # A failed top-level step ends the journey -- the user can't proceed.
                if status >= 400:
                    break

        self._publish_gauges(acc)
        log_generator.push_to_loki(logs, tick_now=now)
        for line in logs:
            print(json.dumps(line), flush=True)
        if logs:
            logger.debug("Emitted %d log line(s) from %d journey(s) @ %s",
                          len(logs), JOURNEYS_PER_TICK, ts_iso)
        return logs

    def _run_span(self, trace_id: str, user_id: str, service: str, path: str,
                  parent_span_id: str, ts_iso: str, logs: list, acc: dict) -> tuple:
        ep = topology.get_endpoint(service, path)
        if ep is None:
            return 200, 0.0, ""

        span_id = self._next_id("sp")
        request_id = self._next_id("req")
        health = self.engine.health_for(service)

        # --- own work latency ---
        own_latency = ep.base_latency_ms + health.extra_latency_ms
        own_latency = max(1.0, own_latency + RNG.gauss(0, ep.base_latency_ms * 0.15))

        # --- downstream calls (child spans) ---
        upstream_latency = 0.0
        upstream_failed_dep = None
        for dep_service, dep_path in ep.calls:
            c_status, c_lat, _c_sid = self._run_span(
                trace_id, user_id, dep_service, dep_path,
                parent_span_id=span_id, ts_iso=ts_iso, logs=logs, acc=acc,
            )
            mx.observe_upstream(service, dep_service, c_lat)
            upstream_latency += c_lat
            if c_status >= 500 and upstream_failed_dep is None:
                upstream_failed_dep = dep_service

        # --- decide status ---
        status = 200
        error_type = ""
        message = SUCCESS_MSG.get((service, path), "ok")

        if RNG.random() < health.inject_error_pct:
            # Injected incident error.
            status = 500
            error_type = health.error_type or "incident_error"
            message = health.error_message or "incident-induced failure"
        elif upstream_failed_dep is not None:
            status = 500
            error_type = "upstream_error"
            message = f"{UPSTREAM_ERROR_MSG}: {upstream_failed_dep}"
        elif RNG.random() < ep.base_error_pct / 100.0:
            status = 500
            error_type = "internal_error"
            message = "unhandled internal error"

        total_latency = round(own_latency + upstream_latency, 1)
        pod = RNG.choice(topology.pods_for(service))

        # --- primary request log line (full trace schema) ---
        line = {
            "timestamp": ts_iso,
            "level": _level_for(status),
            "service": service,
            "endpoint": path,
            "method": ep.method,
            "status_code": status,
            "latency_ms": total_latency,
            "trace_id": trace_id,
            "span_id": span_id,
            "parent_span_id": parent_span_id,
            "request_id": request_id,
            "user_id": user_id,
            "message": message,
            "pod": pod,
            "region": topology.REGION,
        }
        logs.append(line)

        # --- optional secondary WARN line (SLO breach / cache failover) ---
        if health.warn_pct > 0 and RNG.random() < health.warn_pct / 100.0:
            logs.append({
                **line,
                "level": "WARN",
                "message": health.warn_message or LATENCY_SLO_WARN_MSG,
            })

        # --- metrics ---
        mx.observe_request(service, path, status, total_latency)
        mx.inc_error(service, path, error_type)
        acc[(service, path)].append((total_latency, status))

        return status, total_latency, span_id

    def _publish_gauges(self, acc: dict) -> None:
        """Compute per-endpoint p99/error-rate gauges from the tick sample, and
        set per-service pool/cache gauges from the engine snapshot."""
        for (service, endpoint), samples in acc.items():
            latencies = sorted(s[0] for s in samples)
            n = len(latencies)
            idx = max(0, int(round(0.99 * (n - 1))))
            p99 = latencies[idx]
            errors = sum(1 for _l, st in samples if st >= 400)
            err_rate = round(errors / n * 100.0, 2) if n else 0.0
            mx.set_endpoint_gauges(service, endpoint, p99, err_rate)

        for service in topology.all_service_names():
            snap = self.engine.service_snapshot(service)
            mx.set_service_gauges(service, snap.pool_active, snap.pool_max, snap.cache_hit)
