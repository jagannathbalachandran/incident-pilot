"""
log_generator.py

Pushes a batch of already-built log line dicts to Loki via its HTTP push
API. ``traffic.py`` derives the actual log content (one line per span,
straight from that span's outcome) and calls ``push_to_loki()`` once per
tick -- this module has no state and no opinion about log content, only
about getting a batch there reliably.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

LOKI_PUSH_URL = os.getenv("LOKI_PUSH_URL", "http://loki:3100/loki/api/v1/push")


def push_to_loki(lines: list, tick_now: Optional[datetime] = None) -> None:
    """Push a batch of log lines to Loki via the HTTP push API, grouped by service.

    Uses the provided ``tick_now`` datetime to derive the nanosecond base, so
    the Loki index timestamp ALIGNS with the log content timestamp. Each line
    within a service's batch gets an incremented nanosecond timestamp so Loki
    does NOT deduplicate them as identical entries.

    Args:
        lines:    Log entry dicts to push. Each must have a ``service`` key --
                  entries are grouped into one Loki stream per service, since
                  ``service`` is the (bounded-cardinality) stream label;
                  ``trace_id``/``request_id`` stay in the JSON body only.
        tick_now: Datetime captured at the start of the emitting tick. Falls
                  back to ``datetime.now()`` if not provided.
    """
    if not lines:
        return
    now = tick_now or datetime.now(timezone.utc)
    base_ns = int(now.timestamp() * 1_000_000_000)

    by_service: dict = {}
    for line in lines:
        by_service.setdefault(line.get("service", "unknown"), []).append(line)

    streams = []
    i = 0
    for service, svc_lines in by_service.items():
        streams.append({
            "stream": {"service": service, "source": "incident-generator"},
            "values": [
                [str(base_ns + i + j), json.dumps(line)]
                for j, line in enumerate(svc_lines)
            ],
        })
        i += len(svc_lines)

    try:
        resp = requests.post(LOKI_PUSH_URL, json={"streams": streams}, timeout=2)
        if resp.status_code not in (204, 200):
            logger.warning("Loki push returned %d: %s", resp.status_code, resp.text[:100])
    except requests.RequestException as exc:
        logger.warning("Loki push failed: %s", exc)
