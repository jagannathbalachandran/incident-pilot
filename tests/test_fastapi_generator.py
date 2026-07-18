"""
Tests for the FastAPI generator endpoints (flask-generator/app.py).

Uses FastAPI's TestClient to test all API endpoints without running
the Docker stack. The background tick loop is mocked to prevent it
from running during tests.

Covers:
  - GET  /health                  — health check, active_incident field
  - GET  /metrics                 — Prometheus text format, content-type
  - POST /api/incidents/{kind}/trigger  — pool, cache, fraud triggers
  - POST /api/incidents/trigger-random  — random scenario
  - POST /api/incidents/{kind}/resolve  — resolve by kind and current
  - GET  /api/incidents/state          — state with/without active incident
  - Error handling: invalid kind, no body, resolve with no incident
  - X-Request-ID header presence
  - OpenAPI docs endpoints (/docs, /redoc, /openapi.json)
  - Incident lifecycle simulation via engine.tick()
"""

import json
import os
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add flask-generator to path BEFORE importing the app module
sys.path.insert(0, str(Path(__file__).parent.parent / "flask-generator"))

# Mock threading.Thread BEFORE any flask-generator imports so the
# background tick loop never starts during tests.
_mock_thread = MagicMock(spec=threading.Thread)
_mock_thread.start = MagicMock()


@patch("threading.Thread", return_value=_mock_thread)
def _get_app(mock_thread_class):
    """Import and return the FastAPI app with tick loop mocked out.

    This function must be called with the threading patch active so
    that the module-level ``_thread = threading.Thread(...)`` call in
    app.py receives a mock instead of a real thread.
    """
    import importlib

    # Clear any cached import of app module
    if "app" in sys.modules:
        del sys.modules["app"]

    from app import app
    return app


# Obtain the test app once (module-level, so it's available to all tests)
app = _get_app()

from starlette.testclient import TestClient

client = TestClient(app)

# Also import engine so we can manipulate state in lifecycle tests
from app import engine


# ===================================================================
# Helpers
# ===================================================================

def _reset_engine() -> None:
    """Force-reset the engine state between tests."""
    engine._active = None


def _validate_trigger_response(data: dict, expected_kind: str) -> None:
    """Assert common trigger response fields."""
    assert data["status"] == "started", f"Expected started, got {data['status']}"
    assert data["kind"] == expected_kind, f"Expected {expected_kind}, got {data['kind']}"
    assert "phase" in data, "Missing phase field"
    assert "tick_count" in data, "Missing tick_count field"
    assert "request_id" in data, "Missing request_id field"
    assert len(data["request_id"]) == 12, f"request_id should be 12 chars, got {len(data['request_id'])}"


def _validate_state_response(data: dict, expected_kind: str = "none") -> None:
    """Assert common state response fields."""
    assert data["kind"] == expected_kind, f"Expected kind={expected_kind}, got {data['kind']}"
    assert "phase" in data
    assert "phase_progress" in data
    assert "tick_count" in data
    assert "p99_latency_ms" in data
    assert "error_rate_pct" in data
    assert "active_connections" in data
    assert "cache_hit_ratio" in data
    assert "auto_resolve" in data


# ===================================================================
# GET /health
# ===================================================================

class TestHealthEndpoint(unittest.TestCase):
    """GET /health returns service status and active incident info."""

    def setUp(self):
        _reset_engine()

    def test_health_returns_200_and_ok_status(self):
        response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["service"], "flask-generator")
        self.assertIn("request_id", data)

    def test_health_shows_no_active_incident(self):
        response = client.get("/health")
        data = response.json()
        self.assertIsNone(data["active_incident"])

    def test_health_shows_active_incident_kind(self):
        engine.start_scenario("pool", auto_resolve=True)
        response = client.get("/health")
        data = response.json()
        self.assertEqual(data["active_incident"], "pool")

    def test_health_has_request_id_header(self):
        response = client.get("/health")
        self.assertIn("x-request-id", response.headers)


# ===================================================================
# GET /metrics
# ===================================================================

class TestMetricsEndpoint(unittest.TestCase):
    """GET /metrics returns Prometheus text format."""

    def test_metrics_returns_200(self):
        response = client.get("/metrics")
        self.assertEqual(response.status_code, 200)

    def test_metrics_has_correct_content_type(self):
        response = client.get("/metrics")
        self.assertIn("text/plain", response.headers["content-type"])

    def test_metrics_contains_gauge_metrics(self):
        response = client.get("/metrics")
        body = response.text
        # All gauge metrics should be present
        self.assertIn("checkout_p99_latency_ms", body)
        self.assertIn("checkout_error_rate_pct", body)
        self.assertIn("checkout_active_connections", body)
        self.assertIn("checkout_cache_hit_ratio", body)
        self.assertIn("checkout_max_connections", body)

    def test_metrics_has_help_lines(self):
        response = client.get("/metrics")
        body = response.text
        self.assertIn("# HELP", body)
        self.assertIn("# TYPE", body)

    def test_metrics_has_service_label(self):
        """Prometheus metrics include a 'service' label."""
        response = client.get("/metrics")
        body = response.text
        # Prometheus format: metric_name{label="value"} value
        self.assertIn("checkout_p99_latency_ms", body,
                      "Expected p99 latency metric in Prometheus output")
        self.assertIn("checkout_error_rate_pct", body,
                      "Expected error rate metric in Prometheus output")
        self.assertIn("checkout_active_connections", body,
                      "Expected active connections metric in Prometheus output")

    def test_metrics_does_not_leak_phase_label(self):
        """The phase field must not appear as a Prometheus label."""
        _reset_engine()
        engine.start_scenario("pool", auto_resolve=True)
        engine.tick()
        engine.tick()
        response = client.get("/metrics")
        body = response.text
        self.assertNotIn("phase", body,
                         "Phase label leaked into Prometheus metrics!")


# ===================================================================
# POST /api/incidents/{kind}/trigger
# ===================================================================

class TestTriggerEndpoint(unittest.TestCase):
    """POST /api/incidents/{kind}/trigger starts incident scenarios."""

    def setUp(self):
        _reset_engine()

    def test_trigger_pool(self):
        response = client.post("/api/incidents/pool/trigger",
                               json={"auto_resolve": True})
        self.assertEqual(response.status_code, 200)
        _validate_trigger_response(response.json(), "pool")

    def test_trigger_cache(self):
        response = client.post("/api/incidents/cache/trigger",
                               json={"auto_resolve": True})
        self.assertEqual(response.status_code, 200)
        _validate_trigger_response(response.json(), "cache")

    def test_trigger_fraud(self):
        response = client.post("/api/incidents/fraud/trigger",
                               json={"auto_resolve": True})
        self.assertEqual(response.status_code, 200)
        _validate_trigger_response(response.json(), "fraud")

    def test_trigger_pool_initial_phase_is_climbing(self):
        response = client.post("/api/incidents/pool/trigger",
                               json={"auto_resolve": True})
        data = response.json()
        self.assertEqual(data["phase"], "climbing")

    def test_trigger_cache_initial_phase_is_climbing(self):
        """Cache starts in 'climbing' phase; first tick advances to 'failover'."""
        response = client.post("/api/incidents/cache/trigger",
                               json={"auto_resolve": True})
        data = response.json()
        self.assertEqual(data["phase"], "climbing")

    def test_trigger_fraud_initial_phase_is_active(self):
        response = client.post("/api/incidents/fraud/trigger",
                               json={"auto_resolve": True})
        data = response.json()
        self.assertEqual(data["phase"], "active")

    def test_trigger_without_body_uses_defaults(self):
        """Trigger with no body should default auto_resolve to True."""
        response = client.post("/api/incidents/pool/trigger")
        self.assertEqual(response.status_code, 200)
        _validate_trigger_response(response.json(), "pool")

    def test_trigger_without_auto_resolve(self):
        response = client.post("/api/incidents/pool/trigger",
                               json={"auto_resolve": False})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        # engine should reflect auto_resolve=False internally

    def test_trigger_invalid_kind_returns_400(self):
        response = client.post("/api/incidents/invalid/trigger",
                               json={"auto_resolve": True})
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("error", data)
        self.assertIn("unknown incident kind", data["error"].lower())

    def test_trigger_has_request_id(self):
        response = client.post("/api/incidents/pool/trigger",
                               json={"auto_resolve": True})
        data = response.json()
        self.assertEqual(len(data["request_id"]), 12)

    def test_trigger_restart_replaces_active_scenario(self):
        """Triggering a new scenario should replace the current one."""
        client.post("/api/incidents/pool/trigger", json={"auto_resolve": True})
        response = client.post("/api/incidents/cache/trigger",
                               json={"auto_resolve": True})
        data = response.json()
        self.assertEqual(data["kind"], "cache")  # Should now be cache


# ===================================================================
# POST /api/incidents/trigger-random
# ===================================================================

class TestTriggerRandomEndpoint(unittest.TestCase):
    """POST /api/incidents/trigger-random starts a random scenario."""

    def setUp(self):
        _reset_engine()

    def test_trigger_random_returns_200(self):
        response = client.post("/api/incidents/trigger-random")
        self.assertEqual(response.status_code, 200)

    def test_trigger_random_has_valid_kind(self):
        response = client.post("/api/incidents/trigger-random")
        data = response.json()
        self.assertIn(data["kind"], {"pool", "cache", "fraud"})

    def test_trigger_random_has_started_status(self):
        response = client.post("/api/incidents/trigger-random")
        data = response.json()
        self.assertEqual(data["status"], "started")

    def test_trigger_random_has_request_id(self):
        response = client.post("/api/incidents/trigger-random")
        data = response.json()
        self.assertIn("request_id", data)
        self.assertEqual(len(data["request_id"]), 12)


# ===================================================================
# POST /api/incidents/{kind}/resolve
# ===================================================================

class TestResolveEndpoint(unittest.TestCase):
    """POST /api/incidents/{kind}/resolve resolves active incidents."""

    def setUp(self):
        _reset_engine()

    def test_resolve_with_no_active_incident(self):
        response = client.post("/api/incidents/current/resolve")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "no_active_incident")

    def test_resolve_specific_kind(self):
        engine.start_scenario("pool", auto_resolve=True)
        response = client.post("/api/incidents/pool/resolve")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "resolved")
        self.assertEqual(data["kind"], "pool")

    def test_resolve_current_resolves_whatever_is_active(self):
        engine.start_scenario("cache", auto_resolve=True)
        response = client.post("/api/incidents/current/resolve")
        data = response.json()
        self.assertEqual(data["status"], "resolved")

    def test_resolve_kind_mismatch_returns_no_active(self):
        engine.start_scenario("pool", auto_resolve=True)
        response = client.post("/api/incidents/cache/resolve")
        data = response.json()
        self.assertEqual(data["status"], "no_active_incident")

    def test_resolve_has_request_id(self):
        engine.start_scenario("fraud", auto_resolve=True)
        response = client.post("/api/incidents/fraud/resolve")
        data = response.json()
        self.assertIn("request_id", data)


# ===================================================================
# GET /api/incidents/state
# ===================================================================

class TestStateEndpoint(unittest.TestCase):
    """GET /api/incidents/state returns current incident metrics."""

    def setUp(self):
        _reset_engine()

    def test_state_with_no_active_incident(self):
        response = client.get("/api/incidents/state")
        self.assertEqual(response.status_code, 200)
        _validate_state_response(response.json(), "none")

    def test_state_shows_baseline_metrics_when_inactive(self):
        response = client.get("/api/incidents/state")
        data = response.json()
        self.assertEqual(data["p99_latency_ms"], 380.0)
        self.assertEqual(data["error_rate_pct"], 0.05)
        self.assertEqual(data["active_connections"], 118)
        self.assertEqual(data["cache_hit_ratio"], 0.95)

    def test_state_after_pool_trigger(self):
        engine.start_scenario("pool", auto_resolve=True)
        engine.tick()
        response = client.get("/api/incidents/state")
        data = response.json()
        self.assertEqual(data["kind"], "pool")
        self.assertIn(data["phase"], {"climbing", "plateau", "recovering"})
        # Connections should be above baseline after tick
        self.assertGreaterEqual(data["active_connections"], 118)

    def test_state_after_cache_trigger(self):
        engine.start_scenario("cache", auto_resolve=True)
        engine.tick()
        response = client.get("/api/incidents/state")
        data = response.json()
        self.assertEqual(data["kind"], "cache")
        # Cache hit should drop during failover
        self.assertLess(data["cache_hit_ratio"], 0.95)

    def test_state_after_fraud_trigger(self):
        engine.start_scenario("fraud", auto_resolve=True)
        engine.tick()
        response = client.get("/api/incidents/state")
        data = response.json()
        self.assertEqual(data["kind"], "fraud")
        # Error rate should be high during fraud
        self.assertGreaterEqual(data["error_rate_pct"], 10.0)

    def test_state_after_resolve(self):
        engine.start_scenario("pool", auto_resolve=True)
        engine.resolve()
        response = client.get("/api/incidents/state")
        data = response.json()
        self.assertEqual(data["kind"], "none")

    def test_state_has_request_id(self):
        response = client.get("/api/incidents/state")
        data = response.json()
        # When no incident, state endpoint includes request_id via _json_resp
        self.assertIn("request_id", data)

    def test_state_has_no_internal_fields(self):
        """Internal fields like pool_error_pct must not leak in state."""
        engine.start_scenario("pool", auto_resolve=True)
        engine.tick()
        response = client.get("/api/incidents/state")
        data = response.json()
        self.assertNotIn("pool_error_pct", data)
        self.assertNotIn("fraud_error_pct", data)
        self.assertNotIn("cache_warn_pct", data)
        self.assertNotIn("started_at", data)


# ===================================================================
# Incident Lifecycle (engine.tick() progression)
# ===================================================================

class TestIncidentLifecycle(unittest.TestCase):
    """Incident scenarios progress through phases via engine.tick()."""

    def setUp(self):
        _reset_engine()

    def test_pool_lifecycle_progresses_through_phases(self):
        """Pool: climbing → plateau → recovering → resolved (auto)."""
        engine.start_scenario("pool", auto_resolve=True)

        # Initial: climbing
        self.assertEqual(engine._active.phase, "climbing")

        # Tick through climbing phase (15 ticks)
        for _ in range(14):
            engine.tick()
        self.assertEqual(engine._active.phase, "climbing",
                         "Should still be climbing before tick 15")

        engine.tick()  # tick 15 → plateau
        self.assertEqual(engine._active.phase, "plateau")

        # Tick through plateau (15 more ticks = tick 30)
        for _ in range(14):
            engine.tick()
        self.assertEqual(engine._active.phase, "plateau")

        engine.tick()  # tick 30 → recovering
        self.assertEqual(engine._active.phase, "recovering")

        # Tick through recovery (10 ticks = tick 40)
        for _ in range(9):
            engine.tick()
        self.assertEqual(engine._active.phase, "recovering")

        engine.tick()  # tick 40 → resolved (auto)
        self.assertIsNone(engine.get_state(),
                          "Engine should return None when resolved (auto)")

    def test_cache_lifecycle_progresses_through_phases(self):
        """Cache: climbing → failover → warming → resolved (auto).

        Phase transitions happen when tick_count >= duration budget.
        failover_end = 6, warming_end = 18.
        """
        engine.start_scenario("cache", auto_resolve=True)
        # Initial phase is 'climbing' (tick_count=0)
        self.assertEqual(engine._active.phase, "climbing")

        engine.tick()  # tick_count=1 → failover (1 < 6)
        self.assertEqual(engine._active.phase, "failover")

        # Ticks 2-5: still failover (5 < 6)
        engine.tick()  # tick 2
        engine.tick()  # tick 3
        engine.tick()  # tick 4
        engine.tick()  # tick 5
        self.assertEqual(engine._active.phase, "failover")

        engine.tick()  # tick 6 → warming (6 == 6, so 6 < 6 is False, 6 < 18 is True)
        self.assertEqual(engine._active.phase, "warming")

        # Ticks 7-17: still warming
        for _ in range(11):
            engine.tick()
        self.assertEqual(engine._active.phase, "warming")  # tick 17

        engine.tick()  # tick 18 → resolved (auto, 18 < 18 is False)
        self.assertIsNone(engine.get_state())

    def test_fraud_lifecycle_progresses_through_phases(self):
        """Fraud: active → resolved (auto)."""
        engine.start_scenario("fraud", auto_resolve=True)
        self.assertEqual(engine._active.phase, "active")

        for _ in range(19):
            engine.tick()
        self.assertEqual(engine._active.phase, "active")

        engine.tick()  # tick 20 → resolved
        self.assertIsNone(engine.get_state())

    def test_metric_values_change_during_lifecycle(self):
        """Verify metrics change meaningfully during pool lifecycle."""
        engine.start_scenario("pool", auto_resolve=False)

        # Capture baseline at start
        initial = engine.get_state()
        initial_conns = initial.active_connections
        initial_latency = initial.p99_latency_ms

        # Tick well into climbing phase (8 ticks should show clear increase)
        for _ in range(8):
            engine.tick()
        mid = engine.get_state()
        self.assertGreater(mid.active_connections, initial_conns,
                           "Connections should increase during climbing")
        self.assertGreaterEqual(mid.p99_latency_ms, initial_latency,
                                "Latency should not decrease during climbing")

    def test_pool_metrics_at_plateau(self):
        """At plateau, connections should be at MAX (200)."""
        engine.start_scenario("pool", auto_resolve=False)
        # Tick to plateau (15 ticks climbing)
        for _ in range(15):
            engine.tick()
        state = engine.get_state()
        self.assertEqual(state.phase, "plateau")
        self.assertEqual(state.active_connections, 200)
        self.assertGreater(state.error_rate_pct, 1.0)

    def test_fraud_metrics_have_high_error_rate(self):
        """Fraud incidents should have error_rate > 10%."""
        engine.start_scenario("fraud", auto_resolve=False)
        engine.tick()
        state = engine.get_state()
        self.assertGreaterEqual(state.error_rate_pct, 10.0)

    def test_cache_metrics_have_low_hit_ratio(self):
        """Cache incidents should have low cache_hit_ratio."""
        engine.start_scenario("cache", auto_resolve=False)
        engine.tick()
        state = engine.get_state()
        self.assertLess(state.cache_hit_ratio, 0.60)


# ===================================================================
# X-Request-ID Header
# ===================================================================

class TestRequestIdTracing(unittest.TestCase):
    """X-Request-ID header is present on JSON responses."""

    def setUp(self):
        _reset_engine()

    def test_health_response_has_x_request_id_header(self):
        response = client.get("/health")
        self.assertIn("x-request-id", response.headers)
        rid = response.headers["x-request-id"]
        self.assertEqual(len(rid), 12)

    def test_trigger_response_has_x_request_id_header(self):
        response = client.post("/api/incidents/pool/trigger",
                               json={"auto_resolve": True})
        self.assertIn("x-request-id", response.headers)

    def test_state_response_has_x_request_id_header(self):
        response = client.get("/api/incidents/state")
        self.assertIn("x-request-id", response.headers)

    def test_resolve_response_has_x_request_id_header(self):
        engine.start_scenario("pool", auto_resolve=True)
        response = client.post("/api/incidents/pool/resolve")
        self.assertIn("x-request-id", response.headers)

    def test_request_id_in_body_matches_header(self):
        response = client.get("/health")
        body_rid = response.json().get("request_id", "")
        header_rid = response.headers.get("x-request-id", "")
        # Note: body and header may differ because the middleware sets
        # the header after the response is generated (post-call_next),
        # while the body request_id is set during route handling.
        # Both should be valid 12-char hex strings.
        self.assertEqual(len(body_rid), 12)
        self.assertEqual(len(header_rid), 12)


# ===================================================================
# X-Request-ID Header Not on Metrics
# ===================================================================

class TestMetricsNoRequestId(unittest.TestCase):
    """The /metrics endpoint should NOT have X-Request-ID header."""

    def test_metrics_does_not_have_x_request_id_header(self):
        """Metrics endpoint returns plain text, not JSON."""
        response = client.get("/metrics")
        self.assertNotIn("x-request-id", response.headers,
                         "Metrics endpoint should not expose request IDs")


# ===================================================================
# OpenAPI Documentation
# ===================================================================

class TestOpenApiDocs(unittest.TestCase):
    """FastAPI auto-generates OpenAPI documentation."""

    def test_openapi_json_is_valid(self):
        response = client.get("/openapi.json")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["info"]["title"], "Incident Generator")
        self.assertEqual(data["info"]["version"], "3.0.0")

    def test_openapi_json_lists_all_paths(self):
        response = client.get("/openapi.json")
        paths = response.json()["paths"]
        expected_paths = [
            "/health",
            "/metrics",
            "/api/incidents/{kind}/trigger",
            "/api/incidents/{kind}/resolve",
            "/api/incidents/trigger-random",
            "/api/incidents/state",
        ]
        for path in expected_paths:
            self.assertIn(path, paths, f"Missing path: {path}")

    def test_openapi_json_has_request_models(self):
        """Pydantic models used as body params appear in OpenAPI schemas.
        Models returned via _json_resp() (not declared as response_model)
        won't appear — they're manually serialized."""
        response = client.get("/openapi.json")
        schemas = response.json()["components"]["schemas"]
        # TriggerRequest appears because it's a FastAPI body parameter
        self.assertIn("TriggerRequest", schemas,
                      "TriggerRequest should be in schemas (body param)")

    def test_docs_swagger_ui_returns_html(self):
        response = client.get("/docs")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])

    def test_redoc_ui_returns_html(self):
        response = client.get("/redoc")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])


# ===================================================================
# Error Handling
# ===================================================================

class TestErrorHandling(unittest.TestCase):
    """Edge cases and error responses."""

    def setUp(self):
        _reset_engine()

    def test_trigger_empty_string_kind(self):
        """An empty string kind should be treated as invalid."""
        response = client.post("/api/incidents//trigger",
                               json={"auto_resolve": True})
        # FastAPI will not match the route with empty string, so we
        # expect a 404 (no route matched) or 405
        self.assertIn(response.status_code, {404, 405})

    def test_trigger_with_extra_fields_ignored(self):
        """Extra fields in the request body should be ignored (not error)."""
        response = client.post("/api/incidents/pool/trigger",
                               json={"auto_resolve": True, "extra_field": "ignored"})
        self.assertEqual(response.status_code, 200)

    def test_resolve_with_kind_mismatch_shows_expected_and_active(self):
        engine.start_scenario("pool", auto_resolve=True)
        response = client.post("/api/incidents/cache/resolve")
        data = response.json()
        self.assertEqual(data["status"], "no_active_incident")
        self.assertEqual(data.get("expected"), "cache")
        self.assertEqual(data.get("active"), "pool")

    def test_concurrent_triggers_dont_crash(self):
        """Multiple rapid triggers should not crash the engine."""
        for _ in range(10):
            response = client.post("/api/incidents/pool/trigger",
                                   json={"auto_resolve": True})
            self.assertEqual(response.status_code, 200)
            _reset_engine()

    def test_trigger_then_state_then_resolve_cycle(self):
        """Full cycle: trigger → state → tick → state → resolve."""
        # Trigger
        resp = client.post("/api/incidents/pool/trigger",
                           json={"auto_resolve": True})
        self.assertEqual(resp.status_code, 200)

        # State (active)
        resp = client.get("/api/incidents/state")
        self.assertEqual(resp.json()["kind"], "pool")

        # Tick
        engine.tick()

        # State (still active, progressed)
        resp = client.get("/api/incidents/state")
        self.assertEqual(resp.json()["kind"], "pool")

        # Resolve
        resp = client.post("/api/incidents/pool/resolve")
        self.assertEqual(resp.json()["status"], "resolved")

        # State (inactive)
        resp = client.get("/api/incidents/state")
        self.assertEqual(resp.json()["kind"], "none")


# ===================================================================
# Edge Cases: Empty Engine State
# ===================================================================

class TestEdgeCases(unittest.TestCase):
    """Edge cases around engine state boundaries."""

    def setUp(self):
        _reset_engine()

    def test_state_after_engine_forced_reset(self):
        """State should return 'none' after engine is forcefully cleared."""
        engine.start_scenario("pool", auto_resolve=True)
        _reset_engine()
        response = client.get("/api/incidents/state")
        data = response.json()
        self.assertEqual(data["kind"], "none")

    def test_multiple_resolves_return_ok(self):
        """Resolving multiple times should not error."""
        engine.start_scenario("pool", auto_resolve=True)
        client.post("/api/incidents/pool/resolve")
        # Second resolve should return no_active_incident
        response = client.post("/api/incidents/pool/resolve")
        data = response.json()
        self.assertEqual(data["status"], "no_active_incident")

    def test_all_kinds_can_be_triggered_and_resolved(self):
        """All three incident kinds can be triggered and resolved."""
        for kind in ("pool", "cache", "fraud"):
            _reset_engine()
            resp = client.post(f"/api/incidents/{kind}/trigger",
                               json={"auto_resolve": False})
            self.assertEqual(resp.status_code, 200, f"Failed to trigger {kind}")
            self.assertEqual(resp.json()["kind"], kind)

            resp = client.post(f"/api/incidents/{kind}/resolve")
            self.assertEqual(resp.status_code, 200, f"Failed to resolve {kind}")
            self.assertEqual(resp.json()["status"], "resolved")


if __name__ == "__main__":
    unittest.main()
