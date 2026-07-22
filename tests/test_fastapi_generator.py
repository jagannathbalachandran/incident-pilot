"""
Tests for the FastAPI generator endpoints and multi-service simulation
(flask-generator/*.py).

Uses FastAPI's TestClient to test all API endpoints without running
the Docker stack. The background tick loop is mocked to prevent it
from running during tests; ``traffic.tick()`` is called directly in
tests that need traffic-derived metrics/logs/traces.

Covers:
  - GET  /health, /metrics, /api/services
  - POST /api/incidents/{kind}/trigger  — pool, cache, fraud, per-service targeting
  - POST /api/incidents/trigger-random  — random scenario on a supporting service
  - POST /api/incidents/{kind}/resolve  — resolve by kind/service and current
  - GET  /api/incidents/state           — list of concurrently-active incidents
  - Topology catalog and journey integrity
  - Multi-incident engine: concurrent incidents on different services
  - Cascade: an incident on a downstream service inflates its caller's metrics
  - Distributed trace correlation: trace_id/span_id/parent_span_id chains
  - Error handling, X-Request-ID header, OpenAPI docs
"""

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
    """Import and return the FastAPI app with tick loop mocked out."""
    import importlib

    if "app" in sys.modules:
        del sys.modules["app"]

    from app import app
    return app


app = _get_app()

from starlette.testclient import TestClient

client = TestClient(app)

# Also import engine/traffic so tests can manipulate state / run ticks directly
from app import engine, traffic
import topology
from config import service_supports


# ===================================================================
# Helpers
# ===================================================================

def _reset_engine() -> None:
    """Force-reset the engine state between tests."""
    engine._incidents = {}


def _validate_trigger_response(data: dict, expected_kind: str, expected_service: str = None) -> None:
    """Assert common trigger response fields."""
    assert data["status"] == "started", f"Expected started, got {data['status']}"
    assert data["kind"] == expected_kind, f"Expected {expected_kind}, got {data['kind']}"
    assert "service" in data, "Missing service field"
    if expected_service:
        assert data["service"] == expected_service, f"Expected service={expected_service}, got {data['service']}"
    assert "phase" in data, "Missing phase field"
    assert "tick_count" in data, "Missing tick_count field"
    assert "request_id" in data, "Missing request_id field"
    assert len(data["request_id"]) == 12, f"request_id should be 12 chars, got {len(data['request_id'])}"


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
        self.assertEqual(data["service"], "incident-generator")
        self.assertIn("request_id", data)

    def test_health_shows_no_active_incidents(self):
        response = client.get("/health")
        data = response.json()
        self.assertEqual(data["active_incidents"], [])

    def test_health_shows_active_incident_label(self):
        engine.start_scenario("pool", auto_resolve=True)
        response = client.get("/health")
        data = response.json()
        self.assertIn("pool@checkout-api", data["active_incidents"])

    def test_health_shows_multiple_active_incidents(self):
        engine.start_scenario("pool", auto_resolve=True)
        engine.start_scenario("cache", service="listing-service", auto_resolve=True)
        response = client.get("/health")
        data = response.json()
        self.assertEqual(len(data["active_incidents"]), 2)
        self.assertIn("pool@checkout-api", data["active_incidents"])
        self.assertIn("cache@listing-service", data["active_incidents"])

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
        self.assertIn("svc_p99_latency_ms", body)
        self.assertIn("svc_error_rate_pct", body)
        self.assertIn("svc_active_connections", body)
        self.assertIn("svc_cache_hit_ratio", body)
        self.assertIn("svc_max_connections", body)

    def test_metrics_has_help_lines(self):
        response = client.get("/metrics")
        body = response.text
        self.assertIn("# HELP", body)
        self.assertIn("# TYPE", body)

    def test_metrics_have_service_and_endpoint_labels_after_traffic(self):
        """Once traffic.tick() runs, gauges carry real service+endpoint labels."""
        _reset_engine()
        traffic.tick()
        response = client.get("/metrics")
        body = response.text
        self.assertIn('service="checkout-api"', body)
        self.assertIn('endpoint="/checkout"', body)

    def test_metrics_does_not_leak_phase_label(self):
        """The phase field must not appear as a Prometheus label."""
        _reset_engine()
        engine.start_scenario("pool", auto_resolve=True)
        engine.tick()
        engine.tick()
        traffic.tick()
        response = client.get("/metrics")
        body = response.text
        self.assertNotIn("phase", body,
                         "Phase label leaked into Prometheus metrics!")


# ===================================================================
# GET /api/services
# ===================================================================

class TestServicesEndpoint(unittest.TestCase):
    """GET /api/services returns the topology catalog + canonical journey."""

    def test_services_returns_200(self):
        response = client.get("/api/services")
        self.assertEqual(response.status_code, 200)

    def test_services_lists_all_catalog_services(self):
        data = client.get("/api/services").json()
        names = {s["name"] for s in data["services"]}
        for expected in ("auth-service", "listing-service", "checkout-api", "payment-service"):
            self.assertIn(expected, names)

    def test_services_journey_matches_topology(self):
        data = client.get("/api/services").json()
        journey = [tuple(step) for step in data["journey"]]
        self.assertEqual(journey, list(topology.JOURNEY))

    def test_services_response_has_request_id(self):
        data = client.get("/api/services").json()
        self.assertIn("request_id", data)


# ===================================================================
# POST /api/incidents/{kind}/trigger
# ===================================================================

class TestTriggerEndpoint(unittest.TestCase):
    """POST /api/incidents/{kind}/trigger starts incident scenarios."""

    def setUp(self):
        _reset_engine()

    def test_trigger_pool_defaults_to_checkout_api(self):
        response = client.post("/api/incidents/pool/trigger", json={"auto_resolve": True})
        self.assertEqual(response.status_code, 200)
        _validate_trigger_response(response.json(), "pool", "checkout-api")

    def test_trigger_cache_defaults_to_checkout_api(self):
        response = client.post("/api/incidents/cache/trigger", json={"auto_resolve": True})
        self.assertEqual(response.status_code, 200)
        _validate_trigger_response(response.json(), "cache", "checkout-api")

    def test_trigger_fraud_defaults_to_fraud_scoring_svc(self):
        """fraud defaults to the actual dependency, so checkout-api's own
        error rate rises as a genuine cascade, not a separately-injected number."""
        response = client.post("/api/incidents/fraud/trigger", json={"auto_resolve": True})
        self.assertEqual(response.status_code, 200)
        _validate_trigger_response(response.json(), "fraud", "fraud-scoring-svc")

    def test_trigger_pool_on_explicit_supporting_service(self):
        response = client.post("/api/incidents/pool/trigger",
                               json={"auto_resolve": True, "service": "payment-service"})
        self.assertEqual(response.status_code, 200)
        _validate_trigger_response(response.json(), "pool", "payment-service")

    def test_trigger_pool_on_unsupporting_service_rejected(self):
        """auth-service has no db pool -- pool cannot target it."""
        response = client.post("/api/incidents/pool/trigger",
                               json={"auto_resolve": True, "service": "auth-service"})
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("does not support", data["error"])

    def test_trigger_cache_on_auth_service(self):
        response = client.post("/api/incidents/cache/trigger",
                               json={"auto_resolve": True, "service": "auth-service"})
        self.assertEqual(response.status_code, 200)
        _validate_trigger_response(response.json(), "cache", "auth-service")

    def test_trigger_pool_initial_phase_is_climbing(self):
        response = client.post("/api/incidents/pool/trigger", json={"auto_resolve": True})
        data = response.json()
        self.assertEqual(data["phase"], "climbing")

    def test_trigger_fraud_initial_phase_is_active(self):
        response = client.post("/api/incidents/fraud/trigger", json={"auto_resolve": True})
        data = response.json()
        self.assertEqual(data["phase"], "active")

    def test_trigger_without_body_uses_defaults(self):
        response = client.post("/api/incidents/pool/trigger")
        self.assertEqual(response.status_code, 200)
        _validate_trigger_response(response.json(), "pool", "checkout-api")

    def test_trigger_invalid_kind_returns_400(self):
        response = client.post("/api/incidents/invalid/trigger", json={"auto_resolve": True})
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("error", data)
        self.assertIn("unknown incident kind", data["error"].lower())

    def test_trigger_has_request_id(self):
        response = client.post("/api/incidents/pool/trigger", json={"auto_resolve": True})
        data = response.json()
        self.assertEqual(len(data["request_id"]), 12)

    def test_trigger_different_services_run_concurrently(self):
        """Triggering pool on checkout-api and cache on listing-service should
        both remain active -- they don't clobber each other."""
        client.post("/api/incidents/pool/trigger",
                   json={"auto_resolve": True, "service": "checkout-api"})
        client.post("/api/incidents/cache/trigger",
                   json={"auto_resolve": True, "service": "listing-service"})
        state = client.get("/api/incidents/state").json()
        self.assertEqual(state["count"], 2)

    def test_trigger_same_kind_same_service_restarts(self):
        """Re-triggering the same (kind, service) replaces it, not duplicates it."""
        client.post("/api/incidents/pool/trigger", json={"auto_resolve": True})
        client.post("/api/incidents/pool/trigger", json={"auto_resolve": True})
        state = client.get("/api/incidents/state").json()
        self.assertEqual(state["count"], 1)


# ===================================================================
# POST /api/incidents/trigger-random
# ===================================================================

class TestTriggerRandomEndpoint(unittest.TestCase):
    """POST /api/incidents/trigger-random starts a random scenario on a
    randomly selected supporting service."""

    def setUp(self):
        _reset_engine()

    def test_trigger_random_returns_200(self):
        response = client.post("/api/incidents/trigger-random")
        self.assertEqual(response.status_code, 200)

    def test_trigger_random_has_valid_kind_and_supporting_service(self):
        response = client.post("/api/incidents/trigger-random")
        data = response.json()
        self.assertIn(data["kind"], {"pool", "cache", "fraud"})
        self.assertTrue(service_supports(data["kind"], data["service"]))

    def test_trigger_random_has_request_id(self):
        response = client.post("/api/incidents/trigger-random")
        data = response.json()
        self.assertIn("request_id", data)
        self.assertEqual(len(data["request_id"]), 12)


# ===================================================================
# POST /api/incidents/{kind}/resolve
# ===================================================================

class TestResolveEndpoint(unittest.TestCase):
    """POST /api/incidents/{kind}/resolve resolves matching active incident(s)."""

    def setUp(self):
        _reset_engine()

    def test_resolve_with_no_active_incident(self):
        response = client.post("/api/incidents/current/resolve")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "no_active_incident")
        self.assertEqual(data["resolved"], [])

    def test_resolve_specific_kind(self):
        engine.start_scenario("pool", auto_resolve=True)
        response = client.post("/api/incidents/pool/resolve")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "resolved")
        self.assertEqual(data["resolved"], [{"kind": "pool", "service": "checkout-api", "phase": "resolved"}])

    def test_resolve_current_resolves_everything(self):
        engine.start_scenario("pool", auto_resolve=True)
        engine.start_scenario("cache", service="listing-service", auto_resolve=True)
        response = client.post("/api/incidents/current/resolve")
        data = response.json()
        self.assertEqual(data["status"], "resolved")
        self.assertEqual(len(data["resolved"]), 2)

    def test_resolve_scoped_to_service_leaves_others_active(self):
        engine.start_scenario("pool", service="checkout-api", auto_resolve=True)
        engine.start_scenario("pool", service="payment-service", auto_resolve=True)
        response = client.post("/api/incidents/pool/resolve", params={"service": "payment-service"})
        data = response.json()
        self.assertEqual(len(data["resolved"]), 1)
        self.assertEqual(data["resolved"][0]["service"], "payment-service")
        state = client.get("/api/incidents/state").json()
        self.assertEqual(state["count"], 1)
        self.assertEqual(state["active"][0]["service"], "checkout-api")

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
    """GET /api/incidents/state returns every currently-active incident."""

    def setUp(self):
        _reset_engine()

    def test_state_with_no_active_incident(self):
        response = client.get("/api/incidents/state")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["active"], [])
        self.assertEqual(data["count"], 0)

    def test_state_after_pool_trigger(self):
        engine.start_scenario("pool", auto_resolve=True)
        engine.tick()
        data = client.get("/api/incidents/state").json()
        self.assertEqual(data["count"], 1)
        entry = data["active"][0]
        self.assertEqual(entry["kind"], "pool")
        self.assertEqual(entry["service"], "checkout-api")
        self.assertIn(entry["phase"], {"climbing", "plateau", "recovering"})

    def test_state_after_multiple_triggers(self):
        engine.start_scenario("pool", service="checkout-api", auto_resolve=True)
        engine.start_scenario("fraud", service="fraud-scoring-svc", auto_resolve=True)
        data = client.get("/api/incidents/state").json()
        self.assertEqual(data["count"], 2)
        kinds_services = {(a["kind"], a["service"]) for a in data["active"]}
        self.assertEqual(kinds_services, {("pool", "checkout-api"), ("fraud", "fraud-scoring-svc")})

    def test_state_after_resolve(self):
        engine.start_scenario("pool", auto_resolve=True)
        engine.resolve()
        data = client.get("/api/incidents/state").json()
        self.assertEqual(data["count"], 0)

    def test_state_has_request_id(self):
        data = client.get("/api/incidents/state").json()
        self.assertIn("request_id", data)

    def test_state_has_no_internal_fields(self):
        """Internal effect fields must not leak into the public state."""
        engine.start_scenario("pool", auto_resolve=True)
        engine.tick()
        data = client.get("/api/incidents/state").json()
        entry = data["active"][0]
        for leaked in ("extra_latency_ms", "inject_error_pct", "error_type",
                       "error_message", "pool_active", "cache_hit"):
            self.assertNotIn(leaked, entry)


# ===================================================================
# Topology
# ===================================================================

class TestTopology(unittest.TestCase):
    """The service/endpoint/call-graph catalog is internally consistent."""

    def test_all_services_present(self):
        names = set(topology.all_service_names())
        for expected in ("auth-service", "listing-service", "checkout-api",
                          "payment-service", "inventory-svc", "fraud-scoring-svc"):
            self.assertIn(expected, names)

    def test_journey_endpoints_resolve(self):
        for service, path in topology.JOURNEY:
            ep = topology.get_endpoint(service, path)
            self.assertIsNotNone(ep, f"{service}{path} should resolve to an Endpoint")

    def test_checkout_payment_calls_payment_service(self):
        ep = topology.get_endpoint("checkout-api", "/payment")
        self.assertIn(("payment-service", "/charge"), ep.calls)

    def test_checkout_checkout_calls_auth_validate_session(self):
        ep = topology.get_endpoint("checkout-api", "/checkout")
        self.assertIn(("auth-service", "/validate-session"), ep.calls)

    def test_payment_service_not_user_facing(self):
        """payment-service is reached only as a child call, per design."""
        self.assertFalse(topology.SERVICES["payment-service"].user_facing)

    def test_pods_for_returns_nonempty(self):
        for name in topology.all_service_names():
            self.assertGreater(len(topology.pods_for(name)), 0)


# ===================================================================
# Multi-incident engine
# ===================================================================

class TestMultiIncidentEngine(unittest.TestCase):
    """Concurrent incidents on different services don't clobber each other."""

    def setUp(self):
        _reset_engine()

    def test_concurrent_incidents_have_independent_phases(self):
        engine.start_scenario("pool", service="checkout-api", auto_resolve=True)
        engine.start_scenario("cache", service="listing-service", auto_resolve=True)
        for _ in range(3):
            engine.tick()
        pool_inc = engine._incidents[("pool", "checkout-api")]
        cache_inc = engine._incidents[("cache", "listing-service")]
        self.assertEqual(pool_inc.tick_count, 3)
        self.assertEqual(cache_inc.tick_count, 3)
        self.assertEqual(pool_inc.phase, "climbing")
        self.assertEqual(cache_inc.phase, "failover")

    def test_health_for_only_reflects_targeted_service(self):
        engine.start_scenario("pool", service="payment-service", auto_resolve=True)
        engine.tick()
        payment_health = engine.health_for("payment-service")
        checkout_health = engine.health_for("checkout-api")
        self.assertGreater(payment_health.extra_latency_ms, 0)
        self.assertEqual(checkout_health.extra_latency_ms, 0)

    def test_service_snapshot_reflects_pool_incident(self):
        engine.start_scenario("pool", service="checkout-api", auto_resolve=True)
        for _ in range(5):
            engine.tick()
        snap = engine.service_snapshot("checkout-api")
        self.assertGreater(snap.pool_active, 118)

    def test_lifecycle_pool_progresses_and_resolves(self):
        engine.start_scenario("pool", auto_resolve=True)
        self.assertEqual(engine._incidents[("pool", "checkout-api")].phase, "climbing")
        for _ in range(39):
            engine.tick()
        self.assertIn(("pool", "checkout-api"), engine._incidents)
        engine.tick()  # tick 40 -> resolved -> removed
        self.assertNotIn(("pool", "checkout-api"), engine._incidents)
        self.assertEqual(engine.get_state(), [])

    def test_lifecycle_cache_progresses_and_resolves(self):
        engine.start_scenario("cache", auto_resolve=True)
        engine.tick()
        self.assertEqual(engine._incidents[("cache", "checkout-api")].phase, "failover")
        for _ in range(16):
            engine.tick()  # tick 17 -> still warming
        self.assertEqual(engine._incidents[("cache", "checkout-api")].phase, "warming")
        engine.tick()  # tick 18 -> resolved
        self.assertEqual(engine.get_state(), [])

    def test_lifecycle_fraud_progresses_and_resolves(self):
        engine.start_scenario("fraud", auto_resolve=True)
        self.assertEqual(engine._incidents[("fraud", "fraud-scoring-svc")].phase, "active")
        for _ in range(19):
            engine.tick()
        self.assertIn(("fraud", "fraud-scoring-svc"), engine._incidents)
        engine.tick()
        self.assertEqual(engine.get_state(), [])

    def test_pool_effects_increase_over_climbing(self):
        engine.start_scenario("pool", auto_resolve=True)
        initial = engine.health_for("checkout-api")
        for _ in range(8):
            engine.tick()
        mid = engine.health_for("checkout-api")
        self.assertGreater(mid.extra_latency_ms, initial.extra_latency_ms)

    def test_cache_effects_drop_hit_ratio(self):
        engine.start_scenario("cache", auto_resolve=True)
        engine.tick()
        snap = engine.service_snapshot("checkout-api")
        self.assertLess(snap.cache_hit, 0.60)

    def test_fraud_effects_have_high_inject_error_pct(self):
        engine.start_scenario("fraud", auto_resolve=True)
        engine.tick()
        health = engine.health_for("fraud-scoring-svc")
        self.assertGreaterEqual(health.inject_error_pct, 0.10)


# ===================================================================
# Cascade: downstream incident inflates caller's own metrics
# ===================================================================

class TestCascade(unittest.TestCase):
    """An incident on a downstream service measurably affects its caller,
    with no special-cased cross-service logic -- purely via traffic.py
    walking the call graph and folding health_for() at trace time."""

    def setUp(self):
        _reset_engine()

    @staticmethod
    def _error_count(logs: list, service: str, endpoint: str) -> tuple:
        """Return (total, errors) among spans matching (service, endpoint) in
        the given batch of log lines. Aggregating across many ticks (rather
        than reading one tick's /metrics snapshot) avoids flakiness from the
        small per-tick journey sample (JOURNEYS_PER_TICK=8)."""
        matching = [l for l in logs if l["service"] == service and l["endpoint"] == endpoint]
        errors = [l for l in matching if l["status_code"] >= 400]
        return len(matching), len(errors)

    def test_payment_pool_exhaustion_cascades_into_checkout_payment(self):
        # Baseline: no incident, accumulate several ticks' worth of traffic.
        baseline_total = baseline_errors = 0
        for _ in range(5):
            logs = traffic.tick()
            total, errors = self._error_count(logs, "checkout-api", "/payment")
            baseline_total += total
            baseline_errors += errors
        self.assertEqual(baseline_errors, 0, "no incident is active -- baseline should be error-free")

        # Trigger pool exhaustion on payment-service and run it into plateau,
        # where error injection is strongest (~6%), then sample many ticks so
        # the ~8 journeys/tick sample size doesn't make this test flaky.
        engine.start_scenario("pool", service="payment-service", auto_resolve=True)
        for _ in range(16):
            engine.tick()

        incident_total = incident_errors = 0
        for _ in range(10):
            engine.tick()
            logs = traffic.tick()
            total, errors = self._error_count(logs, "checkout-api", "/payment")
            incident_total += total
            incident_errors += errors

        self.assertGreater(
            incident_errors, 0,
            "checkout-api's /payment calls should show errors once payment-service "
            "is pool-exhausted, purely via the call-graph cascade "
            f"(sampled {incident_total} calls, {incident_errors} errors)",
        )

    def test_payment_service_own_error_rate_also_rises(self):
        engine.start_scenario("pool", service="payment-service", auto_resolve=True)
        for _ in range(16):
            engine.tick()

        total = errors = 0
        for _ in range(10):
            engine.tick()
            logs = traffic.tick()
            t, e = self._error_count(logs, "payment-service", "/charge")
            total += t
            errors += e
        self.assertGreater(errors, 0, f"sampled {total} calls, {errors} errors")


# ===================================================================
# Distributed trace correlation
# ===================================================================

class TestTraceCorrelation(unittest.TestCase):
    """One tick's traffic produces properly-linked traces."""

    def setUp(self):
        _reset_engine()

    def test_all_spans_in_one_trace_share_trace_id(self):
        logs = traffic.tick()
        by_trace = {}
        for line in logs:
            by_trace.setdefault(line["trace_id"], []).append(line)
        self.assertGreater(len(by_trace), 0)
        for trace_id, spans in by_trace.items():
            self.assertTrue(all(s["trace_id"] == trace_id for s in spans))

    def test_child_spans_reference_a_real_parent(self):
        logs = traffic.tick()
        span_ids = {line["span_id"] for line in logs}
        for line in logs:
            if line["parent_span_id"]:
                self.assertIn(line["parent_span_id"], span_ids,
                             "every non-empty parent_span_id should match a real span in this tick")

    def test_checkout_payment_child_span_is_payment_service(self):
        """A /payment span's children should include a payment-service /charge span."""
        logs = traffic.tick()
        by_span = {line["span_id"]: line for line in logs}
        payment_spans = [l for l in logs if l["service"] == "checkout-api" and l["endpoint"] == "/payment"]
        self.assertGreater(len(payment_spans), 0)
        found_child = False
        for p in payment_spans:
            children = [l for l in logs if l["parent_span_id"] == p["span_id"]]
            if any(c["service"] == "payment-service" and c["endpoint"] == "/charge" for c in children):
                found_child = True
        self.assertTrue(found_child, "expected at least one checkout-api/payment -> payment-service/charge child span")

    def test_every_span_has_request_id_and_user_id(self):
        logs = traffic.tick()
        self.assertGreater(len(logs), 0)
        for line in logs:
            self.assertTrue(line["request_id"])
            self.assertTrue(line["user_id"])

    def test_failed_journey_stops_early(self):
        """When checkout-api's own error injection fires, the journey should
        stop before reaching auth-service/logout for that trace. Sampled over
        many ticks at plateau (~6% injected error rate) so this isn't flaky
        against the small per-tick journey sample (JOURNEYS_PER_TICK=8)."""
        engine.start_scenario("pool", service="checkout-api", auto_resolve=True)
        for _ in range(15):
            engine.tick()  # reach plateau, where errors are near-guaranteed

        saw_incomplete_journey = False
        for _ in range(15):
            engine.tick()
            logs = traffic.tick()
            by_trace = {}
            for line in logs:
                by_trace.setdefault(line["trace_id"], []).append(line)
            if any(
                not any(s["service"] == "auth-service" and s["endpoint"] == "/logout" for s in spans)
                for spans in by_trace.values()
            ):
                saw_incomplete_journey = True
                break

        self.assertTrue(saw_incomplete_journey, "expected at least one journey to stop before logout across 15 sampled ticks")


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
        response = client.post("/api/incidents/pool/trigger", json={"auto_resolve": True})
        self.assertIn("x-request-id", response.headers)

    def test_state_response_has_x_request_id_header(self):
        response = client.get("/api/incidents/state")
        self.assertIn("x-request-id", response.headers)

    def test_resolve_response_has_x_request_id_header(self):
        engine.start_scenario("pool", auto_resolve=True)
        response = client.post("/api/incidents/pool/resolve")
        self.assertIn("x-request-id", response.headers)

    def test_request_id_in_body_matches_header_format(self):
        response = client.get("/health")
        body_rid = response.json().get("request_id", "")
        header_rid = response.headers.get("x-request-id", "")
        self.assertEqual(len(body_rid), 12)
        self.assertEqual(len(header_rid), 12)


class TestMetricsNoRequestId(unittest.TestCase):
    """The /metrics endpoint should NOT have X-Request-ID header."""

    def test_metrics_does_not_have_x_request_id_header(self):
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
        self.assertEqual(data["info"]["version"], "4.0.0")

    def test_openapi_json_lists_all_paths(self):
        response = client.get("/openapi.json")
        paths = response.json()["paths"]
        expected_paths = [
            "/health",
            "/metrics",
            "/api/services",
            "/api/incidents/{kind}/trigger",
            "/api/incidents/{kind}/resolve",
            "/api/incidents/trigger-random",
            "/api/incidents/state",
        ]
        for path in expected_paths:
            self.assertIn(path, paths, f"Missing path: {path}")

    def test_openapi_json_has_request_models(self):
        response = client.get("/openapi.json")
        schemas = response.json()["components"]["schemas"]
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
        response = client.post("/api/incidents//trigger", json={"auto_resolve": True})
        self.assertIn(response.status_code, {404, 405})

    def test_trigger_with_extra_fields_ignored(self):
        response = client.post("/api/incidents/pool/trigger",
                               json={"auto_resolve": True, "extra_field": "ignored"})
        self.assertEqual(response.status_code, 200)

    def test_concurrent_triggers_dont_crash(self):
        for _ in range(10):
            response = client.post("/api/incidents/pool/trigger", json={"auto_resolve": True})
            self.assertEqual(response.status_code, 200)
            _reset_engine()

    def test_trigger_then_state_then_resolve_cycle(self):
        resp = client.post("/api/incidents/pool/trigger", json={"auto_resolve": True})
        self.assertEqual(resp.status_code, 200)

        resp = client.get("/api/incidents/state")
        self.assertEqual(resp.json()["count"], 1)

        engine.tick()

        resp = client.get("/api/incidents/state")
        self.assertEqual(resp.json()["count"], 1)

        resp = client.post("/api/incidents/pool/resolve")
        self.assertEqual(resp.json()["status"], "resolved")

        resp = client.get("/api/incidents/state")
        self.assertEqual(resp.json()["count"], 0)


# ===================================================================
# Edge Cases: Empty Engine State
# ===================================================================

class TestEdgeCases(unittest.TestCase):
    """Edge cases around engine state boundaries."""

    def setUp(self):
        _reset_engine()

    def test_state_after_engine_forced_reset(self):
        engine.start_scenario("pool", auto_resolve=True)
        _reset_engine()
        data = client.get("/api/incidents/state").json()
        self.assertEqual(data["count"], 0)

    def test_multiple_resolves_return_ok(self):
        engine.start_scenario("pool", auto_resolve=True)
        client.post("/api/incidents/pool/resolve")
        response = client.post("/api/incidents/pool/resolve")
        data = response.json()
        self.assertEqual(data["status"], "no_active_incident")

    def test_all_kinds_can_be_triggered_and_resolved(self):
        for kind in ("pool", "cache", "fraud"):
            _reset_engine()
            resp = client.post(f"/api/incidents/{kind}/trigger", json={"auto_resolve": False})
            self.assertEqual(resp.status_code, 200, f"Failed to trigger {kind}")
            self.assertEqual(resp.json()["kind"], kind)

            resp = client.post(f"/api/incidents/{kind}/resolve")
            self.assertEqual(resp.status_code, 200, f"Failed to resolve {kind}")
            self.assertEqual(resp.json()["status"], "resolved")


if __name__ == "__main__":
    unittest.main()
