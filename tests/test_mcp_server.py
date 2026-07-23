"""
Tests for mcp_server/server.py -- the MCP tool handlers that wrap
query_logs.py's Prometheus/Loki query + fallback functions.

These call the tool functions directly (the ``@mcp.tool()`` decorator
returns the original function unchanged, it only registers it), so no
subprocess/stdio transport is needed here -- that round trip is exercised
separately via mcp_client.MCPClient against a real subprocess.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import mcp_server.server as server


def _log_entry(line: str, ts: str = "1700000000000000000") -> dict:
    return {"timestamp": ts, "line": line, "labels": {"service": "checkout-api"}}


class TestQueryMetrics(unittest.TestCase):
    def test_live_source(self):
        fake_series = [
            {"metric": {"__name__": "svc_p99_latency_ms", "service": "checkout-api"},
             "values": [["1000", "1486.2"]]},
        ]
        with patch("mcp_server.server.query_prometheus", return_value=fake_series):
            result = server.query_metrics(service="checkout-api", timeframe="15m")
        self.assertEqual(result["source"], "live")
        # query_metrics condenses each series to its latest value only --
        # see _condense_metrics.
        self.assertEqual(result["metrics"], [
            {"name": "svc_p99_latency_ms", "service": "checkout-api", "endpoint": "", "value": "1486.2"},
        ])

    def test_falls_back_to_static(self):
        fake_fallback = [
            {"metric": {"__name__": "svc_p99_latency_ms", "service": "checkout-api"},
             "values": [["1000", "400"]]},
        ]
        with patch("mcp_server.server.query_prometheus", return_value=None), \
             patch("mcp_server.server._load_metrics_fallback", return_value=fake_fallback):
            result = server.query_metrics()
        self.assertEqual(result["source"], "static_fallback")
        self.assertEqual(result["metrics"], [
            {"name": "svc_p99_latency_ms", "service": "checkout-api", "endpoint": "", "value": "400"},
        ])

    def test_unavailable_when_both_fail(self):
        with patch("mcp_server.server.query_prometheus", return_value=None), \
             patch("mcp_server.server._load_metrics_fallback", return_value=None):
            result = server.query_metrics()
        self.assertEqual(result["source"], "unavailable")
        self.assertEqual(result["metrics"], [])


class TestQueryLogs(unittest.TestCase):
    def test_live_source_returns_structured_analysis(self):
        entries = [
            _log_entry('{"level": "ERROR", "message": "could not obtain connection from pool"}'),
            _log_entry('{"level": "INFO", "message": "user login successful"}'),
        ]
        with patch("mcp_server.server.query_loki", return_value=entries):
            result = server.query_logs(service="checkout-api", timeframe="15m")
        self.assertEqual(result["source"], "live")
        self.assertEqual(result["log_analysis"]["total_entries"], 2)
        self.assertIn("trace_summary", result)
        # Raw log lines must not leak out -- only the structured analysis.
        self.assertNotIn("logs", result)

    def test_falls_back_to_static(self):
        entries = [_log_entry('{"level": "INFO", "message": "ok"}')]
        with patch("mcp_server.server.query_loki", return_value=None), \
             patch("mcp_server.server._load_logs_fallback", return_value=entries):
            result = server.query_logs()
        self.assertEqual(result["source"], "static_fallback")
        self.assertEqual(result["log_analysis"]["total_entries"], 1)

    def test_unavailable_when_both_fail(self):
        with patch("mcp_server.server.query_loki", return_value=None), \
             patch("mcp_server.server._load_logs_fallback", return_value=None):
            result = server.query_logs()
        self.assertEqual(result["source"], "unavailable")
        self.assertEqual(result["log_analysis"]["total_entries"], 0)
        self.assertEqual(result["trace_summary"]["total_traces"], 0)


if __name__ == "__main__":
    unittest.main()
