"""
Test the IncidentPilot Postman collection against live services.

Reads the Postman collection JSON, makes real HTTP requests to each endpoint,
and validates responses against the Postman test-script assertions.

Requires:
    - Docker stack running (flask-generator:5001, Prometheus:9090, Loki:3100, Grafana:3000)
    - Gradio UI running (port 7860, optional for smoke test)

Usage:
    uv run python -m pytest tests/test_postman_collection.py -v --timeout=30
"""

import json
import os
import re
import time
from pathlib import Path

import pytest
import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
COLLECTION_PATH = PROJECT_ROOT / "docs" / "postman" / "IncidentPilot.postman_collection.json"

BASE_URLS = {
    "api_url": "http://localhost:5001",
    "prometheus_url": "http://localhost:9090",
    "loki_url": "http://localhost:3100",
    "grafana_url": "http://localhost:3000",
    "gradio_url": "http://127.0.0.1:7860",
}

REQUEST_TIMEOUT = 10
REQ_ID_PATTERN = re.compile(r"^[a-f0-9]{12}$")

with open(COLLECTION_PATH) as f:
    COLLECTION = json.load(f)


def _resolve_url(raw_url: str) -> str:
    """Replace {{variable}} placeholders with actual base URLs."""
    now = time.time()
    for key, value in BASE_URLS.items():
        raw_url = raw_url.replace("{{" + key + "}}", value)
    # Substitute template variables for Prometheus/Loki timestamps
    raw_url = raw_url.replace("{{start_ts}}", str(int(now - 900)))  # 15 min ago
    raw_url = raw_url.replace("{{end_ts}}", str(int(now)))
    ns_now = int(now * 1_000_000_000)
    ns_start = int((now - 900) * 1_000_000_000)
    raw_url = raw_url.replace("{{start_ts_ns}}", str(ns_start))
    raw_url = raw_url.replace("{{end_ts_ns}}", str(ns_now))
    return raw_url


def _find(section_name: str, request_name: str) -> dict:
    """Find a request dict by section name and request name."""
    for section in COLLECTION.get("item", []):
        if section.get("name") == section_name:
            for req in section.get("item", []):
                if req.get("name") == request_name:
                    return req
    raise KeyError(f"Request {request_name!r} not found in section {section_name!r}")


def _get_request_details(req: dict) -> tuple:
    """Extract method, url, headers, body from a Postman request dict."""
    r = req.get("request", {})
    method = r.get("method", "GET")
    url_raw = r.get("url", {}).get("raw", "")
    url = _resolve_url(url_raw)
    headers = {h["key"]: h["value"] for h in r.get("header", []) if h.get("key")}
    body = None
    if r.get("body", {}).get("mode") == "raw":
        body = r["body"]["raw"]
    return method, url, headers, body


def _make_request(method: str, url: str, headers: dict = None, body: str = None,
                  timeout: int = REQUEST_TIMEOUT) -> requests.Response:
    kwargs = {"timeout": timeout, "allow_redirects": False}
    if headers:
        kwargs["headers"] = headers
    if body and headers and headers.get("Content-Type") == "application/json":
        kwargs["json"] = json.loads(body)
    elif body:
        kwargs["data"] = body
    return getattr(requests, method.lower())(url, **kwargs)


# ==============================================================
# Tests
# ==============================================================

class TestPostmanHealthChecks:
    """Section 1: Health Checks (4 endpoints)."""

    def test_health_fastapi(self):
        req = _find("\U0001f7e2 1. Health Checks", "FastAPI Generator \u2014 /health")
        m, u, h, b = _get_request_details(req)
        resp = _make_request(m, u, h, b)
        assert resp.status_code == 200
        data = resp.json()
        assert data["service"] == "flask-generator"
        assert data["status"] == "ok"
        assert REQ_ID_PATTERN.match(data["request_id"])
        assert REQ_ID_PATTERN.match(resp.headers.get("X-Request-ID", ""))

    def test_health_prometheus(self):
        req = _find("\U0001f7e2 1. Health Checks", "Prometheus \u2014 /-/ready")
        m, u, h, b = _get_request_details(req)
        resp = _make_request(m, u, h, b)
        assert resp.status_code == 200
        assert "Prometheus" in resp.text

    def test_health_loki(self):
        req = _find("\U0001f7e2 1. Health Checks", "Loki \u2014 /ready")
        m, u, h, b = _get_request_details(req)
        resp = _make_request(m, u, h, b)
        assert resp.status_code == 200

    def test_health_grafana(self):
        req = _find("\U0001f7e2 1. Health Checks", "Grafana \u2014 /api/health")
        m, u, h, b = _get_request_details(req)
        resp = _make_request(m, u, h, b)
        assert resp.status_code == 200
        data = resp.json()
        assert data["database"] == "ok"


class TestPostmanIncidents:
    """Section 2: Incident Simulator (12 endpoints)."""

    def test_trigger_pool(self):
        req = _find("\u26a1 2. Incident Simulator (FastAPI)", "Trigger \u2014 Pool Exhaustion")
        m, u, h, b = _get_request_details(req)
        resp = _make_request(m, u, h, b)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"
        assert data["kind"] == "pool"
        assert data["phase"] == "climbing"
        assert REQ_ID_PATTERN.match(data["request_id"])
        assert REQ_ID_PATTERN.match(resp.headers.get("X-Request-ID", ""))

    def test_trigger_cache(self):
        req = _find("\u26a1 2. Incident Simulator (FastAPI)", "Trigger \u2014 Cache Failover")
        m, u, h, b = _get_request_details(req)
        resp = _make_request(m, u, h, b)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"
        assert data["kind"] == "cache"
        assert REQ_ID_PATTERN.match(data["request_id"])
        assert REQ_ID_PATTERN.match(resp.headers.get("X-Request-ID", ""))

    def test_trigger_fraud(self):
        req = _find("\u26a1 2. Incident Simulator (FastAPI)", "Trigger \u2014 Fraud Scoring Outage")
        m, u, h, b = _get_request_details(req)
        resp = _make_request(m, u, h, b)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"
        assert data["kind"] == "fraud"
        assert data["phase"] == "active"
        assert REQ_ID_PATTERN.match(data["request_id"])
        assert REQ_ID_PATTERN.match(resp.headers.get("X-Request-ID", ""))

    def test_trigger_random(self):
        req = _find("\u26a1 2. Incident Simulator (FastAPI)", "Trigger \u2014 Random Scenario")
        m, u, h, b = _get_request_details(req)
        resp = _make_request(m, u, h, b)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"
        assert data["kind"] in ("pool", "cache", "fraud")
        assert REQ_ID_PATTERN.match(data["request_id"])

    def test_trigger_invalid_kind(self):
        req = _find("\u26a1 2. Incident Simulator (FastAPI)", "Trigger \u2014 Invalid Kind (Error Test)")
        m, u, h, b = _get_request_details(req)
        resp = _make_request(m, u, h, b)
        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data
        assert "unknown incident kind" in data["error"]
        assert REQ_ID_PATTERN.match(data["request_id"])

    def test_resolve_by_kind(self):
        # First trigger an incident so resolve has something to do
        requests.post(f"{BASE_URLS['api_url']}/api/incidents/pool/trigger",
                       json={"auto_resolve": True}, timeout=5)
        time.sleep(0.5)

        req = _find("\u26a1 2. Incident Simulator (FastAPI)", "Resolve \u2014 by Kind")
        m, u, h, b = _get_request_details(req)
        resp = _make_request(m, u, h, b)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("resolved", "no_active_incident")
        if data["status"] == "resolved":
            assert data["phase"] == "resolved"
        assert REQ_ID_PATTERN.match(data["request_id"])
        assert REQ_ID_PATTERN.match(resp.headers.get("X-Request-ID", ""))

    def test_resolve_current(self):
        req = _find("\u26a1 2. Incident Simulator (FastAPI)", "Resolve \u2014 Current (any kind)")
        m, u, h, b = _get_request_details(req)
        resp = _make_request(m, u, h, b)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("resolved", "no_active_incident")
        assert REQ_ID_PATTERN.match(data["request_id"])

    def test_incident_state(self):
        req = _find("\u26a1 2. Incident Simulator (FastAPI)", "Get Incident State")
        m, u, h, b = _get_request_details(req)
        resp = _make_request(m, u, h, b)
        assert resp.status_code == 200
        data = resp.json()
        assert "kind" in data
        assert "phase" in data
        assert "tick_count" in data
        assert "p99_latency_ms" in data
        assert "error_rate_pct" in data
        assert "active_connections" in data
        assert REQ_ID_PATTERN.match(data["request_id"])

    def test_fastapi_metrics(self):
        req = _find("\u26a1 2. Incident Simulator (FastAPI)", "FastAPI Prometheus Metrics (raw)")
        m, u, h, b = _get_request_details(req)
        resp = _make_request(m, u, h, b)
        assert resp.status_code == 200
        assert "text/plain" in resp.headers.get("Content-Type", "")
        body = resp.text
        assert "checkout_p99_latency_ms" in body
        assert "checkout_error_rate_pct" in body
        assert "checkout_active_connections" in body
        assert "checkout_max_connections" in body
        assert "checkout_cache_hit_ratio" in body
        assert resp.headers.get("X-Request-ID") is None

    def test_openapi_swagger_ui(self):
        req = _find("\u26a1 2. Incident Simulator (FastAPI)", "FastAPI OpenAPI \u2014 Swagger UI")
        m, u, h, b = _get_request_details(req)
        resp = _make_request(m, u, h, b)
        assert resp.status_code in (200, 302)
        assert "swagger" in resp.text.lower()

    def test_openapi_redoc(self):
        req = _find("\u26a1 2. Incident Simulator (FastAPI)", "FastAPI OpenAPI \u2014 ReDoc")
        m, u, h, b = _get_request_details(req)
        resp = _make_request(m, u, h, b)
        assert resp.status_code in (200, 302)
        assert "redoc" in resp.text.lower()

    def test_openapi_json_spec(self):
        req = _find("\u26a1 2. Incident Simulator (FastAPI)", "FastAPI OpenAPI \u2014 JSON Spec")
        m, u, h, b = _get_request_details(req)
        resp = _make_request(m, u, h, b)
        assert resp.status_code == 200
        spec = resp.json()
        assert "openapi" in spec
        assert spec["info"]["title"] == "Incident Generator"
        assert spec["info"]["version"] == "3.0.0"
        expected_paths = ["/health", "/metrics", "/api/incidents/{kind}/trigger",
                          "/api/incidents/{kind}/resolve", "/api/incidents/trigger-random",
                          "/api/incidents/state"]
        for p in expected_paths:
            assert p in spec["paths"], f"Missing path: {p}"


class TestPostmanPrometheus:
    """Section 3: Prometheus Queries (3 endpoints)."""

    def test_all_metrics(self):
        req = _find("\U0001f4ca 3. Prometheus Queries", "Query \u2014 All Checkout Metrics (label matcher regex)")
        m, u, h, b = _get_request_details(req)
        resp = _make_request(m, u, h, b)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert len(data["data"]["result"]) >= 1

    def test_instant(self):
        req = _find("\U0001f4ca 3. Prometheus Queries", "Query \u2014 Single Instant Value")
        m, u, h, b = _get_request_details(req)
        resp = _make_request(m, u, h, b)
        assert resp.status_code == 200

    def test_targets(self):
        req = _find("\U0001f4ca 3. Prometheus Queries", "Query \u2014 Prometheus Targets")
        m, u, h, b = _get_request_details(req)
        resp = _make_request(m, u, h, b)
        assert resp.status_code == 200
        data = resp.json()
        targets = data.get("data", {}).get("activeTargets", [])
        flask_gen = [t for t in targets if "flask-generator" in t.get("labels", {}).get("job", "")]
        assert len(flask_gen) > 0, "Expected flask-generator target in Prometheus"
        assert all(t["health"] == "up" for t in flask_gen), "flask-generator target should be UP"


class TestPostmanLoki:
    """Section 4: Loki Log Queries (4 endpoints)."""

    def test_all_logs(self):
        req = _find("\U0001f4dd 4. Loki Log Queries", "Query \u2014 All Logs (15m window)")
        m, u, h, b = _get_request_details(req)
        resp = _make_request(m, u, h, b)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert "result" in data["data"]

    def test_error_logs(self):
        req = _find("\U0001f4dd 4. Loki Log Queries", "Query \u2014 Error Logs Only")
        m, u, h, b = _get_request_details(req)
        resp = _make_request(m, u, h, b)
        assert resp.status_code == 200

    def test_pool_logs(self):
        req = _find("\U0001f4dd 4. Loki Log Queries", "Query \u2014 Pool Timeout Logs")
        m, u, h, b = _get_request_details(req)
        resp = _make_request(m, u, h, b)
        assert resp.status_code == 200

    def test_fraud_logs(self):
        req = _find("\U0001f4dd 4. Loki Log Queries", "Query \u2014 Fraud Logs")
        m, u, h, b = _get_request_details(req)
        resp = _make_request(m, u, h, b)
        assert resp.status_code == 200


class TestPostmanGrafana:
    """Section 5: Grafana Dashboard APIs (4 endpoints)."""

    def test_search_dashboards(self):
        req = _find("\U0001f4c8 5. Grafana Dashboard APIs", "Grafana \u2014 Search Dashboards")
        m, u, h, b = _get_request_details(req)
        resp = _make_request(m, u, h, b)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_dashboard_by_uid(self):
        # First find the actual UID from Grafana's search API
        search = requests.get(
            f"{BASE_URLS['grafana_url']}/api/search",
            headers={"Authorization": "Basic YWRtaW46YWRtaW4="},
            timeout=5
        )
        assert search.status_code == 200
        dashboards = search.json()
        assert len(dashboards) > 0, "No dashboards found in Grafana"
        # Use the first dashboard's UID
        uid = dashboards[0]["uid"]
        resp = requests.get(
            f"{BASE_URLS['grafana_url']}/api/dashboards/uid/{uid}",
            headers={"Authorization": "Basic YWRtaW46YWRtaW4="},
            timeout=5
        )
        assert resp.status_code == 200
        assert "dashboard" in resp.json()

    def test_datasources(self):
        req = _find("\U0001f4c8 5. Grafana Dashboard APIs", "Grafana \u2014 List Datasources")
        m, u, h, b = _get_request_details(req)
        resp = _make_request(m, u, h, b)
        assert resp.status_code == 200

    def test_home(self):
        req = _find("\U0001f4c8 5. Grafana Dashboard APIs", "Grafana \u2014 Home Dashboard (redirect)")
        m, u, h, b = _get_request_details(req)
        resp = _make_request(m, u, h, b)
        assert resp.status_code in (200, 301, 302)


class TestPostmanGradio:
    """Extra: Gradio UI smoke test."""

    def test_gradio_accessible(self):
        resp = requests.get(BASE_URLS["gradio_url"], timeout=REQUEST_TIMEOUT)
        assert resp.status_code == 200
        assert "gradio" in resp.text.lower() or "incidentpilot" in resp.text.lower()


class TestPostmanLifecycle:
    """End-to-end incident lifecycle."""

    def test_pool_lifecycle(self):
        # 1. Resolve anything active
        requests.post(f"{BASE_URLS['api_url']}/api/incidents/current/resolve", timeout=5)
        time.sleep(1)

        # 2. Trigger pool
        resp = requests.post(f"{BASE_URLS['api_url']}/api/incidents/pool/trigger",
                              json={"auto_resolve": True}, timeout=5)
        assert resp.status_code == 200
        assert resp.json()["status"] == "started"
        assert resp.json()["kind"] == "pool"

        # 3. Verify state
        resp = requests.get(f"{BASE_URLS['api_url']}/api/incidents/state", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert data["kind"] == "pool"
        assert data["phase"] == "climbing"

        # 4. Resolve
        resp = requests.post(f"{BASE_URLS['api_url']}/api/incidents/pool/resolve", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "resolved"

        # 5. Verify baseline
        resp = requests.get(f"{BASE_URLS['api_url']}/api/incidents/state", timeout=5)
        assert resp.status_code == 200
        assert resp.json()["kind"] == "none"
