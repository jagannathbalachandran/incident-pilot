"""
incident_scenarios.py

Tick-driven state machine that simulates three known incident scenarios:

  A) Postgres connection-pool exhaustion  (runbook Known Issue #1)
  B) Redis cache node failover             (runbook Known Issue #2)
  C) Fraud-scoring-svc outage

Every call to tick() advances the simulated time by one minute and
re-computes the metric values for the current phase.

All public methods return Pydantic models for validated, typed responses.
"""

import logging
import time
import threading
from typing import Optional

from pydantic import BaseModel

from config import (
    ScenarioState,
    TriggerResponse,
    ResolveResponse,
    ErrorResponse,
    MAX_CONNECTIONS,
    BASELINE_LATENCY_MS,
    BASELINE_ERROR_PCT,
    BASELINE_CONNECTIONS,
    POOL_CLIMBING_MINUTES,
    POOL_PLATEAU_MINUTES,
    POOL_RECOVERY_MINUTES,
    CACHE_FAILOVER_MINUTES,
    CACHE_WARMING_MINUTES,
    FRAUD_ACTIVE_MINUTES,
    RNG,
)

logger = logging.getLogger(__name__)


class ScenarioResult(BaseModel):
    """Unified result type returned by engine public methods.

    Contains either success data or an error message so the caller can
    distinguish the two cases without catching exceptions.
    """
    success: bool = True
    data: Optional[dict] = None
    error: Optional[str] = None


class IncidentEngine:
    """Thread-safe state machine for incident scenarios.

    Thread safety is provided by a single reentrant lock that guards
    all reads and writes to ``self._active``.
    """

    VALID_KINDS = frozenset({"pool", "cache", "fraud"})

    def __init__(self):
        self._lock = threading.RLock()
        self._active: Optional[ScenarioState] = None
        logger.info("IncidentEngine initialised")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_scenario(self, kind: str, auto_resolve: bool = True,
                        request_id: str = "") -> ScenarioResult:
        """Start (or restart) a scenario.

        Args:
            kind: Incident kind (pool | cache | fraud).
            auto_resolve: Whether to auto-resolve after the lifecycle completes.
            request_id: Request ID from the triggering API call, for log tracing.

        Returns a ScenarioResult. On success, `.data` contains the
        TriggerResponse dict. On error, `.success` is False and `.error`
        has the message.
        """
        kind = kind.lower()
        if kind not in self.VALID_KINDS:
            logger.warning("start_scenario: unknown kind '%s'", kind)
            return ScenarioResult(
                success=False,
                error=f"unknown incident kind '{kind}'. Valid: {sorted(self.VALID_KINDS)}",
            )

        with self._lock:
            self._active = ScenarioState(
                kind=kind,
                request_id=request_id,
                phase="climbing" if kind != "fraud" else "active",
                phase_progress=0.0,
                tick_count=0,
                auto_resolve=auto_resolve,
                started_at=time.time(),
            )
            self._compute_metric_values()
            logger.info("Scenario started [req=%s]: kind=%s phase=%s auto_resolve=%s",
                         request_id or "-", kind, self._active.phase, auto_resolve)
            return ScenarioResult(
                data=TriggerResponse(
                    status="started",
                    kind=kind,
                    phase=self._active.phase,
                    tick_count=0,
                ).model_dump(),
            )

    def resolve(self, kind: Optional[str] = None) -> ScenarioResult:
        """Force-resolve the active scenario.

        Returns a ScenarioResult. On success, `.data` contains the
        ResolveResponse dict. On error, `.success` is False and `.error`
        has the message.
        """
        with self._lock:
            if self._active is None or self._active.phase == "resolved":
                logger.info("Resolve: no active incident to resolve")
                return ScenarioResult(
                    data=ResolveResponse(status="no_active_incident").model_dump(),
                )
            if kind and self._active.kind != kind:
                logger.info("Resolve: kind mismatch (expected=%s, active=%s)",
                             kind, self._active.kind)
                return ScenarioResult(
                    data=ResolveResponse(
                        status="no_active_incident",
                        expected=kind,
                        active=self._active.kind,
                    ).model_dump(),
                )
            resolved_kind = self._active.kind
            self._active.phase = "resolved"
            logger.info("Scenario resolved: kind=%s", resolved_kind)
            return ScenarioResult(
                data=ResolveResponse(
                    status="resolved",
                    kind=resolved_kind,
                    phase="resolved",
                ).model_dump(),
            )

    def tick(self) -> None:
        """Advance simulation by one tick (one simulated minute).

        Should be called periodically from the background thread.
        """
        with self._lock:
            if self._active is None or self._active.phase in ("none", "resolved"):
                return

            prev_phase = self._active.phase
            self._active.tick_count += 1
            self._advance_phase()
            self._compute_metric_values()

            if self._active.phase != prev_phase:
                logger.info("Phase transition: %s → %s (tick=%d, kind=%s)",
                             prev_phase, self._active.phase,
                             self._active.tick_count, self._active.kind)

    def get_state(self) -> Optional[ScenarioState]:
        """Return the current scenario state (or None if no incident active)."""
        with self._lock:
            if self._active is None or self._active.phase in ("none", "resolved"):
                return None
            return self._active

    def get_state_dict(self) -> dict:
        """Return state as dict, or a 'none' sentinel for the API."""
        with self._lock:
            if self._active is None:
                return {"kind": "none", "phase": "none", "tick_count": 0, "auto_resolve": True}
            return self._active.public_dict()

    def is_active(self) -> bool:
        """Check whether a scenario is currently running."""
        with self._lock:
            return self._active is not None and self._active.phase not in ("none", "resolved")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _advance_phase(self) -> None:
        """Determine the current phase from tick_count and duration budgets."""
        sp = self._active
        t = sp.tick_count

        if sp.kind == "pool":
            climbing_end = POOL_CLIMBING_MINUTES
            plateau_end = climbing_end + POOL_PLATEAU_MINUTES
            recovery_end = plateau_end + POOL_RECOVERY_MINUTES

            if t < climbing_end:
                sp.phase = "climbing"
                sp.phase_progress = t / climbing_end
            elif t < plateau_end:
                sp.phase = "plateau"
                sp.phase_progress = (t - climbing_end) / plateau_end
            elif t < recovery_end:
                sp.phase = "recovering"
                sp.phase_progress = (t - plateau_end) / recovery_end
            else:
                sp.phase = "resolved"

        elif sp.kind == "cache":
            failover_end = CACHE_FAILOVER_MINUTES
            warming_end = failover_end + CACHE_WARMING_MINUTES

            if t < failover_end:
                sp.phase = "failover"
                sp.phase_progress = t / failover_end
            elif t < warming_end:
                sp.phase = "warming"
                sp.phase_progress = (t - failover_end) / warming_end
            else:
                sp.phase = "resolved"

        elif sp.kind == "fraud":
            if t < FRAUD_ACTIVE_MINUTES:
                sp.phase = "active"
                sp.phase_progress = t / FRAUD_ACTIVE_MINUTES
            else:
                sp.phase = "resolved"

    def _compute_metric_values(self) -> None:
        """Re-derive all metric values from the current phase + progress.

        Math matches generate_synthetic_data.py exactly.
        """
        sp = self._active
        if sp.kind == "pool":
            self._compute_pool_metrics()
        elif sp.kind == "cache":
            self._compute_cache_metrics()
        elif sp.kind == "fraud":
            self._compute_fraud_metrics()

    def _compute_pool_metrics(self) -> None:
        sp = self._active
        p = sp.phase_progress

        if sp.phase == "climbing":
            conns = int(BASELINE_CONNECTIONS + p * (MAX_CONNECTIONS - BASELINE_CONNECTIONS))
            latency = BASELINE_LATENCY_MS + p * 1450 + RNG.gauss(0, 40)
            err_rate = BASELINE_ERROR_PCT + p * 5.8 + RNG.gauss(0, 0.15)
        elif sp.phase == "plateau":
            conns = MAX_CONNECTIONS
            latency = 1780.0 + RNG.gauss(0, 60)
            err_rate = max(0.0, 6.1 + RNG.gauss(0, 0.3))
        elif sp.phase == "recovering":
            conns = int(MAX_CONNECTIONS - p * (MAX_CONNECTIONS - BASELINE_CONNECTIONS))
            latency = 1780.0 - p * 1400 + RNG.gauss(0, 30)
            err_rate = max(0.0, 6.1 - p * 6.0 + RNG.gauss(0, 0.1))
        else:
            return  # resolved or unknown

        sp.active_connections = max(0, conns)
        sp.p99_latency_ms = max(0.0, round(latency, 1))
        sp.error_rate_pct = round(max(0.0, err_rate), 3)
        sp.cache_hit_ratio = round(0.95 + RNG.gauss(0, 0.01), 3)
        sp.pool_error_pct = 0.0

        # Pool errors appear only when connections are near exhaustion
        if sp.active_connections >= 190 and sp.phase in ("climbing", "plateau"):
            excess = (sp.active_connections - 190) / (MAX_CONNECTIONS - 190)
            sp.pool_error_pct = round(RNG.uniform(4.0, 6.0) * excess, 3)

    def _compute_cache_metrics(self) -> None:
        sp = self._active
        p = sp.phase_progress
        FLOOR_RATIO = 0.41

        if sp.phase == "failover":
            hit = FLOOR_RATIO
            cache_warn = 4.0  # always warning during failover
        elif sp.phase == "warming":
            hit = FLOOR_RATIO + p * (0.93 - FLOOR_RATIO)
            cache_warn = 4.0 if hit < 0.90 else 0.0
        else:
            return

        sp.cache_hit_ratio = round(hit, 3)
        sp.cache_warn_pct = cache_warn
        sp.active_connections = BASELINE_CONNECTIONS + RNG.randint(-5, 5)
        sp.error_rate_pct = round(max(0.0, BASELINE_ERROR_PCT + RNG.gauss(0, 0.02)), 3)
        sp.pool_error_pct = 0.0
        sp.fraud_error_pct = 0.0

        # Latency scales with severity of cache miss
        severity = (0.95 - hit) / (0.95 - FLOOR_RATIO)
        success_median = 240.0 * (1.0 + severity * 2.9)
        sp.p99_latency_ms = round(max(0.0, success_median + RNG.gauss(0, 40)), 1)

    def _compute_fraud_metrics(self) -> None:
        sp = self._active

        if sp.phase == "active":
            sp.error_rate_pct = round(RNG.uniform(10.0, 15.0), 3)
            sp.p99_latency_ms = round(BASELINE_LATENCY_MS * 2.2 + RNG.gauss(0, 30), 1)
            sp.active_connections = BASELINE_CONNECTIONS + RNG.randint(-5, 5)
            sp.cache_hit_ratio = round(0.95 + RNG.gauss(0, 0.01), 3)
            sp.pool_error_pct = 0.0
            sp.fraud_error_pct = RNG.uniform(10.0, 15.0)  # same as error_rate
        else:
            sp.fraud_error_pct = 0.0
