"""
app.py

FastAPI application entry point.  Starts the background tick loop and exposes:

  GET  /metrics                       — Prometheus scrape endpoint
  GET  /health                        — Health check (uses HealthResponse)
  GET  /api/services                  — Topology catalog + canonical journey
  POST /api/incidents/{kind}/trigger  — Start a scenario (uses TriggerRequest)
  POST /api/incidents/{kind}/resolve  — Force-resolve
  POST /api/incidents/trigger-random  — Random scenario on a random service
  GET  /api/incidents/state           — Every currently-active incident

Every API response includes a ``request_id`` field for cross-service log
correlation. This is a *different* ID space from the ``trace_id``/per-span
``request_id`` that ``traffic.py`` mints for simulated user journeys — this
one identifies an operator's API call (a trigger/resolve/health request),
not a simulated end-user request.

FastAPI automatically generates OpenAPI docs:
  - Swagger UI: http://localhost:5001/docs
  - ReDoc:      http://localhost:5001/redoc

Usage:
    python app.py                              (accelerated, default)
    TICK_MODE=realtime python app.py           (real-time)
    TICK_INTERVAL=5 python app.py              (custom interval)
"""

import logging
import os
import random as _random
import threading
import time
import uuid
from contextvars import ContextVar
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

import topology
from config import (
    TICK_INTERVAL_SECONDS,
    TICK_MODE,
    VALID_KINDS,
    TriggerRequest,
    TriggerResponse,
    ResolveResponse,
    HealthResponse,
    ActiveIncidentState,
    StateResponse,
    EndpointInfo,
    ServiceInfo,
    ServicesResponse,
    ErrorResponse,
    service_supports,
)

from incident_scenarios import IncidentEngine
from metrics_exporter import REGISTRY
from traffic import TrafficGenerator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Request-ID context (thread-safe, per-API-call)
# ---------------------------------------------------------------------------

_request_id_ctx: ContextVar[str] = ContextVar("fastapi_request_id", default="")


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
# logger (e.g. ``uvicorn``) emits a record.  The factory wraps every
# single LogRecord at creation time, regardless of logger hierarchy,
# so the formatter can safely use ``%(request_id)s``.

_old_factory = logging.getLogRecordFactory()


def _record_factory(*args, **kwargs) -> logging.LogRecord:
    record = _old_factory(*args, **kwargs)
    record.request_id = _get_rid()
    return record


logging.setLogRecordFactory(_record_factory)

# ---------------------------------------------------------------------------
# App initialisation
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Incident Generator",
    description="Tick-driven multi-service incident simulator for the IncidentPilot AI copilot.",
    version="4.0.0",
)

engine = IncidentEngine()
traffic = TrafficGenerator(engine)

# ---------------------------------------------------------------------------
# Middleware — generate a request ID for every API call
# ---------------------------------------------------------------------------


@app.middleware("http")
async def _assign_request_id(request: Request, call_next):
    """Generate a unique request ID for every incoming API request.

    Excludes the Prometheus /metrics scrape endpoint (it returns plain
    text, not JSON) and the tick-loop thread (background, no API call).
    """
    if request.url.path != "/metrics":
        rid = _generate_rid()
        _request_id_ctx.set(rid)

    response = await call_next(request)

    # Add request_id header to all JSON responses for tracing
    if response.headers.get("content-type", "").startswith("application/json"):
        response.headers["X-Request-ID"] = _get_rid()

    return response


# ---------------------------------------------------------------------------
# Background tick loop
# ---------------------------------------------------------------------------


def _tick_loop() -> None:
    """Background thread: advances every active incident, then runs one
    tick's worth of simulated user traffic through the topology."""
    while True:
        try:
            engine.tick()
            active = engine.get_state()
            if active:
                logger.debug("Tick: %d active incident(s): %s",
                              len(active), [(i["kind"], i["service"], i["phase"]) for i in active])
            traffic.tick()
        except Exception as exc:
            logger.error("Tick loop error: %s", exc, exc_info=True)
        time.sleep(TICK_INTERVAL_SECONDS)


_thread = threading.Thread(target=_tick_loop, daemon=True, name="tick-loop")
_thread.start()

logger.info(
    "FastAPI generator started: TICK_MODE=%s, interval=%ds, services=%s",
    TICK_MODE, TICK_INTERVAL_SECONDS, topology.all_service_names(),
)

# ---------------------------------------------------------------------------
# Helper: build response with request_id
# ---------------------------------------------------------------------------


def _json_resp(model_instance, status=200):
    """Serialise a Pydantic model to a JSON response with request_id injected."""
    data = model_instance.model_dump(mode="json")
    data["request_id"] = _get_rid()
    return JSONResponse(content=data, status_code=status)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
def health():
    """Health check — returns a Pydantic HealthResponse."""
    active = engine.get_state()
    active_labels = [f"{i['kind']}@{i['service']}" for i in active]
    logger.debug("GET /health — active_incidents=%s", active_labels)
    return _json_resp(
        HealthResponse(service="incident-generator", active_incidents=active_labels)
    )


@app.get("/metrics")
def metrics():
    """Prometheus scrape endpoint — renders all registered metrics."""
    return PlainTextResponse(
        content=generate_latest(REGISTRY),
        media_type=CONTENT_TYPE_LATEST,
    )


@app.get("/api/services")
def services():
    """Return the topology catalog + canonical user journey."""
    rid = _get_rid()
    infos = [
        ServiceInfo(
            name=svc.name,
            user_facing=svc.user_facing,
            uses_db_pool=svc.uses_db_pool,
            uses_cache=svc.uses_cache,
            endpoints=[
                EndpointInfo(method=ep.method, path=ep.path, calls=[list(c) for c in ep.calls])
                for ep in svc.endpoints.values()
            ],
        )
        for svc in topology.SERVICES.values()
    ]
    return _json_resp(
        ServicesResponse(
            services=[i.model_dump() for i in infos],
            journey=[list(step) for step in topology.JOURNEY],
        )
    )


@app.post("/api/incidents/{kind}/trigger")
def trigger_incident(kind: str, body: Optional[TriggerRequest] = None):
    """Start a scenario of the given kind, optionally targeting a specific service.

    FastAPI validates the optional JSON body using the Pydantic ``TriggerRequest`` model.
    """
    rid = _get_rid()
    req = body or TriggerRequest()
    logger.info("POST /api/incidents/%s/trigger [req=%s] — service=%s auto_resolve=%s",
                kind, rid, req.service or "(default)", req.auto_resolve)

    result = engine.start_scenario(
        kind, service=req.service, auto_resolve=req.auto_resolve, request_id=rid,
    )
    if not result.success:
        logger.warning("Trigger failed for kind=%s [req=%s]: %s", kind, rid, result.error)
        return _json_resp(ErrorResponse(error=result.error or "Unknown error"), 400)

    logger.info("Scenario started [req=%s]: kind=%s service=%s auto_resolve=%s",
                rid, kind, result.data.get("service"), req.auto_resolve)
    return _json_resp(TriggerResponse(**result.data))


@app.post("/api/incidents/{kind}/resolve")
def resolve_incident(kind: str, service: Optional[str] = None):
    """Force-resolve active incident(s). ``kind='current'`` matches any kind;
    an unset ``service`` query param matches any service."""
    resolve_kind = None if kind == "current" else kind
    rid = _get_rid()
    logger.info("POST /api/incidents/%s/resolve [req=%s] service=%s", kind, rid, service or "(any)")
    result = engine.resolve(kind=resolve_kind, service=service)
    logger.info("Resolve result [req=%s]: %s", rid, result.data)
    return _json_resp(ResolveResponse(**result.data))


@app.post("/api/incidents/trigger-random")
def trigger_random():
    """Start a randomly selected scenario on a randomly selected supporting service."""
    kind = _random.choice(list(VALID_KINDS))
    candidates = [s for s in topology.all_service_names() if service_supports(kind, s)]
    service = _random.choice(candidates)
    rid = _get_rid()
    logger.info("POST /api/incidents/trigger-random [req=%s] — selected kind=%s service=%s",
                rid, kind, service)
    result = engine.start_scenario(kind, service=service, auto_resolve=True, request_id=rid)
    if not result.success:
        logger.warning("Random trigger failed [req=%s]: %s", rid, result.error)
        return _json_resp(ErrorResponse(error=result.error or "Unknown error"), 400)
    return _json_resp(TriggerResponse(**result.data))


@app.get("/api/incidents/state")
def incident_state():
    """Return every currently-active incident."""
    rid = _get_rid()
    active = engine.get_state()
    logger.debug("GET /api/incidents/state [req=%s] — %d active", rid, len(active))
    return _json_resp(
        StateResponse(
            active=[ActiveIncidentState(**a).model_dump() for a in active],
            count=len(active),
        )
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  [req=%(request_id)s]  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "5000"))
    logger.info("Starting FastAPI server on %s:%s (uvicorn)", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")
