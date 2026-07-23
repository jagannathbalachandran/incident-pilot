"""
mcp_server/server.py

MCP server exposing IncidentPilot's telemetry tools: ``query_metrics`` and
``query_logs``. Wraps the existing Prometheus/Loki query + fallback logic in
``query_logs.py`` unchanged -- this module only adds the MCP transport, it
has no telemetry logic of its own.

Run standalone (mostly for manual testing):
    cd src && python -m mcp_server.server

In production this is spawned as a subprocess by ``mcp_client.py`` over
stdio -- ``IncidentPilot`` never imports this module directly.
"""

from typing import Optional

from mcp.server.fastmcp import FastMCP

from query_logs import (
    query_prometheus,
    _load_metrics_fallback,
    query_loki,
    _load_logs_fallback,
    analyze_logs,
    analyze_traces,
)

mcp = FastMCP("incident-pilot-telemetry")


def _condense_metrics(series_list: list[dict]) -> list[dict]:
    """Collapse each Prometheus series to its latest value only.

    A triage query needs current state, not a full time-series dump the
    model would have to re-aggregate itself -- and querying "all services"
    can return 100+ series, whose full ``values`` arrays are large enough to
    blow past the model's per-request token budget if passed through
    unmodified.
    """
    condensed = []
    for series in series_list:
        m = series.get("metric", {})
        values = series.get("values", [])
        if not values:
            continue
        condensed.append({
            "name": m.get("__name__", "unknown"),
            "service": m.get("service", ""),
            "endpoint": m.get("endpoint", ""),
            "value": values[-1][1],
        })
    return condensed


@mcp.tool()
def query_metrics(service: Optional[str] = None, timeframe: str = "15m") -> dict:
    """Query live Prometheus metrics: p99 latency, error rate, active
    connections, and cache hit ratio. Returns each metric's latest value
    (not a full time-series history).

    Falls back automatically to static synthetic data if Prometheus is
    unreachable -- the ``source`` field in the result tells you which
    happened ("live" or "static_fallback"), or "unavailable" if neither
    worked.

    Args:
        service: Service name to scope to (e.g. "checkout-api"). Omit to
            query across every simulated service at once.
        timeframe: Relative window ("15m", "1h") or absolute
            "start_iso/end_iso" range. Defaults to the last 15 minutes.
    """
    data = query_prometheus(service, timeframe)
    source = "live"
    if data is None:
        data = _load_metrics_fallback(service)
        source = "static_fallback" if data is not None else "unavailable"
    return {"metrics": _condense_metrics(data or []), "source": source}


@mcp.tool()
def query_logs(service: Optional[str] = None, timeframe: str = "15m") -> dict:
    """Query application logs and return a structured analysis: log-level
    breakdown, top recurring message patterns, error clusters, and
    reconstructed user-journey traces (login -> ... -> logout). Returns
    analysis, not raw log lines.

    Falls back automatically to static synthetic data if Loki is
    unreachable -- the ``source`` field tells you which happened.

    Args:
        service: Service name to scope to. Omit to query across every
            simulated service (needed to reconstruct a full journey, since
            its spans land in more than one service's log stream).
        timeframe: Relative window ("15m", "1h") or absolute
            "start_iso/end_iso" range. Defaults to the last 15 minutes.
    """
    entries = query_loki(service, timeframe)
    source = "live"
    if entries is None:
        entries = _load_logs_fallback(service)
        source = "static_fallback" if entries is not None else "unavailable"
    entries = entries or []
    return {
        "source": source,
        "log_analysis": analyze_logs(entries),
        "trace_summary": analyze_traces(entries),
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
