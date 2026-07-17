"""
app.py

Flask application entry point.  Starts the background tick loop and exposes:

  GET  /metrics                 — Prometheus scrape endpoint
  GET  /health                  — Health check (uses HealthResponse)
  POST /api/incidents/<kind>/trigger  — Start a scenario (uses TriggerRequest)
  POST /api/incidents/<kind>/resolve  — Force-resolve
  POST /api/incidents/trigger-random  — Random scenario
  GET  /api/incidents/state          — Current state

Every API response includes a ``request_id`` field for cross-service log
correlation.  The request ID also flows into the log format and intoLoki
log entries emitted by ``log_generator.emit_logs()``.

Usage:
    python app.py                              (accelerated, default)
    TICK_MODE=realtime python app.py           (real-time)
    TICK_INTERVAL=5 python app.py              (custom interval)
"""

import logging
import os
import threading
import time
import uuid
from contextvars import ContextVar

from flask import Flask, g, jsonify, request, Response
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

from config import (
    TICK_INTERVAL_SECONDS,
    TICK_MODE,
    SERVICE,
    TriggerRequest,
    TriggerResponse,
    ResolveResponse,
    HealthResponse,
    StateResponse,
    ErrorResponse,
)
from pydantic import ValidationError

from incident_scenarios import IncidentEngine
from metrics_exporter import REGISTRY, update_all
from log_generator import LogGenerator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Request-ID context (thread-safe, per-API-call)
# ---------------------------------------------------------------------------

_request_id_ctx: ContextVar[str] = ContextVar("flask_request_id", default="")


def _get_rid() -> str:
    """Return the request ID for the current thread (or "-" if none)."""
    return _request_id_ctx.get() or "-"


def _generate_rid() -> str:
    """Generate a short unique request ID (12 hex chars)."""
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# Custom log-record factory — injects request_id into EVERY LogRecord
# ---------------------------------------------------------------------------
#
# We use ``setLogRecordFactory()`` instead of a logging.Filter because
# filters attached to the root logger are NOT consulted when a child
# logger (e.g. ``werkzeug``) emits a record.  The factory wraps every
# single LogRecord at creation time, regardless of logger hierarchy,
# so the formatter can safely use ``%(request_id)s``.

_old_factory = logging.getLogRecordFactory()


def _record_factory(*args, **kwargs) -> logging.LogRecord:
    record = _old_factory(*args, **kwargs)
    record.request_id = _get_rid()
    return record


logging.setLogRecordFactory(_record_factory)


class _HealthFilter(logging.Filter):
    """Suppress health-check log spam by filtering out /health requests."""
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return "GET /health" not in msg and "/api/incidents/state" not in msg


logging.getLogger("werkzeug").addFilter(_HealthFilter())

# ---------------------------------------------------------------------------
# App initialisation
# ---------------------------------------------------------------------------

app = Flask(__name__)
engine = IncidentEngine()
log_generator = LogGenerator()

# ---------------------------------------------------------------------------
# Before-request handler — generate a request ID for every API call
# ---------------------------------------------------------------------------

@app.before_request
def _assign_request_id():
    """Generate a unique request ID for every incoming API request.

    Excludes the Prometheus /metrics scrape endpoint (it returns plain
    text, not JSON) and the tick-loop thread (background, no API call).
    """
    if request.path == "/metrics":
        return
    rid = _generate_rid()
    _request_id_ctx.set(rid)
    g.request_id = rid

# ---------------------------------------------------------------------------
# Background tick loop
# ---------------------------------------------------------------------------


def _tick_loop() -> None:
    """Background thread: advances the scenario state every TICK_INTERVAL_SECONDS."""
    while True:
        try:
            engine.tick()
            state = engine.get_state()
            if state:
                logger.debug("Tick: kind=%s phase=%s tick=%d latency=%.0f err=%.2f%% conns=%d",
                              state.kind, state.phase, state.tick_count,
                              state.p99_latency_ms, state.error_rate_pct,
                              state.active_connections)
                update_all(state)
                log_generator.emit_logs(state)
            else:
                logger.debug("Tick: no active incident")
        except Exception as exc:
            logger.error("Tick loop error: %s", exc, exc_info=True)
        time.sleep(TICK_INTERVAL_SECONDS)


_thread = threading.Thread(target=_tick_loop, daemon=True, name="tick-loop")
_thread.start()

logger.info(
    "Flask generator started: TICK_MODE=%s, interval=%ds, service=%s",
    TICK_MODE, TICK_INTERVAL_SECONDS, SERVICE,
)

# ---------------------------------------------------------------------------
# Helper: build response with request_id
# ---------------------------------------------------------------------------

def _json_resp(model_instance, status=200):
    """Serialise a Pydantic model to a JSON response with request_id injected."""
    data = model_instance.model_dump(mode="json")
    data["request_id"] = _get_rid()
    return jsonify(data), status


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.route("/health")
def health():
    """Health check — returns a Pydantic HealthResponse."""
    state = engine.get_state_dict()
    active = state.get("kind") if state.get("kind") != "none" else None
    logger.debug("GET /health — active_incident=%s", active)
    return _json_resp(
        HealthResponse(service="flask-generator", active_incident=active)
    )


@app.route("/metrics")
def metrics():
    """Prometheus scrape endpoint — renders all registered metrics."""
    return Response(generate_latest(REGISTRY), mimetype=CONTENT_TYPE_LATEST)


@app.route("/api/incidents/<kind>/trigger", methods=["POST"])
def trigger_incident(kind):
    """Start a scenario of the given kind.

    Parses the optional JSON body with Pydantic ``TriggerRequest``.
    """
    body = request.get_json(silent=True) or {}
    rid = _get_rid()
    logger.info("POST /api/incidents/%s/trigger [req=%s] — body=%s", kind, rid, body)
    try:
        req = TriggerRequest(**body)
    except ValidationError:
        logger.warning("Invalid trigger request body for kind=%s [req=%s]: %s", kind, rid, body)
        return _json_resp(ErrorResponse(error="Invalid request body"), 400)

    result = engine.start_scenario(kind, auto_resolve=req.auto_resolve, request_id=rid)
    if not result.success:
        logger.warning("Trigger failed for kind=%s [req=%s]: %s", kind, rid, result.error)
        return _json_resp(ErrorResponse(error=result.error), 400)
    logger.info("Scenario started [req=%s]: kind=%s auto_resolve=%s", rid, kind, req.auto_resolve)
    return _json_resp(
        TriggerResponse(
            status="started",
            kind=kind,
            phase=result.data.get("phase", "unknown"),
            tick_count=result.data.get("tick_count", 0),
        )
    )


@app.route("/api/incidents/<kind>/resolve", methods=["POST"])
def resolve_incident(kind):
    """Force-resolve the active scenario (must match kind or use 'current')."""
    resolve_kind = kind if kind != "current" else None
    rid = _get_rid()
    logger.info("POST /api/incidents/%s/resolve [req=%s]", kind, rid)
    result = engine.resolve(kind=resolve_kind)
    logger.info("Resolve result [req=%s]: %s", rid, result.data)
    return _json_resp(
        ResolveResponse(
            status=result.data.get("status", "ok"),
            kind=result.data.get("kind"),
            phase=result.data.get("phase"),
        )
    )


@app.route("/api/incidents/trigger-random", methods=["POST"])
def trigger_random():
    """Start a randomly selected scenario."""
    import random as _random

    kind = _random.choice(list(engine.VALID_KINDS))
    rid = _get_rid()
    logger.info("POST /api/incidents/trigger-random [req=%s] — selected kind=%s", rid, kind)
    result = engine.start_scenario(kind, auto_resolve=True, request_id=rid)
    if not result.success:
        logger.warning("Random trigger failed [req=%s]: %s", rid, result.error)
        return _json_resp(ErrorResponse(error=result.error), 400)
    return _json_resp(
        TriggerResponse(
            status="started",
            kind=kind,
            phase=result.data.get("phase", "unknown"),
            tick_count=result.data.get("tick_count", 0),
        )
    )


@app.route("/api/incidents/state")
def incident_state():
    """Return the current incident state as a Pydantic StateResponse."""
    rid = _get_rid()
    state = engine.get_state()
    if state:
        logger.debug("GET /api/incidents/state [req=%s] — kind=%s phase=%s tick=%d",
                      rid, state.kind, state.phase, state.tick_count)
        return jsonify(state.public_dict())
    logger.debug("GET /api/incidents/state [req=%s] — no active incident", rid)
    return _json_resp(StateResponse())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  [req=%(request_id)s]  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    host = os.getenv("FLASK_HOST", "0.0.0.0")
    port = int(os.getenv("FLASK_PORT", "5000"))
    logger.info("Starting Flask server on %s:%s", host, port)
    app.run(host=host, port=port, debug=False, use_reloader=False)
