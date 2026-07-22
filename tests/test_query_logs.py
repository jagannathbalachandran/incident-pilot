"""
Tests for the query_logs module (Prometheus / Loki queries with static fallback).

Covers:
  - parse_timeframe          : relative durations, absolute ranges, fallback
  - _parse_iso               : valid/invalid ISO-8601 parsing
  - query_prometheus         : live query, connection error, timeout
  - query_loki               : live query, connection error, timeout
  - _load_metrics_fallback   : file read, missing file, bad JSON
  - _load_logs_fallback      : file read, missing file
  - query_logs               : combined live / fallback / unavailable
"""

import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import query_logs as ql


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_prometheus_response(series_count: int = 5, include_phase: bool = True) -> dict:
    """Build a mock Prometheus query_range response body."""
    names = [
        "svc_p99_latency_ms",
        "svc_error_rate_pct",
        "svc_active_connections",
        "svc_cache_hit_ratio",
        "svc_max_connections",
    ]
    results = []
    for i in range(min(series_count, len(names))):
        metric = {"__name__": names[i], "service": "checkout-api"}
        if include_phase:
            metric["phase"] = "baseline"
        results.append({
            "metric": metric,
            "values": [[1700000000, str(100 * (i + 1))]],
        })
    return {"status": "success", "data": {"result": results}}


def _fake_loki_response(entry_count: int = 3) -> dict:
    """Build a mock Loki query_range response body."""
    return {
        "status": "success",
        "data": {
            "result": [
                {
                    "stream": {"service": "checkout-api"},
                    "values": [
                        [f"{1700000000000000000 + j}", f"log line {j}"]
                        for j in range(entry_count)
                    ],
                }
            ]
        },
    }


def _fake_metrics_json_content() -> str:
    """Return a JSON string like the static metrics files."""
    return json.dumps({
        "series": [
            {
                "timestamp": "2026-07-16T12:00:00Z",
                "p99_latency_ms": 380.0,
                "error_rate_pct": 0.05,
                "active_connections": 118,
                "cache_hit_ratio": 0.95,
                "max_connections": 200,
            }
        ]
    })


def _fake_logs_jsonl_content(line_count: int = 3) -> str:
    """Return a JSONL string like the static log files."""
    lines = []
    for i in range(line_count):
        lines.append(
            json.dumps({
                "timestamp": f"2026-07-16T12:0{i}:00Z",
                "level": "INFO",
                "message": f"test log line {i}",
                "service": "checkout-api",
            })
        )
    return "\n".join(lines)


# ===================================================================
# parse_timeframe
# ===================================================================

class TestParseTimeframe(unittest.TestCase):
    """parse_timeframe converts relative/absolute strings to (start, end)."""

    def test_relative_minutes(self):
        start, end = ql.parse_timeframe("15m")
        self.assertAlmostEqual((end - start).total_seconds(), 900, delta=5)

    def test_relative_hours(self):
        start, end = ql.parse_timeframe("1h")
        self.assertAlmostEqual((end - start).total_seconds(), 3600, delta=5)

    def test_relative_seconds(self):
        start, end = ql.parse_timeframe("30s")
        self.assertAlmostEqual((end - start).total_seconds(), 30, delta=5)

    def test_absolute_range(self):
        start, end = ql.parse_timeframe("2026-05-14T13:50:00Z/2026-05-14T14:50:00Z")
        self.assertEqual(start.hour, 13)
        self.assertEqual(start.minute, 50)
        self.assertEqual(end.hour, 14)
        self.assertEqual((end - start).total_seconds(), 3600)

    def test_absolute_range_single_part_falls_back(self):
        start, end = ql.parse_timeframe("2026-05-14T13:50:00Z")
        self.assertAlmostEqual((end - start).total_seconds(), 900, delta=5)

    def test_malformed_absolute_falls_back(self):
        start, end = ql.parse_timeframe("not-a-date/also-not")
        self.assertAlmostEqual((end - start).total_seconds(), 900, delta=5)

    def test_empty_string_falls_back(self):
        start, end = ql.parse_timeframe("")
        self.assertAlmostEqual((end - start).total_seconds(), 900, delta=5)


# ===================================================================
# _parse_iso
# ===================================================================

class TestParseIso(unittest.TestCase):
    """_parse_iso handles ISO-8601 strings with optional trailing Z."""

    def test_utc_zulu(self):
        dt = ql._parse_iso("2026-05-14T13:50:00Z")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.hour, 13)

    def test_with_offset(self):
        dt = ql._parse_iso("2026-05-14T13:50:00+00:00")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.hour, 13)

    def test_none_on_invalid(self):
        self.assertIsNone(ql._parse_iso("not-a-date"))

    def test_none_on_empty(self):
        self.assertIsNone(ql._parse_iso(""))


# ===================================================================
# query_prometheus
# ===================================================================

class TestQueryPrometheus(unittest.TestCase):
    """query_prometheus returns data or None depending on network state."""

    @patch("query_logs.requests.get")
    def test_success_returns_series(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = _fake_prometheus_response(3)
        mock_get.return_value = mock_resp

        data = ql.query_prometheus(service="checkout-api", timeframe="15m")
        self.assertIsNotNone(data)
        self.assertEqual(len(data), 3)
        # phase label should be stripped (defensive)
        for series in data:
            self.assertNotIn("phase", series["metric"])

    @patch("query_logs.requests.get")
    def test_connection_error_returns_none(self, mock_get):
        mock_get.side_effect = requests.ConnectionError("refused")
        self.assertIsNone(ql.query_prometheus())

    @patch("query_logs.requests.get")
    def test_timeout_returns_none(self, mock_get):
        mock_get.side_effect = requests.Timeout("timed out")
        self.assertIsNone(ql.query_prometheus())

    @patch("query_logs.requests.get")
    def test_malformed_json_returns_none(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.side_effect = ValueError("not JSON")
        mock_get.return_value = mock_resp
        self.assertIsNone(ql.query_prometheus())

    @patch("query_logs.requests.get")
    def test_missing_data_key_returns_none(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"status": "error"}
        mock_get.return_value = mock_resp
        self.assertIsNone(ql.query_prometheus())


# ===================================================================
# query_loki
# ===================================================================

class TestQueryLoki(unittest.TestCase):
    """query_loki returns entries or None depending on network state."""

    @patch("query_logs.requests.get")
    def test_success_returns_entries(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = _fake_loki_response(3)
        mock_get.return_value = mock_resp

        data = ql.query_loki(service="checkout-api", timeframe="15m")
        self.assertIsNotNone(data)
        self.assertEqual(len(data), 3)
        for entry in data:
            self.assertIn("timestamp", entry)
            self.assertIn("line", entry)
            self.assertIn("labels", entry)

    @patch("query_logs.requests.get")
    def test_connection_error_returns_none(self, mock_get):
        mock_get.side_effect = requests.ConnectionError("refused")
        self.assertIsNone(ql.query_loki())

    @patch("query_logs.requests.get")
    def test_timeout_returns_none(self, mock_get):
        mock_get.side_effect = requests.Timeout("timed out")
        self.assertIsNone(ql.query_loki())

    @patch("query_logs.requests.get")
    def test_missing_data_key_returns_none(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {}
        mock_get.return_value = mock_resp
        self.assertIsNone(ql.query_loki())


# ===================================================================
# _load_metrics_fallback
# ===================================================================

class TestLoadMetricsFallback(unittest.TestCase):
    """_load_metrics_fallback reads from static JSON files."""

    @patch("query_logs.DATA_DIR", Path("/nonexistent"))
    def test_missing_metrics_dir_returns_none(self):
        self.assertIsNone(ql._load_metrics_fallback(service="checkout-api"))

    @patch("pathlib.Path.exists", return_value=False)
    @patch("pathlib.Path.is_dir", return_value=True)
    @patch("query_logs.DATA_DIR")
    def test_no_matching_files_returns_none(self, mock_dir, mock_is_dir, mock_exists):
        self.assertIsNone(ql._load_metrics_fallback(service="checkout-api"))

    @patch("pathlib.Path.is_dir", return_value=True)
    @patch("builtins.open", side_effect=OSError("permission denied"))
    @patch("pathlib.Path.exists", return_value=True)
    @patch("query_logs.DATA_DIR")
    def test_oserror_returns_none(self, mock_dir, mock_exists, mock_open, mock_is_dir):
        self.assertIsNone(ql._load_metrics_fallback(service="checkout-api"))

    @patch("pathlib.Path.is_dir", return_value=True)
    @patch("builtins.open", return_value=MagicMock(
        __enter__=MagicMock(return_value=MagicMock(
            read=MagicMock(return_value=_fake_metrics_json_content())
        ))
    ))
    @patch("pathlib.Path.exists", return_value=True)
    @patch("query_logs.DATA_DIR")
    def test_successful_read_returns_five_series_for_one_service(
        self, mock_dir, mock_exists, mock_open, mock_is_dir
    ):
        result = ql._load_metrics_fallback(service="checkout-api")
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 5)
        names = {s["metric"]["__name__"] for s in result}
        self.assertIn("svc_p99_latency_ms", names)
        self.assertIn("svc_error_rate_pct", names)
        self.assertIn("svc_active_connections", names)
        self.assertIn("svc_cache_hit_ratio", names)
        self.assertIn("svc_max_connections", names)
        for s in result:
            self.assertEqual(s["metric"]["service"], "checkout-api")

    @patch("pathlib.Path.is_dir", return_value=True)
    @patch("builtins.open", return_value=MagicMock(
        __enter__=MagicMock(return_value=MagicMock(
            read=MagicMock(return_value=_fake_metrics_json_content())
        ))
    ))
    @patch("pathlib.Path.exists", return_value=True)
    @patch("query_logs.DATA_DIR")
    def test_no_service_merges_every_service(
        self, mock_dir, mock_exists, mock_open, mock_is_dir
    ):
        """service=None (the default) fans out across every service's fallback file."""
        result = ql._load_metrics_fallback(service=None)
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 5 * len(ql.ALL_SERVICES))


# ===================================================================
# _load_logs_fallback
# ===================================================================

class TestLoadLogsFallback(unittest.TestCase):
    """_load_logs_fallback reads from static JSONL files."""

    @patch("query_logs.DATA_DIR", Path("/nonexistent"))
    def test_missing_logs_dir_returns_none(self):
        self.assertIsNone(ql._load_logs_fallback(service="checkout-api"))

    @patch("pathlib.Path.is_dir", return_value=True)
    @patch("builtins.open", return_value=MagicMock(
        __enter__=MagicMock(return_value=MagicMock(
            __iter__=MagicMock(return_value=iter(
                _fake_logs_jsonl_content(3).splitlines(keepends=True)
            ))
        ))
    ))
    @patch("pathlib.Path.exists", return_value=True)
    @patch("query_logs.DATA_DIR")
    def test_successful_read_returns_entries(
        self, mock_dir, mock_exists, mock_open, mock_is_dir
    ):
        result = ql._load_logs_fallback(service="checkout-api")
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 3)
        for entry in result:
            self.assertIn("timestamp", entry)
            self.assertIn("line", entry)
            self.assertIn("labels", entry)
            self.assertEqual(entry["labels"]["service"], "checkout-api")


# ===================================================================
# query_logs (combined)
# ===================================================================

class TestQueryLogsCombined(unittest.TestCase):
    """query_logs orchestrates metrics + logs with correct source label."""

    @patch("query_logs.query_loki")
    @patch("query_logs.query_prometheus")
    def test_both_live(self, mock_prom, mock_loki):
        mock_prom.return_value = [{"metric": {"__name__": "test"}, "values": []}]
        mock_loki.return_value = [{"timestamp": "0", "line": "test", "labels": {}}]

        result = ql.query_logs()
        self.assertEqual(result["source"], "live")
        self.assertIsNotNone(result["metrics"])
        self.assertIsNotNone(result["logs"])

    @patch("query_logs.query_loki", return_value=None)
    @patch("query_logs.query_prometheus", return_value=None)
    @patch("query_logs._load_metrics_fallback")
    @patch("query_logs._load_logs_fallback")
    def test_both_fallback(self, mock_logs_fb, mock_metrics_fb, mock_prom, mock_loki):
        mock_metrics_fb.return_value = [{"metric": {"__name__": "test"}, "values": []}]
        mock_logs_fb.return_value = [{"timestamp": "0", "line": "test", "labels": {}}]

        result = ql.query_logs()
        self.assertEqual(result["source"], "static_fallback")
        self.assertIsNotNone(result["metrics"])
        self.assertIsNotNone(result["logs"])

    @patch("query_logs.query_loki", return_value=None)
    @patch("query_logs.query_prometheus", return_value=None)
    @patch("query_logs._load_metrics_fallback", return_value=None)
    @patch("query_logs._load_logs_fallback", return_value=None)
    def test_both_unavailable(self, mock_logs_fb, mock_metrics_fb, mock_prom, mock_loki):
        result = ql.query_logs()
        self.assertEqual(result["source"], "unavailable")
        self.assertIsNone(result["metrics"])
        self.assertIsNone(result["logs"])

    @patch("query_logs.query_loki", return_value=None)
    @patch("query_logs.query_prometheus")
    @patch("query_logs._load_logs_fallback")
    def test_metrics_live_logs_fallback_sets_static(self, mock_logs_fb, mock_prom, mock_loki):
        """When metrics are live but logs fallback, source becomes static_fallback."""
        mock_prom.return_value = [{"metric": {"__name__": "test"}, "values": []}]
        mock_logs_fb.return_value = [{"timestamp": "0", "line": "test", "labels": {}}]

        result = ql.query_logs()
        # Current behaviour: if any data source falls back, the overall
        # source label becomes "static_fallback".
        self.assertEqual(result["source"], "static_fallback")
        self.assertIsNotNone(result["metrics"])
        self.assertIsNotNone(result["logs"])

    @patch("query_logs.query_loki")
    @patch("query_logs.query_prometheus", return_value=None)
    @patch("query_logs._load_metrics_fallback")
    def test_metrics_fallback_logs_live(self, mock_metrics_fb, mock_loki, mock_prom):
        mock_metrics_fb.return_value = [{"metric": {"__name__": "test"}, "values": []}]
        mock_loki.return_value = [{"timestamp": "0", "line": "test", "labels": {}}]

        result = ql.query_logs()
        self.assertEqual(result["source"], "live")
        self.assertIsNotNone(result["metrics"])
        self.assertIsNotNone(result["logs"])

    @patch("query_logs.query_loki", return_value=None)
    @patch("query_logs.query_prometheus", return_value=None)
    @patch("query_logs._load_metrics_fallback", return_value=None)
    @patch("query_logs._load_logs_fallback", return_value=None)
    def test_env_url_override(self, mock_logs_fb, mock_metrics_fb, mock_prom, mock_loki):
        result = ql.query_logs(service="checkout-api", timeframe="1h")
        self.assertEqual(result["source"], "unavailable")


# ===================================================================
# analyze_logs
# ===================================================================

class TestAnalyzeLogs(unittest.TestCase):
    """analyze_logs parses log entries and returns structured analysis."""

    def test_empty_entries(self):
        result = ql.analyze_logs([])
        self.assertEqual(result["total_entries"], 0)
        self.assertEqual(result["error_rate_pct"], 0.0)
        self.assertEqual(result["error_cluster_count"], 0)

    def test_none_input(self):
        result = ql.analyze_logs(None)
        self.assertEqual(result["total_entries"], 0)

    def test_level_parsing_from_json_line(self):
        entries = [
            {"timestamp": "t1", "line": '{"level":"ERROR","message":"pool timeout","service":"api"}', "labels": {}},
            {"timestamp": "t2", "line": '{"level":"WARN","message":"high latency","service":"api"}', "labels": {}},
            {"timestamp": "t3", "line": '{"level":"INFO","message":"request OK","service":"api"}', "labels": {}},
            {"timestamp": "t4", "line": '{"level":"DEBUG","message":"trace detail","service":"api"}', "labels": {}},
        ]
        result = ql.analyze_logs(entries)
        self.assertEqual(result["total_entries"], 4)
        self.assertEqual(result["by_level"]["ERROR"], 1)
        self.assertEqual(result["by_level"]["WARN"], 1)
        self.assertEqual(result["by_level"]["INFO"], 1)
        self.assertEqual(result["by_level"]["DEBUG"], 1)

    def test_level_parsing_from_plain_text_line(self):
        entries = [
            {"timestamp": "t1", "line": "2026-07-16 ERROR: connection refused", "labels": {}},
            {"timestamp": "t2", "line": "WARNING: high memory usage", "labels": {}},
        ]
        result = ql.analyze_logs(entries)
        self.assertEqual(result["by_level"].get("ERROR"), 1)
        self.assertEqual(result["by_level"].get("WARN"), 1)

    def test_fatal_mapped_to_error(self):
        entries = [
            {"timestamp": "t1", "line": '{"level":"FATAL","message":"crash"}', "labels": {}},
        ]
        result = ql.analyze_logs(entries)
        self.assertIn("ERROR", result["by_level"])
        self.assertNotIn("FATAL", result["by_level"])

    def test_error_rate_calculation(self):
        entries = [
            {"timestamp": "t1", "line": '{"level":"ERROR","message":"err1"}', "labels": {}},
            {"timestamp": "t2", "line": '{"level":"ERROR","message":"err2"}', "labels": {}},
            {"timestamp": "t3", "line": '{"level":"INFO","message":"ok"}', "labels": {}},
            {"timestamp": "t4", "line": '{"level":"INFO","message":"ok"}', "labels": {}},
        ]
        result = ql.analyze_logs(entries)
        self.assertEqual(result["error_rate_pct"], 50.0)

    def test_message_normalization_groups_similar_messages(self):
        entries = [
            {"timestamp": "t1", "line": '{"level":"ERROR","message":"timeout after 5000ms"}', "labels": {}},
            {"timestamp": "t2", "line": '{"level":"ERROR","message":"timeout after 3000ms"}', "labels": {}},
            {"timestamp": "t3", "line": '{"level":"ERROR","message":"timeout after 10000ms"}', "labels": {}},
            {"timestamp": "t4", "line": '{"level":"INFO","message":"request completed"}', "labels": {}},
        ]
        result = ql.analyze_logs(entries)
        # All three timeout messages should be normalized to the same pattern
        timeout_patterns = [
            m for m in result["top_messages"]
            if m["level"] == "ERROR" and "timeout" in m["pattern"]
        ]
        self.assertEqual(len(timeout_patterns), 1,
                         "Similar messages should be grouped into one pattern")
        self.assertEqual(timeout_patterns[0]["count"], 3)

    def test_error_cluster_detection(self):
        # Two clusters: 3 errors close together, then 2 errors close together
        entries = [
            {"timestamp": "1700000000000000000", "line": '{"level":"ERROR","message":"err"}', "labels": {}},
            {"timestamp": "1700000005000000000", "line": '{"level":"ERROR","message":"err"}', "labels": {}},
            {"timestamp": "1700000010000000000", "line": '{"level":"ERROR","message":"err"}', "labels": {}},
            # 60s gap — should split cluster
            {"timestamp": "1700000070000000000", "line": '{"level":"ERROR","message":"err"}', "labels": {}},
            {"timestamp": "1700000075000000000", "line": '{"level":"ERROR","message":"err"}', "labels": {}},
        ]
        result = ql.analyze_logs(entries)
        self.assertEqual(result["error_cluster_count"], 2)
        self.assertEqual(len(result["error_clusters"]), 2)

    def test_top_messages_limited_to_10(self):
        entries = []
        for i in range(15):
            entries.append({
                "timestamp": f"t{i}",
                "line": '{"level":"INFO","message":"msg%s"}' % i,
                "labels": {},
            })
        result = ql.analyze_logs(entries)
        self.assertLessEqual(len(result["top_messages"]), 10)

    def test_time_range_from_loki_timestamps(self):
        entries = [
            {"timestamp": "1700000000000000000", "line": '{"level":"INFO","message":"first"}', "labels": {}},
            {"timestamp": "1700000060000000000", "line": '{"level":"INFO","message":"last"}', "labels": {}},
        ]
        result = ql.analyze_logs(entries)
        self.assertIsNotNone(result["time_range"])
        self.assertEqual(result["time_range"]["earliest"], "1700000000000000000")
        self.assertEqual(result["time_range"]["latest"], "1700000060000000000")

    def test_time_range_from_iso_timestamps(self):
        """Cover fallback path where timestamps are ISO-8601 strings."""
        entries = [
            {"timestamp": "2026-07-16T12:00:00Z", "line": '{"level":"ERROR","message":"err1"}', "labels": {}},
            {"timestamp": "2026-07-16T12:00:15Z", "line": '{"level":"ERROR","message":"err2"}', "labels": {}},
            {"timestamp": "2026-07-16T12:01:00Z", "line": '{"level":"ERROR","message":"err3"}', "labels": {}},
        ]
        result = ql.analyze_logs(entries)
        self.assertIsNotNone(result["time_range"])
        # First and last should be preserved as-is
        self.assertEqual(result["time_range"]["earliest"], "2026-07-16T12:00:00Z")
        self.assertEqual(result["time_range"]["latest"], "2026-07-16T12:01:00Z")
        # 3 errors within 45s and 60s gaps — cluster detection should find 1 cluster
        # (first two at 0s and 15s = 15s gap → cluster; 3rd at 60s = 45s gap from 2nd → separate)
        # Each cluster needs >= 2 errors, so only first cluster qualifies
        self.assertEqual(result["error_cluster_count"], 1)


# ===================================================================
# analyze_traces
# ===================================================================


def _span(trace_id, span_id, parent, service, endpoint, status, user_id):
    return {
        "timestamp": "2026-07-18T10:20:00Z",
        "line": json.dumps({
            "trace_id": trace_id, "span_id": span_id, "parent_span_id": parent,
            "service": service, "endpoint": endpoint, "status_code": status,
            "latency_ms": 100.0, "user_id": user_id, "request_id": f"req-{span_id}",
            "level": "ERROR" if status >= 500 else "INFO", "message": "ok",
        }),
        "labels": {"service": service},
    }


class TestAnalyzeTraces(unittest.TestCase):
    """analyze_traces groups spans by trace_id and reconstructs journeys."""

    def _successful_trace(self):
        return [
            _span("trA", "a1", "", "auth-service", "/login", 200, "user-1"),
            _span("trA", "a2", "", "listing-service", "/listings", 200, "user-1"),
            _span("trA", "a3", "", "checkout-api", "/checkout", 200, "user-1"),
            _span("trA", "a4", "", "checkout-api", "/payment", 200, "user-1"),
            _span("trA", "a5", "a4", "payment-service", "/charge", 200, "user-1"),
            _span("trA", "a6", "", "auth-service", "/logout", 200, "user-1"),
        ]

    def _failed_trace(self):
        return [
            _span("trB", "b1", "", "auth-service", "/login", 200, "user-2"),
            _span("trB", "b2", "", "listing-service", "/listings", 200, "user-2"),
            _span("trB", "b3", "", "checkout-api", "/checkout", 200, "user-2"),
            _span("trB", "b4", "", "checkout-api", "/payment", 500, "user-2"),
            _span("trB", "b5", "b4", "payment-service", "/charge", 500, "user-2"),
        ]

    def test_empty_entries(self):
        result = ql.analyze_traces([])
        self.assertEqual(result["total_traces"], 0)
        self.assertIsNone(result["sample_path"])

    def test_counts_total_and_failed_traces(self):
        entries = self._successful_trace() + self._failed_trace()
        result = ql.analyze_traces(entries)
        self.assertEqual(result["total_traces"], 2)
        self.assertEqual(result["failed_traces"], 1)
        self.assertEqual(result["affected_users"], 1)

    def test_break_point_identifies_first_failing_hop(self):
        entries = self._successful_trace() + self._failed_trace()
        result = ql.analyze_traces(entries)
        self.assertEqual(len(result["break_points"]), 1)
        bp = result["break_points"][0]
        self.assertEqual(bp["service"], "checkout-api")
        self.assertEqual(bp["endpoint"], "/payment")
        self.assertEqual(bp["status_code"], 500)
        self.assertEqual(bp["count"], 1)

    def test_sample_path_is_ordered_root_before_child_and_stops_at_failure(self):
        entries = self._successful_trace() + self._failed_trace()
        result = ql.analyze_traces(entries)
        self.assertEqual(result["sample_trace_id"], "trB")
        path = [f"{s['service']}{s['endpoint']}" for s in result["sample_path"]]
        # login -> listings -> checkout -> payment -> charge (child of payment),
        # and crucially never reaches logout since the journey failed at payment.
        self.assertEqual(
            path,
            ["auth-service/login", "listing-service/listings", "checkout-api/checkout",
             "checkout-api/payment", "payment-service/charge"],
        )

    def test_only_successful_traces_have_no_failures(self):
        result = ql.analyze_traces(self._successful_trace())
        self.assertEqual(result["total_traces"], 1)
        self.assertEqual(result["failed_traces"], 0)
        self.assertIsNone(result["sample_path"])


if __name__ == "__main__":
    unittest.main()
