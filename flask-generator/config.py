"""
config.py

Central configuration constants, Pydantic models for ScenarioState and all
API request/response types, and phase duration budgets for the Flask
incident generator.

All values match the existing synthetic-data/script/generate_synthetic_data.py
so that the live generator produces exactly the same metric shapes.
"""

import os
import random
from typing import Optional

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Metric baselines (copied from generate_synthetic_data.py)
# ---------------------------------------------------------------------------

MAX_CONNECTIONS = 200
BASELINE_LATENCY_MS = 380.0
BASELINE_ERROR_PCT = 0.05
BASELINE_CONNECTIONS = 118

SERVICE = "checkout-api"

# ---------------------------------------------------------------------------
# Timing — environment-configurable
# ---------------------------------------------------------------------------

TICK_MODE = os.getenv("TICK_MODE", "accelerated")  # "realtime" | "accelerated"

# In accelerated mode each wall-clock second = one simulated minute.
# In realtime mode each wall-clock minute = one simulated minute.
_INTERVAL = 1 if TICK_MODE == "accelerated" else 60
_tick_env = os.getenv("TICK_INTERVAL", "")
TICK_INTERVAL_SECONDS = int(_tick_env) if _tick_env.strip() else _INTERVAL
SIM_MINUTES_PER_TICK = 1

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
# Log message templates
# ---------------------------------------------------------------------------

POOL_ERROR_MSG = "could not obtain connection from pool within 5000ms"
FRAUD_ERROR_MSG = "fraud-scoring-svc unavailable"
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
# Pydantic Models
# ===================================================================


class ScenarioState(BaseModel):
    """Holds the current state of an active incident scenario.

    The engine advances ``tick_count`` by 1 every tick. Phase transitions are
    determined by comparing tick_count against the duration budgets. Computed
    metric values (p99_latency_ms, error_rate_pct, etc.) are re-derived every
    tick from phase + phase_progress.

    Internal fields (pool_error_pct, fraud_error_pct, cache_warn_pct) are
    excluded from serialization so they never leak to the API.
    """

    # --- Identity ---
    kind: str = Field(..., description="Incident kind: pool | cache | fraud")
    request_id: str = Field(default="", description="Request ID that triggered this incident")

    # --- Phase tracking ---
    phase: str = Field(default="none", description="Current phase label")
    phase_progress: float = Field(default=0.0, ge=0.0, le=1.0)
    tick_count: int = Field(default=0, ge=0)
    auto_resolve: bool = Field(default=True)
    started_at: float = Field(default=0.0, description="time.time() of trigger")

    # --- Public metric values (returned in API responses) ---
    p99_latency_ms: float = Field(default=BASELINE_LATENCY_MS)
    error_rate_pct: float = Field(default=BASELINE_ERROR_PCT)
    active_connections: int = Field(default=BASELINE_CONNECTIONS)
    cache_hit_ratio: float = Field(default=0.95)

    # --- Internal-only fields (excluded from serialization) ---
    pool_error_pct: float = Field(default=0.0, exclude=True)
    fraud_error_pct: float = Field(default=0.0, exclude=True)
    cache_warn_pct: float = Field(default=0.0, exclude=True)

    @model_validator(mode="after")
    def _clamp_phase_progress(self) -> "ScenarioState":
        """Ensure phase_progress is always clamped to [0.0, 1.0]."""
        self.phase_progress = max(0.0, min(1.0, self.phase_progress))
        return self

    def public_dict(self) -> dict:
        """Serialize to a plain dict for API endpoints (strips internal fields)."""
        return self.model_dump(
            mode="json",
            exclude={"pool_error_pct", "fraud_error_pct", "cache_warn_pct", "started_at"},
        )


# ---------------------------------------------------------------------------
# API Request Models
# ---------------------------------------------------------------------------


class TriggerRequest(BaseModel):
    """Optional JSON body for POST /api/incidents/<kind>/trigger."""
    auto_resolve: bool = Field(default=True)


# ---------------------------------------------------------------------------
# API Response Models
# ---------------------------------------------------------------------------


class TriggerResponse(BaseModel):
    """Response from a successful scenario trigger."""
    status: str = Field(default="started")
    kind: str
    phase: str
    tick_count: int = 0
    request_id: str = Field(default="", description="Unique ID for this API request for log tracing")


class ResolveResponse(BaseModel):
    """Response from a resolve request."""
    status: str
    kind: Optional[str] = None
    phase: Optional[str] = None
    expected: Optional[str] = None
    active: Optional[str] = None
    request_id: str = Field(default="", description="Unique ID for this API request for log tracing")


class HealthResponse(BaseModel):
    """Response from GET /health."""
    status: str = Field(default="ok")
    service: str = Field(default="flask-generator")
    active_incident: Optional[str] = None
    request_id: str = Field(default="", description="Unique ID for this API request for log tracing")


class StateResponse(BaseModel):
    """Public state exposed by GET /api/incidents/state."""
    kind: str = "none"
    phase: str = "none"
    phase_progress: float = 0.0
    tick_count: int = 0
    p99_latency_ms: float = BASELINE_LATENCY_MS
    error_rate_pct: float = BASELINE_ERROR_PCT
    active_connections: int = BASELINE_CONNECTIONS
    cache_hit_ratio: float = 0.95
    auto_resolve: bool = True
    request_id: str = Field(default="", description="Unique ID for this API request for log tracing")


class ErrorResponse(BaseModel):
    """Generic error response."""
    error: str
    request_id: str = Field(default="", description="Unique ID for this API request for log tracing")
