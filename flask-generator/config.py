"""
config.py

Central configuration constants, Pydantic models for incidents and all
API request/response types, and phase duration budgets for the incident
generator.

The generator simulates 4 services declared in ``topology.py``. Incidents
target one service each; ``incident_scenarios.py`` folds whichever
incidents are active on a service into a single ``Health`` snapshot that
``traffic.py`` applies when it walks a request through that service.
"""

import os
import random
from typing import Optional

from pydantic import BaseModel, Field

import topology

# ---------------------------------------------------------------------------
# Metric baselines -- applied uniformly to whichever service an incident
# targets (a simplification: every pool/cache-using service shares the same
# baseline shape rather than each having its own tuned constants).
# ---------------------------------------------------------------------------

MAX_CONNECTIONS = 200
BASELINE_LATENCY_MS = 380.0
BASELINE_ERROR_PCT = 0.05
BASELINE_CONNECTIONS = 118
BASELINE_CACHE_HIT = 0.95

# ---------------------------------------------------------------------------
# Timing -- environment-configurable
# ---------------------------------------------------------------------------

TICK_MODE = os.getenv("TICK_MODE", "accelerated")  # "realtime" | "accelerated"

# In accelerated mode each wall-clock second = one simulated minute.
# In realtime mode each wall-clock minute = one simulated minute.
_INTERVAL = 1 if TICK_MODE == "accelerated" else 60
_tick_env = os.getenv("TICK_INTERVAL", "")
TICK_INTERVAL_SECONDS = int(_tick_env) if _tick_env.strip() else _INTERVAL
SIM_MINUTES_PER_TICK = 1

# How many synthetic user journeys (login -> ... -> logout) to run per tick.
_journeys_env = os.getenv("JOURNEYS_PER_TICK", "")
JOURNEYS_PER_TICK = int(_journeys_env) if _journeys_env.strip() else 8
N_USERS = 20

# ---------------------------------------------------------------------------
# Phase duration budgets (in simulated minutes)
# ---------------------------------------------------------------------------

POOL_CLIMBING_MINUTES = 15
POOL_PLATEAU_MINUTES = 15
POOL_RECOVERY_MINUTES = 10

CACHE_FAILOVER_MINUTES = 6
CACHE_WARMING_MINUTES = 12

FRAUD_ACTIVE_MINUTES = 20

# ---------------------------------------------------------------------------
# Incident kind -> default target service, and which topology capability
# (if any) a target service must declare for that kind to apply to it.
# ---------------------------------------------------------------------------

VALID_KINDS = frozenset({"pool", "cache", "fraud"})

DEFAULT_TARGET = {
    "pool": "checkout-api",
    "cache": "checkout-api",
    # fraud-scoring-svc is the actual dependency checkout-api calls during
    # /checkout -- targeting it directly (rather than checkout-api itself)
    # means checkout-api's own elevated error rate is a genuine cascade
    # effect of that dependency failing, not a separately-injected number.
    "fraud": "fraud-scoring-svc",
}

_REQUIRED_CAPABILITY = {
    "pool": "uses_db_pool",
    "cache": "uses_cache",
    "fraud": None,  # error-rate injection has no infra prerequisite
}


def service_supports(kind: str, service: str) -> bool:
    """Return whether ``service`` can host an incident of ``kind``."""
    svc = topology.SERVICES.get(service)
    if svc is None:
        return False
    capability = _REQUIRED_CAPABILITY.get(kind)
    return capability is None or getattr(svc, capability, False)


# ---------------------------------------------------------------------------
# Log message templates
# ---------------------------------------------------------------------------

POOL_ERROR_MSG = "could not obtain connection from pool within 5000ms"
FRAUD_ERROR_MSG = "fraud-scoring-svc unavailable"
UPSTREAM_ERROR_MSG = "upstream dependency failed"
CACHE_WARN_MESSAGES = [
    "Redis cluster failover detected",
    "MOVED redirection error from cache node",
]
LATENCY_SLO_WARN_MSG = "request exceeded p99 SLO threshold (1500ms)"

# ---------------------------------------------------------------------------
# Random number generator (deterministic seed for reproducibility)
# ---------------------------------------------------------------------------

RNG = random.Random(42)
WEEK_RNG = random.Random(1337)  # separate stream, reserved for week dataset


# ===================================================================
# Pydantic Models -- internal incident state
# ===================================================================


class Incident(BaseModel):
    """One active incident targeting one service.

    Phase tracking mirrors the original single-service design (tick_count
    advances every tick, phase + phase_progress derive from duration
    budgets). The effect fields below are re-derived every tick from
    phase + phase_progress and are what ``IncidentEngine.health_for()``
    folds into a ``Health`` snapshot for the target service -- they are
    never read directly by API consumers.
    """

    kind: str
    service: str
    request_id: str = ""

    phase: str = "none"
    phase_progress: float = Field(default=0.0, ge=0.0, le=1.0)
    tick_count: int = Field(default=0, ge=0)
    auto_resolve: bool = True
    started_at: float = 0.0

    # --- Effect fields (internal; folded by IncidentEngine.health_for) ---
    extra_latency_ms: float = 0.0
    inject_error_pct: float = 0.0
    error_type: str = ""
    error_message: str = ""
    warn_pct: float = 0.0
    warn_message: str = ""

    # --- Infra gauges (only set by pool/cache incidents) ---
    pool_active: Optional[int] = None
    cache_hit: Optional[float] = None


class Health(BaseModel):
    """Folded effect of every incident currently active on one service."""

    extra_latency_ms: float = 0.0
    inject_error_pct: float = 0.0
    error_type: str = ""
    error_message: str = ""
    warn_pct: float = 0.0
    warn_message: str = ""


class ServiceSnapshot(BaseModel):
    """Infra gauge values for one service (baseline if no incident sets them)."""

    pool_active: int = BASELINE_CONNECTIONS
    pool_max: int = MAX_CONNECTIONS
    cache_hit: float = BASELINE_CACHE_HIT


# ---------------------------------------------------------------------------
# API Request Models
# ---------------------------------------------------------------------------


class TriggerRequest(BaseModel):
    """Optional JSON body for POST /api/incidents/<kind>/trigger."""
    auto_resolve: bool = Field(default=True)
    service: Optional[str] = Field(
        default=None, description="Target service; defaults per-kind if omitted."
    )


# ---------------------------------------------------------------------------
# API Response Models
# ---------------------------------------------------------------------------


class TriggerResponse(BaseModel):
    """Response from a successful scenario trigger."""
    status: str = Field(default="started")
    kind: str
    service: str
    phase: str
    tick_count: int = 0
    request_id: str = Field(default="", description="Unique ID for this API request for log tracing")


class ResolvedIncident(BaseModel):
    kind: str
    service: str
    phase: str = "resolved"


class ResolveResponse(BaseModel):
    """Response from a resolve request."""
    status: str
    resolved: list = Field(default_factory=list)  # list[ResolvedIncident]
    request_id: str = Field(default="", description="Unique ID for this API request for log tracing")


class HealthResponse(BaseModel):
    """Response from GET /health."""
    status: str = Field(default="ok")
    service: str = Field(default="incident-generator")
    active_incidents: list = Field(default_factory=list)  # ["kind@service", ...]
    request_id: str = Field(default="", description="Unique ID for this API request for log tracing")


class ActiveIncidentState(BaseModel):
    """Public view of one active incident, for GET /api/incidents/state."""
    kind: str
    service: str
    phase: str = "none"
    phase_progress: float = 0.0
    tick_count: int = 0
    auto_resolve: bool = True
    request_id: str = Field(default="", description="Request ID of the API call that triggered this incident")


class StateResponse(BaseModel):
    """Public state exposed by GET /api/incidents/state -- a list, since
    multiple services can each have an active incident concurrently."""
    active: list = Field(default_factory=list)  # list[ActiveIncidentState]
    count: int = 0
    request_id: str = Field(default="", description="Unique ID for this API request for log tracing")


class EndpointInfo(BaseModel):
    method: str
    path: str
    calls: list = Field(default_factory=list)  # list[[service, path]]


class ServiceInfo(BaseModel):
    name: str
    user_facing: bool
    uses_db_pool: bool
    uses_cache: bool
    endpoints: list  # list[EndpointInfo]


class ServicesResponse(BaseModel):
    """Response from GET /api/services -- the topology catalog + journey."""
    services: list  # list[ServiceInfo]
    journey: list  # list[[service, path]]
    request_id: str = Field(default="", description="Unique ID for this API request for log tracing")


class ErrorResponse(BaseModel):
    """Generic error response."""
    error: str
    request_id: str = Field(default="", description="Unique ID for this API request for log tracing")
