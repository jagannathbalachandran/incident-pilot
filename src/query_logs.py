"""
query_logs.py

Tool for querying live metrics and logs from Prometheus and Loki,
with automatic fallback to static JSON/JSONL files when the live
stack is not running.

The generator now simulates several services (auth-service,
listing-service, checkout-api, payment-service, ...); passing
``service=None`` (the default) queries across all of them, which is
what's needed to reconstruct one whole user journey (login -> ... ->
logout) since its spans land in more than one service's log stream.
Pass an explicit service name to scope a query to one service instead.

Used by IncidentPilot.query() to include live incident data (and, when
relevant, a reconstructed distributed trace) in the triage context.

Environment variables:
    PROMETHEUS_URL  — default http://localhost:9090
    LOKI_URL        — default http://localhost:3100
"""

import json
import logging
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
LOKI_URL = os.getenv("LOKI_URL", "http://localhost:3100")

DATA_DIR = Path(__file__).parent.parent / "synthetic-data"

# Every service the generator simulates -- used only to fan the static
# fallback loaders out across per-service files when no single service is
# requested. Kept as a local, display/fallback-only list rather than an
# import from flask-generator/topology.py, since this module never depends
# on the generator's internals (it only talks to Prometheus/Loki over HTTP).
ALL_SERVICES = ("auth-service", "listing-service", "checkout-api", "payment-service")

# Canonical journey order (login -> ... -> logout), used only to order a
# reconstructed trace's top-level steps for display -- mirrors
# flask-generator/topology.py's JOURNEY.
JOURNEY_ORDER = (
    ("auth-service", "/login"),
    ("listing-service", "/listings"),
    ("checkout-api", "/checkout"),
    ("checkout-api", "/payment"),
    ("auth-service", "/logout"),
)

DEFAULT_SERVICE: Optional[str] = None  # None == query across all services

# ---------------------------------------------------------------------------
# Timeframe helpers
# ---------------------------------------------------------------------------


def parse_timeframe(timeframe: str) -> tuple[datetime, datetime]:
    """Parse a timeframe string into (start, end) datetimes.

    Supported formats:
      - Relative durations: ``"15m"``, ``"1h"``, ``"30s"``
      - Absolute ranges:   ``"2026-05-14T13:50:00Z/2026-05-14T14:50:00Z"``
      - Default:           last 15 minutes
    """
    now = datetime.now(timezone.utc)

    # Relative duration (e.g. "15m", "1h", "30s")
    if timeframe.endswith(("s", "m", "h")):
        value = int(timeframe[:-1])
        unit = timeframe[-1]
        if unit == "s":
            delta = timedelta(seconds=value)
        elif unit == "m":
            delta = timedelta(minutes=value)
        elif unit == "h":
            delta = timedelta(hours=value)
        else:
            delta = timedelta(minutes=15)
        logger.debug("parse_timeframe: relative '%s' → %d %s window",
                      timeframe, value, unit)
        return now - delta, now

    # Absolute range (e.g. "2026-05-14T13:50:00Z/2026-05-14T14:50:00Z")
    if "/" in timeframe:
        parts = timeframe.split("/")
        if len(parts) == 2:
            start = _parse_iso(parts[0])
            end = _parse_iso(parts[1])
            if start and end:
                logger.debug("parse_timeframe: absolute '%s' → %s → %s",
                              timeframe, start.isoformat(), end.isoformat())
                return start, end

    # fallback
    logger.debug("parse_timeframe: fallback to 15m for '%s'", timeframe)
    return now - timedelta(minutes=15), now


def _parse_iso(s: str) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp, tolerating trailing 'Z'."""
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Prometheus query
# ---------------------------------------------------------------------------


def query_prometheus(
    service: Optional[str] = DEFAULT_SERVICE,
    timeframe: str = "15m",
) -> Optional[list[dict]]:
    """Query Prometheus for metric time-series data via the HTTP API.

    ``service=None`` queries every ``svc_*`` metric across all services;
    passing a service name scopes the query to that service's label.

    Returns a list of Prometheus result series (each with ``metric`` and
    ``values`` keys), or **None** if the live endpoint is unreachable.
    """
    start, end = parse_timeframe(timeframe)

    # ``or``-chaining would only return the leftmost metric with data;
    # ``__name__=~"svc_.*"`` matches every service metric family instead.
    if service and service != "all":
        promql = f'{{__name__=~"svc_.*",service="{service}"}}'
    else:
        promql = '{__name__=~"svc_.*"}'

    params = {
        "query": promql,
        "start": start.timestamp(),
        "end": end.timestamp(),
        "step": "60",
    }

    logger.debug("query_prometheus: GET %s/api/v1/query_range (start=%.0f end=%.0f)",
                  PROMETHEUS_URL, start.timestamp(), end.timestamp())

    try:
        resp = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query_range",
            params=params,
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()["data"]["result"]
        # Strip any phase label that might have leaked (defensive)
        for series in data:
            series["metric"].pop("phase", None)
        logger.info("Prometheus query succeeded: %d series returned", len(data))
        return data
    except requests.ConnectionError:
        logger.warning("Prometheus connection refused at %s — will fall back",
                        PROMETHEUS_URL)
        return None
    except requests.Timeout:
        logger.warning("Prometheus request timed out at %s — will fall back",
                        PROMETHEUS_URL)
        return None
    except (KeyError, ValueError) as exc:
        logger.warning("Prometheus response malformed: %s", exc)
        return None


def _load_metrics_fallback(
    service: Optional[str] = DEFAULT_SERVICE,
) -> Optional[list[dict]]:
    """Read metric values from the static JSON files.

    ``service=None`` merges every service's fallback file into one series
    list. Returns a list of Prometheus-style series dicts.
    """
    metrics_dir = DATA_DIR / "metrics"
    if not metrics_dir.is_dir():
        logger.debug("Metrics fallback dir %s not found", metrics_dir)
        return None

    targets = ALL_SERVICES if not service or service == "all" else (service,)
    combined: list[dict] = []
    for svc in targets:
        combined.extend(_load_one_service_metrics_fallback(svc))

    if not combined:
        return None
    logger.info("Loaded metrics fallback for %d service(s)", len(targets))
    return combined


def _load_one_service_metrics_fallback(service: str) -> list[dict]:
    metrics_dir = DATA_DIR / "metrics"
    for filename in (f"{service}-current-metrics.json", f"{service}-2026-05-14-metrics.json"):
        path = metrics_dir / filename
        if not path.exists():
            continue
        try:
            with open(path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read metrics fallback %s: %s", filename, exc)
            continue

        series = data.get("series", [])
        if not series:
            continue

        # Only the last (most recent) point is needed — _format_live_data only
        # reads the latest value from each series.
        last_point = series[-1]
        ts = last_point.get("timestamp", "")
        return [
            {"metric": {"__name__": "svc_p99_latency_ms", "service": service},
             "values": [[ts, str(last_point.get("p99_latency_ms", "0"))]]},
            {"metric": {"__name__": "svc_error_rate_pct", "service": service},
             "values": [[ts, str(last_point.get("error_rate_pct", "0"))]]},
            {"metric": {"__name__": "svc_active_connections", "service": service},
             "values": [[ts, str(last_point.get("active_connections", "0"))]]},
            {"metric": {"__name__": "svc_cache_hit_ratio", "service": service},
             "values": [[ts, str(last_point.get("cache_hit_ratio", "0"))]]},
            {"metric": {"__name__": "svc_max_connections", "service": service},
             "values": [[ts, str(last_point.get("max_connections", "0"))]]},
        ]
    return []


# ---------------------------------------------------------------------------
# Loki query
# ---------------------------------------------------------------------------


def query_loki(
    service: Optional[str] = DEFAULT_SERVICE,
    timeframe: str = "15m",
) -> Optional[list[dict]]:
    """Query Loki for log lines.

    ``service=None`` matches every service stream (via the static
    ``source="incident-generator"`` label every stream carries); passing a
    service name scopes the query to that service's stream label.

    Returns a flat list of log entry dicts, or **None** if the live
    endpoint is unreachable.
    """
    start, end = parse_timeframe(timeframe)

    if service and service != "all":
        logql = f'{{service="{service}"}}'
    else:
        logql = '{source="incident-generator"}'

    params = {
        "query": logql,
        "start": str(int(start.timestamp())),
        "end": str(int(end.timestamp())),
        "limit": "500",
    }

    logger.debug("query_loki: GET %s/loki/api/v1/query_range (start=%s end=%s)",
                  LOKI_URL, params["start"], params["end"])

    try:
        resp = requests.get(
            f"{LOKI_URL}/loki/api/v1/query_range",
            params=params,
            timeout=5,
        )
        resp.raise_for_status()
        raw = resp.json()["data"]["result"]
        entries = []
        for stream in raw:
            labels = stream.get("stream", {})
            for ts_ns, line in stream.get("values", []):
                entries.append({
                    "timestamp": ts_ns,
                    "line": line,
                    "labels": labels,
                })
        logger.info("Loki query succeeded: %d entries from %d stream(s)",
                     len(entries), len(raw))
        return entries
    except requests.ConnectionError:
        logger.warning("Loki connection refused at %s — will fall back", LOKI_URL)
        return None
    except requests.Timeout:
        logger.warning("Loki request timed out at %s — will fall back", LOKI_URL)
        return None
    except (KeyError, ValueError) as exc:
        logger.warning("Loki response malformed: %s", exc)
        return None


def _load_logs_fallback(
    service: Optional[str] = DEFAULT_SERVICE,
) -> Optional[list[dict]]:
    """Read log lines from the static JSONL files.

    ``service=None`` merges every service's fallback file. Returns a list
    of log entry dicts.
    """
    logs_dir = DATA_DIR / "logs"
    if not logs_dir.is_dir():
        logger.debug("Logs fallback dir %s not found", logs_dir)
        return None

    targets = ALL_SERVICES if not service or service == "all" else (service,)
    combined: list[dict] = []
    for svc in targets:
        combined.extend(_load_one_service_logs_fallback(svc))

    if not combined:
        return None
    logger.info("Loaded logs fallback for %d service(s), %d lines total",
                 len(targets), len(combined))
    return combined


def _load_one_service_logs_fallback(service: str) -> list[dict]:
    logs_dir = DATA_DIR / "logs"
    for filename in (f"{service}-current-app-logs.jsonl", f"{service}-2026-05-14-app-logs.jsonl"):
        path = logs_dir / filename
        if not path.exists():
            continue
        try:
            entries = []
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    log_entry = json.loads(line)
                    entries.append({
                        "timestamp": log_entry.get("timestamp", ""),
                        "line": json.dumps(log_entry),
                        "labels": {"service": service},
                    })
            if entries:
                return entries
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read logs fallback %s: %s", filename, exc)
            continue
    return []


# ---------------------------------------------------------------------------
# Log analysis
# ---------------------------------------------------------------------------


def _extract_level(line: str) -> str:
    """Extract log level from a log line string.

    Tries JSON parsing first (for structured logs), then falls back to
    regex on the raw line.
    """
    try:
        obj = json.loads(line)
        level = obj.get("level", "").upper()
        if level in ("ERROR", "WARN", "INFO", "DEBUG"):
            return level
    except (json.JSONDecodeError, TypeError):
        pass

    upper = line.upper()
    for token in ("ERROR", "WARN", "INFO", "DEBUG", "FATAL"):
        if token in upper:
            return token if token != "FATAL" else "ERROR"
    return "INFO"


def _extract_message(line: str) -> str:
    """Extract the human-readable message from a log line."""
    try:
        obj = json.loads(line)
        msg = obj.get("message") or obj.get("msg") or ""
        return str(msg)
    except (json.JSONDecodeError, TypeError):
        pass
    return line


def _normalize_message(message: str) -> str:
    """Normalize a log message by replacing variable data with placeholders.

    Replaces:
      - Numbers (integers, decimals, durations like 5000ms) with ``*``
      - UUIDs and hex strings with ``*``
      - IP addresses with ``*``
    """
    # Replace durations like "5000ms", "30s", "2m"
    msg = re.sub(r'\b\d+(\.\d+)?(ms|s|m|h|us|ns)?\b', '*', message)
    # Replace hex sequences
    msg = re.sub(r'\b[0-9a-fA-F]{8,}\b', '*', msg)
    # Replace IP addresses
    msg = re.sub(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', '*', msg)
    # Collapse multiple spaces
    msg = re.sub(r'\s+', ' ', msg).strip()
    return msg


def analyze_logs(log_entries: Optional[list[dict]]) -> dict:
    """Analyze a list of log entries and return structured analysis.

    Input entries should have the form::

        {"timestamp": "...", "line": "...", "labels": {...}}

    Returns a dict with:
      - ``total_entries``: total log count
      - ``time_range``: ``{"earliest", "latest"}`` ISO timestamps or None
      - ``by_level``: ``{"ERROR": N, "WARN": N, "INFO": N, "DEBUG": N}``
      - ``error_rate_pct``: percentage of ERROR + FATAL entries
      - ``top_messages``: up to 10 most frequent unique message patterns
      - ``error_clusters``: list of time-bounded error bursts
      - ``error_cluster_count``: number of distinct error bursts
    """
    analysis = {
        "total_entries": 0,
        "time_range": None,
        "by_level": {},
        "error_rate_pct": 0.0,
        "top_messages": [],
        "error_clusters": [],
        "error_cluster_count": 0,
    }

    if not log_entries:
        logger.debug("analyze_logs: no entries to analyze")
        return analysis

    analysis["total_entries"] = len(log_entries)
    logger.debug("analyze_logs: analyzing %d entries", len(log_entries))

    # --- Parse entries ---
    parsed: list[dict] = []
    for entry in log_entries:
        line = entry.get("line", "")
        ts_raw = entry.get("timestamp", "")
        level = _extract_level(line)
        msg = _extract_message(line)
        parsed.append({"timestamp": ts_raw, "level": level, "message": msg, "line": line})

    # --- Level counts ---
    level_counter: Counter = Counter(p["level"] for p in parsed)
    analysis["by_level"] = dict(level_counter)
    error_count = level_counter.get("ERROR", 0)
    analysis["error_rate_pct"] = round(error_count / len(parsed) * 100, 1) if parsed else 0.0
    logger.debug("analyze_logs: levels=%s error_rate=%.1f%%",
                  dict(level_counter), analysis["error_rate_pct"])

    # --- Top messages (case-insensitive grouped) ---
    msg_counter: Counter = Counter()
    for p in parsed:
        normalized = _normalize_message(p["message"])
        if normalized:
            msg_counter[(p["level"], normalized)] += 1

    analysis["top_messages"] = [
        {
            "pattern": pattern,
            "level": level,
            "count": count,
        }
        for (level, pattern), count in msg_counter.most_common(10)
    ]

    # --- Time range ---
    timestamps = [p["timestamp"] for p in parsed if p["timestamp"]]
    if timestamps:
        analysis["time_range"] = {
            "earliest": timestamps[0],
            "latest": timestamps[-1],
        }

    # --- Error clusters (bursts of ERROR entries within 30s of each other) ---
    error_entries = [p for p in parsed if p["level"] == "ERROR"]
    if len(error_entries) >= 2:
        clusters = []
        current_cluster = [error_entries[0]]
        for i in range(1, len(error_entries)):
            gap = _timestamp_diff(
                error_entries[i]["timestamp"],
                current_cluster[-1]["timestamp"],
            )
            if gap is not None and gap <= 30:
                current_cluster.append(error_entries[i])
            else:
                if len(current_cluster) >= 2:
                    clusters.append({
                        "start": current_cluster[0]["timestamp"],
                        "end": current_cluster[-1]["timestamp"],
                        "count": len(current_cluster),
                    })
                current_cluster = [error_entries[i]]
        if len(current_cluster) >= 2:
            clusters.append({
                "start": current_cluster[0]["timestamp"],
                "end": current_cluster[-1]["timestamp"],
                "count": len(current_cluster),
            })
        analysis["error_clusters"] = clusters
        analysis["error_cluster_count"] = len(clusters)
        if clusters:
            logger.info("analyze_logs: detected %d error cluster(s)", len(clusters))

    return analysis


def _timestamp_diff(ts1: str, ts2: str) -> Optional[float]:
    """Return the absolute difference in seconds between two timestamp strings.

    Handles both nanosecond epoch strings (Loki) and ISO-8601 strings.
    """
    if not ts1 or not ts2:
        return None

    dt1 = _try_parse_timestamp(ts1)
    dt2 = _try_parse_timestamp(ts2)
    if dt1 is None or dt2 is None:
        return None
    return abs((dt1 - dt2).total_seconds())


def _try_parse_timestamp(ts: str) -> Optional[datetime]:
    """Try to parse a timestamp string as either nanosecond epoch or ISO-8601."""
    # Try nanosecond epoch (Loki returns timestamps like "1700000000000000000")
    try:
        seconds = int(ts) / 1_000_000_000
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        pass

    # Try ISO-8601
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        pass

    return None


# ---------------------------------------------------------------------------
# Distributed trace reconstruction
# ---------------------------------------------------------------------------


def _order_spans(spans: list[dict]) -> list[dict]:
    """Order one trace's spans root-first, each root's children depth-first.

    Roots (``parent_span_id == ""``) are ordered by their position in the
    canonical JOURNEY_ORDER so the reconstructed path reads login -> ... ->
    logout regardless of the order Loki happened to return entries in.
    """
    by_id = {s.get("span_id"): s for s in spans if s.get("span_id")}
    children: dict = defaultdict(list)
    roots = []
    for s in spans:
        pid = s.get("parent_span_id") or ""
        if pid and pid in by_id:
            children[pid].append(s)
        else:
            roots.append(s)

    def journey_key(s: dict):
        step = (s.get("service"), s.get("endpoint"))
        return JOURNEY_ORDER.index(step) if step in JOURNEY_ORDER else len(JOURNEY_ORDER)

    roots.sort(key=journey_key)
    for child_list in children.values():
        child_list.sort(key=lambda s: s.get("span_id", ""))

    ordered = []

    def visit(span: dict) -> None:
        ordered.append(span)
        for child in children.get(span.get("span_id"), []):
            visit(child)

    for root in roots:
        visit(root)
    return ordered


def analyze_traces(log_entries: Optional[list[dict]]) -> dict:
    """Group log entries by ``trace_id`` and reconstruct user journeys.

    Returns a dict with:
      - ``total_traces``: distinct trace_id count seen in this window
      - ``failed_traces``: traces containing at least one non-2xx span
      - ``affected_users``: distinct user_id count among failed traces
      - ``break_points``: up to 5 ``{service, endpoint, status_code, count}``
        entries, where journeys most often first hit a non-2xx response
      - ``sample_path``: the ordered span list (login -> ... -> logout, or
        wherever it broke) for one representative failed trace, or None
    """
    result = {
        "total_traces": 0,
        "failed_traces": 0,
        "affected_users": 0,
        "break_points": [],
        "sample_path": None,
        "sample_trace_id": None,
    }
    if not log_entries:
        return result

    spans_by_trace: dict = defaultdict(list)
    for entry in log_entries:
        try:
            obj = json.loads(entry.get("line", ""))
        except (json.JSONDecodeError, TypeError):
            continue
        trace_id = obj.get("trace_id")
        if trace_id:
            spans_by_trace[trace_id].append(obj)

    if not spans_by_trace:
        return result

    result["total_traces"] = len(spans_by_trace)
    failed_users: set = set()
    break_counter: Counter = Counter()
    sample_path = None

    for trace_id, spans in spans_by_trace.items():
        ordered = _order_spans(spans)
        failed = [s for s in ordered if s.get("status_code", 200) >= 400]
        if not failed:
            continue
        result["failed_traces"] += 1
        first_fail = failed[0]
        break_counter[(first_fail.get("service"), first_fail.get("endpoint"),
                        first_fail.get("status_code"))] += 1
        if ordered and ordered[0].get("user_id"):
            failed_users.add(ordered[0]["user_id"])
        if sample_path is None:
            sample_path = ordered
            result["sample_trace_id"] = trace_id

    result["affected_users"] = len(failed_users)
    result["break_points"] = [
        {"service": svc, "endpoint": ep, "status_code": code, "count": n}
        for (svc, ep, code), n in break_counter.most_common(5)
    ]
    if sample_path:
        result["sample_path"] = [
            {
                "service": s.get("service"), "endpoint": s.get("endpoint"),
                "status_code": s.get("status_code"), "latency_ms": s.get("latency_ms"),
            }
            for s in sample_path
        ]

    logger.info("analyze_traces: %d trace(s), %d failed, %d affected user(s)",
                result["total_traces"], result["failed_traces"], result["affected_users"])
    return result


# ---------------------------------------------------------------------------
# Combined query
# ---------------------------------------------------------------------------


def query_logs(
    service: Optional[str] = DEFAULT_SERVICE,
    timeframe: str = "15m",
) -> dict:
    """Query both metrics and logs, with automatic fallback.

    Returns a dict::

        {
            "metrics": <list> or None,
            "logs":    <list> or None,
            "source":  "live" | "static_fallback" | "unavailable",
        }
    """
    result: dict = {"metrics": None, "logs": None, "source": "live"}
    logger.info("query_logs(service='%s', timeframe='%s')", service or "all", timeframe)

    # --- Metrics -------------------------------------------------------
    prom_data = query_prometheus(service, timeframe)
    if prom_data is not None:
        result["metrics"] = prom_data
        logger.debug("Metrics: live (Prometheus)")
    else:
        fallback = _load_metrics_fallback(service)
        if fallback is not None:
            result["metrics"] = fallback
            result["source"] = "static_fallback"
            logger.info("Metrics: static fallback")
        else:
            result["source"] = "unavailable"
            logger.warning("Metrics: unavailable (Prometheus + fallback both failed)")

    # --- Logs ----------------------------------------------------------
    loki_data = query_loki(service, timeframe)
    if loki_data is not None:
        result["logs"] = loki_data
        logger.debug("Logs: live (Loki)")
    else:
        fallback = _load_logs_fallback(service)
        if fallback is not None:
            result["logs"] = fallback
            if result["source"] != "unavailable":
                result["source"] = "static_fallback"
            logger.info("Logs: static fallback")
        elif result["source"] != "unavailable":
            result["source"] = "static_fallback"
            logger.warning("Logs: unavailable (Loki + fallback both failed)")

    logger.info("query_logs result: source=%s", result.get("source"))
    return result
