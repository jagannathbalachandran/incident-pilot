"""
log_generator.py

Derives structured JSON log lines FROM the current ScenarioState every tick,
then prints them to stdout (which the Docker Loki log driver picks up).

This mirrors the derive_app_logs() logic in generate_synthetic_data.py: log
line values are always read from the state object, never re-rolled. This
guarantees log/metric alignment by construction.
"""

import json
import logging
import os
import random
from datetime import datetime, timezone
from typing import Optional

import requests

from config import (
    ScenarioState,
    SERVICE,
    MAX_CONNECTIONS,
    POOL_ERROR_MSG,
    FRAUD_ERROR_MSG,
    CACHE_WARN_MESSAGES,
    LATENCY_SLO_WARN_MSG,
    RNG,
)

logger = logging.getLogger(__name__)

LOKI_PUSH_URL = os.getenv("LOKI_PUSH_URL", "http://loki:3100/loki/api/v1/push")


class LogGenerator:
    """Generates structured application logs from scenario state.

    One instance lives in the Flask app. Its ``emit_logs()`` method is
    called every tick after ``metrics_exporter.update_all()``.

    Logs are both printed to stdout (for Docker logs visibility) and
    pushed directly to Loki via HTTP for reliable ingestion.
    """

    def __init__(self):
        self._rng = RNG

    def _push_to_loki(self, lines: list[dict], tick_now: Optional[datetime] = None) -> None:
        """Push a batch of log lines to Loki via the HTTP push API.

        Uses the provided ``tick_now`` datetime to derive the nanosecond
        base, so the Loki index timestamp ALIGNS with the log content
        timestamp.  Each line within the batch gets an incremented
        nanosecond timestamp (``+i``) so Loki does NOT deduplicate them
        as identical entries.

        Args:
            lines:    Log entry dicts to push.
            tick_now: Datetime captured at the start of the emitting tick.
                      Falls back to ``datetime.now()`` if not provided.
        """
        if not lines:
            return
        now = tick_now or datetime.now(timezone.utc)
        base_ns = int(now.timestamp() * 1_000_000_000)
        stream = {
            "streams": [
                {
                    "stream": {"service": SERVICE, "source": "flask-generator"},
                    "values": [
                        [str(base_ns + i), json.dumps(line)]
                        for i, line in enumerate(lines)
                    ],
                }
            ]
        }
        try:
            resp = requests.post(LOKI_PUSH_URL, json=stream, timeout=2)
            if resp.status_code not in (204, 200):
                logger.warning("Loki push returned %d: %s", resp.status_code, resp.text[:100])
        except requests.RequestException as exc:
            logger.warning("Loki push failed: %s", exc)

    def emit_logs(self, state: ScenarioState) -> None:
        """Derive and print log lines for the current tick.

        Lines are printed as JSONL to stdout (for Docker logs) and
        pushed directly to Loki via HTTP for reliable ingestion.

        A single ``now`` timestamp is captured once at the start of the
        tick and reused for ALL log entries — this guarantees that every
        log line from the same tick has an identical timestamp, and that
        the Loki index timestamp aligns with the log content timestamp.
        """
        now = datetime.now(timezone.utc)
        ts_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        lines = []

        # Every log entry carries the request ID from the triggering API call
        rid = state.request_id or "-"

        # 1. Pool connection timeout errors
        if (
            state.pool_error_pct > 0
            and state.phase in ("climbing", "plateau")
            and state.active_connections >= 190
        ):
            lines.append({
                "timestamp": ts_iso,
                "service": SERVICE,
                "level": "ERROR",
                "message": POOL_ERROR_MSG,
                "request_id": rid,
                "active_connections": state.active_connections,
                "max_connections": MAX_CONNECTIONS,
            })

        # 2. Latency SLO warning (probability-based, matching original ~30%)
        if state.p99_latency_ms > 1500 and self._rng.random() < 0.3:
            lines.append({
                "timestamp": ts_iso,
                "service": SERVICE,
                "level": "WARN",
                "message": LATENCY_SLO_WARN_MSG,
                "request_id": rid,
                "p99_latency_ms": round(state.p99_latency_ms, 1),
            })

        # 3. Cache failover warnings
        if state.cache_warn_pct > 0 and self._rng.random() < (state.cache_warn_pct / 100.0):
            lines.append({
                "timestamp": ts_iso,
                "service": SERVICE,
                "level": "WARN",
                "message": self._rng.choice(CACHE_WARN_MESSAGES),
                "request_id": rid,
            })

        # 4. Fraud-scoring-service errors
        if state.fraud_error_pct > 0 and state.phase == "active":
            # Emit 1-2 error lines per tick during fraud outage
            for _ in range(self._rng.randint(1, 2)):
                lines.append({
                    "timestamp": ts_iso,
                    "service": SERVICE,
                    "level": "ERROR",
                    "message": FRAUD_ERROR_MSG,
                    "request_id": rid,
                })

        # Print everything as JSONL to stdout (for Docker log visibility)
        for line in lines:
            print(json.dumps(line), flush=True)

        # Push directly to Loki using the SAME `now` timestamp so the Loki
        # index timestamp aligns with the log content timestamp.
        self._push_to_loki(lines, tick_now=now)

        if lines:
            logger.debug("Emitted %d log line(s) @ %s: %s", len(lines), ts_iso,
                          [l["level"] + "/" + l["message"][:40] for l in lines])
