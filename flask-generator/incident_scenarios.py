"""
incident_scenarios.py

Tick-driven state machine that simulates incidents targeting any service in
the topology catalog: Postgres connection-pool exhaustion, Redis cache node
failover, or an elevated error-rate injection ("fraud"-style outage).

Multiple incidents can be active concurrently, each targeting its own
service. Every call to tick() advances simulated time by one minute and
re-computes each active incident's effect fields (extra latency, injected
error probability, warn probability). ``health_for(service)`` folds every
incident currently targeting a service into one ``Health`` snapshot -- this
is the entire mechanism ``traffic.py`` uses to make a downstream incident
cascade into its callers' own latency/error numbers, with zero special-cased
cross-service logic here.
"""

import logging
import time
import threading
from typing import Optional

from pydantic import BaseModel

from config import (
    Incident,
    Health,
    ServiceSnapshot,
    TriggerResponse,
    ResolveResponse,
    ResolvedIncident,
    MAX_CONNECTIONS,
    BASELINE_LATENCY_MS,
    BASELINE_ERROR_PCT,
    BASELINE_CONNECTIONS,
    BASELINE_CACHE_HIT,
    POOL_CLIMBING_MINUTES,
    POOL_PLATEAU_MINUTES,
    POOL_RECOVERY_MINUTES,
    CACHE_FAILOVER_MINUTES,
    CACHE_WARMING_MINUTES,
    FRAUD_ACTIVE_MINUTES,
    POOL_ERROR_MSG,
    FRAUD_ERROR_MSG,
    CACHE_WARN_MESSAGES,
    LATENCY_SLO_WARN_MSG,
    VALID_KINDS,
    DEFAULT_TARGET,
    service_supports,
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
    """Thread-safe multi-incident state machine.

    Incidents are keyed by ``(kind, service)`` -- a service can only have
    one incident of a given kind at a time, but different kinds (or the
    same kind on different services) run concurrently. Thread safety is
    provided by a single reentrant lock guarding all reads/writes.
    """

    VALID_KINDS = VALID_KINDS

    def __init__(self):
        self._lock = threading.RLock()
        self._incidents: dict = {}  # (kind, service) -> Incident
        logger.info("IncidentEngine initialised")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_scenario(self, kind: str, service: Optional[str] = None,
                        auto_resolve: bool = True, request_id: str = "") -> ScenarioResult:
        """Start (or restart) a scenario targeting ``service``.

        Args:
            kind: Incident kind (pool | cache | fraud).
            service: Target service name. Defaults to the kind's usual
                target (``config.DEFAULT_TARGET``) if omitted.
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

        target = service or DEFAULT_TARGET[kind]
        if not service_supports(kind, target):
            logger.warning("start_scenario: service '%s' does not support kind '%s'", target, kind)
            return ScenarioResult(
                success=False,
                error=f"service '{target}' does not support incident kind '{kind}'",
            )

        with self._lock:
            key = (kind, target)
            inc = Incident(
                kind=kind,
                service=target,
                request_id=request_id,
                phase="climbing" if kind == "pool" else ("failover" if kind == "cache" else "active"),
                phase_progress=0.0,
                tick_count=0,
                auto_resolve=auto_resolve,
                started_at=time.time(),
            )
            self._incidents[key] = inc
            self._compute_effects(inc)
            logger.info("Scenario started [req=%s]: kind=%s service=%s auto_resolve=%s",
                         request_id or "-", kind, target, auto_resolve)
            return ScenarioResult(
                data=TriggerResponse(
                    status="started", kind=kind, service=target,
                    phase=inc.phase, tick_count=0,
                ).model_dump(),
            )

    def resolve(self, kind: Optional[str] = None, service: Optional[str] = None) -> ScenarioResult:
        """Force-resolve matching active incidents.

        ``kind=None`` matches any kind; ``service=None`` matches any service.
        Passing both None resolves everything currently active.

        Returns a ScenarioResult whose `.data` is a ResolveResponse dict
        listing every incident that was resolved (empty list if none matched).
        """
        with self._lock:
            resolved = []
            for key, inc in list(self._incidents.items()):
                inc_kind, inc_service = key
                if kind is not None and inc_kind != kind:
                    continue
                if service is not None and inc_service != service:
                    continue
                resolved.append(ResolvedIncident(kind=inc_kind, service=inc_service))
                del self._incidents[key]

            if not resolved:
                logger.info("Resolve: no matching active incident (kind=%s, service=%s)", kind, service)
                return ScenarioResult(data=ResolveResponse(status="no_active_incident").model_dump())

            logger.info("Resolved %d incident(s): %s", len(resolved),
                         [(r.kind, r.service) for r in resolved])
            return ScenarioResult(
                data=ResolveResponse(
                    status="resolved",
                    resolved=[r.model_dump() for r in resolved],
                ).model_dump(),
            )

    def tick(self) -> None:
        """Advance every active incident by one tick (one simulated minute)."""
        with self._lock:
            for key, inc in list(self._incidents.items()):
                prev_phase = inc.phase
                inc.tick_count += 1
                self._advance_phase(inc)

                if inc.phase == "resolved":
                    logger.info("Incident resolved: kind=%s service=%s (tick=%d)",
                                 inc.kind, inc.service, inc.tick_count)
                    del self._incidents[key]
                    continue

                self._compute_effects(inc)
                if inc.phase != prev_phase:
                    logger.info("Phase transition: %s -> %s (tick=%d, kind=%s, service=%s)",
                                 prev_phase, inc.phase, inc.tick_count, inc.kind, inc.service)

    def get_state(self) -> list:
        """Return public state (list of dicts) for every active incident."""
        with self._lock:
            return [
                {
                    "kind": inc.kind, "service": inc.service, "phase": inc.phase,
                    "phase_progress": round(inc.phase_progress, 3),
                    "tick_count": inc.tick_count, "auto_resolve": inc.auto_resolve,
                    "request_id": inc.request_id,
                }
                for inc in self._incidents.values()
            ]

    def is_active(self, service: Optional[str] = None) -> bool:
        """Check whether any incident (optionally: on ``service``) is active."""
        with self._lock:
            if service is None:
                return bool(self._incidents)
            return any(inc.service == service for inc in self._incidents.values())

    # ------------------------------------------------------------------
    # Cascade mechanism -- the only two accessors traffic.py needs
    # ------------------------------------------------------------------

    def health_for(self, service: str) -> Health:
        """Fold every incident currently targeting ``service`` into one Health.

        This is the whole cascade mechanism: when traffic.py walks a call
        from checkout-api into payment-service, it asks for payment-service's
        Health and adds/propagates it onto the parent span -- an incident
        never needs to know who calls into its target service.
        """
        with self._lock:
            active = [inc for inc in self._incidents.values() if inc.service == service]
            if not active:
                return Health()
            dominant = max(active, key=lambda i: i.inject_error_pct)
            return Health(
                extra_latency_ms=sum(i.extra_latency_ms for i in active),
                inject_error_pct=max(i.inject_error_pct for i in active),
                error_type=dominant.error_type,
                error_message=dominant.error_message,
                warn_pct=max(i.warn_pct for i in active),
                warn_message=dominant.warn_message,
            )

    def service_snapshot(self, service: str) -> ServiceSnapshot:
        """Pool/cache gauge values for a service (baseline if nothing sets them)."""
        with self._lock:
            snap = ServiceSnapshot()
            for inc in self._incidents.values():
                if inc.service != service:
                    continue
                if inc.pool_active is not None:
                    snap.pool_active = inc.pool_active
                if inc.cache_hit is not None:
                    snap.cache_hit = inc.cache_hit
            return snap

    # ------------------------------------------------------------------
    # Internal helpers -- phase advancement + effect math
    # ------------------------------------------------------------------

    def _advance_phase(self, inc: Incident) -> None:
        """Determine the current phase from tick_count and duration budgets."""
        t = inc.tick_count

        if inc.kind == "pool":
            climbing_end = POOL_CLIMBING_MINUTES
            plateau_end = climbing_end + POOL_PLATEAU_MINUTES
            recovery_end = plateau_end + POOL_RECOVERY_MINUTES
            if t < climbing_end:
                inc.phase, inc.phase_progress = "climbing", t / climbing_end
            elif t < plateau_end:
                inc.phase, inc.phase_progress = "plateau", (t - climbing_end) / POOL_PLATEAU_MINUTES
            elif t < recovery_end:
                inc.phase, inc.phase_progress = "recovering", (t - plateau_end) / POOL_RECOVERY_MINUTES
            else:
                inc.phase = "resolved"

        elif inc.kind == "cache":
            failover_end = CACHE_FAILOVER_MINUTES
            warming_end = failover_end + CACHE_WARMING_MINUTES
            if t < failover_end:
                inc.phase, inc.phase_progress = "failover", t / failover_end
            elif t < warming_end:
                inc.phase, inc.phase_progress = "warming", (t - failover_end) / CACHE_WARMING_MINUTES
            else:
                inc.phase = "resolved"

        elif inc.kind == "fraud":
            if t < FRAUD_ACTIVE_MINUTES:
                inc.phase, inc.phase_progress = "active", t / FRAUD_ACTIVE_MINUTES
            else:
                inc.phase = "resolved"

        inc.phase_progress = max(0.0, min(1.0, inc.phase_progress))

    def _compute_effects(self, inc: Incident) -> None:
        """Re-derive all effect fields from the current phase + progress."""
        if inc.kind == "pool":
            self._pool_effects(inc)
        elif inc.kind == "cache":
            self._cache_effects(inc)
        elif inc.kind == "fraud":
            self._fraud_effects(inc)

    def _pool_effects(self, inc: Incident) -> None:
        p = inc.phase_progress
        if inc.phase == "climbing":
            conns = int(BASELINE_CONNECTIONS + p * (MAX_CONNECTIONS - BASELINE_CONNECTIONS))
            extra_latency = p * 1450 + RNG.gauss(0, 40)
            err_pct = BASELINE_ERROR_PCT + p * 5.8 + RNG.gauss(0, 0.15)
        elif inc.phase == "plateau":
            conns = MAX_CONNECTIONS
            extra_latency = 1400.0 + RNG.gauss(0, 60)
            err_pct = 6.1 + RNG.gauss(0, 0.3)
        elif inc.phase == "recovering":
            conns = int(MAX_CONNECTIONS - p * (MAX_CONNECTIONS - BASELINE_CONNECTIONS))
            extra_latency = 1400.0 - p * 1400 + RNG.gauss(0, 30)
            err_pct = 6.1 - p * 6.0 + RNG.gauss(0, 0.1)
        else:
            return

        inc.pool_active = max(0, conns)
        inc.extra_latency_ms = max(0.0, round(extra_latency, 1))
        inc.inject_error_pct = round(max(0.0, err_pct) / 100.0, 5)
        inc.error_type = "pool_timeout"
        inc.error_message = POOL_ERROR_MSG
        inc.warn_pct = 30.0 if (BASELINE_LATENCY_MS + inc.extra_latency_ms) > 1500 else 0.0
        inc.warn_message = LATENCY_SLO_WARN_MSG

    def _cache_effects(self, inc: Incident) -> None:
        p = inc.phase_progress
        FLOOR_RATIO = 0.41
        if inc.phase == "failover":
            hit = FLOOR_RATIO
            warn_pct = 4.0
        elif inc.phase == "warming":
            hit = FLOOR_RATIO + p * (0.93 - FLOOR_RATIO)
            warn_pct = 4.0 if hit < 0.90 else 0.0
        else:
            return

        inc.cache_hit = round(hit, 3)
        severity = (BASELINE_CACHE_HIT - hit) / (BASELINE_CACHE_HIT - FLOOR_RATIO)
        success_latency = 240.0 * (1.0 + severity * 2.9)
        inc.extra_latency_ms = max(0.0, round(success_latency - BASELINE_LATENCY_MS + RNG.gauss(0, 40), 1))
        inc.inject_error_pct = round(max(0.0, BASELINE_ERROR_PCT + RNG.gauss(0, 0.02)) / 100.0, 5)
        inc.warn_pct = warn_pct
        inc.warn_message = RNG.choice(CACHE_WARN_MESSAGES) if warn_pct > 0 else ""

    def _fraud_effects(self, inc: Incident) -> None:
        if inc.phase == "active":
            inc.inject_error_pct = round(RNG.uniform(10.0, 15.0) / 100.0, 5)
            inc.extra_latency_ms = max(0.0, round(BASELINE_LATENCY_MS * 1.2 + RNG.gauss(0, 30), 1))
            inc.error_type = "fraud_svc_unavailable"
            inc.error_message = FRAUD_ERROR_MSG
