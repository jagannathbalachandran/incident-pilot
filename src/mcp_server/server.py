"""
mcp_server/server.py

MCP server exposing IncidentPilot's telemetry tools: ``query_metrics`` and
``query_logs``. Wraps the existing Prometheus/Loki query logic in
``query_logs.py`` unchanged -- this module only adds the MCP transport, it
has no telemetry logic of its own. If Prometheus/Loki can't be reached, the
tool reports ``source: "unavailable"`` rather than substituting stale data.

Run standalone (mostly for manual testing):
    cd src && python -m mcp_server.server

In production this is spawned as a subprocess by ``mcp_client.py`` over
stdio -- ``IncidentPilot`` never imports this module directly.
"""

from typing import Optional

from mcp.server.fastmcp import FastMCP

from query_logs import (
    query_prometheus,
    query_loki,
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

    If Prometheus is unreachable, returns ``source: "unavailable"`` and an
    empty metrics list -- there is no fallback, so report this to the
    engineer as "unable to reach Prometheus" rather than presenting a
    diagnosis as if it were current.

    Args:
        service: Service name to scope to (e.g. "checkout-api"). Omit to
            query across every simulated service at once.
        timeframe: Relative window ("15m", "1h") or absolute
            "start_iso/end_iso" range. Defaults to the last 15 minutes.
    """
    data = query_prometheus(service, timeframe)
    if data is None:
        return {"metrics": [], "source": "unavailable",
                "message": "Unable to reach Prometheus -- live metrics are not available."}
    return {"metrics": _condense_metrics(data), "source": "live"}


@mcp.tool()
def query_logs(service: Optional[str] = None, timeframe: str = "15m") -> dict:
    """Query application logs and return a structured analysis: log-level
    breakdown, top recurring message patterns, error clusters, and
    reconstructed user-journey traces (login -> ... -> logout). Returns
    analysis, not raw log lines.

    If Loki is unreachable, returns ``source: "unavailable"`` with an empty
    analysis -- there is no fallback, so report this to the engineer as
    "unable to reach Loki" rather than presenting a diagnosis as if it were
    current.

    Args:
        service: Service name to scope to. Omit to query across every
            simulated service (needed to reconstruct a full journey, since
            its spans land in more than one service's log stream).
        timeframe: Relative window ("15m", "1h") or absolute
            "start_iso/end_iso" range. Defaults to the last 15 minutes.
    """
    entries = query_loki(service, timeframe)
    if entries is None:
        return {
            "source": "unavailable",
            "message": "Unable to reach Loki -- live logs are not available.",
            "log_analysis": analyze_logs([]),
            "trace_summary": analyze_traces([]),
        }
    return {
        "source": "live",
        "log_analysis": analyze_logs(entries),
        "trace_summary": analyze_traces(entries),
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
