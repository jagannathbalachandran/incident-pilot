"""
topology.py

Single source of truth for the simulated e-commerce system: the service
catalog, per-endpoint baseline latency/error profiles, the synchronous
call graph (which endpoint calls which downstream service), and the
canonical user journey (login -> browse listings -> checkout -> payment
-> logout).

``traffic.py`` walks this graph every tick to produce distributed traces;
``incident_scenarios.py`` applies health modifiers on top of the baselines
declared here. Nothing in this module is stateful -- it is pure declaration.
"""

from dataclasses import dataclass
from typing import Optional

REGION = "ap-south-1"


@dataclass(frozen=True)
class Endpoint:
    """A single HTTP route on a service.

    ``base_latency_ms`` is the median latency of the endpoint's OWN work
    (excluding downstream calls, which are added on top at trace time).
    ``calls`` lists synchronous downstream (service, endpoint_path) pairs
    this endpoint invokes -- their latency adds to this endpoint's own
    latency, and their failures propagate up as an upstream error.
    """

    method: str
    path: str
    base_latency_ms: float
    base_error_pct: float = 0.05
    calls: tuple = ()  # tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Service:
    """A service in the system.

    ``user_facing`` services appear as steps in the canonical JOURNEY;
    internal dependencies (inventory / fraud-scoring / payment) only
    appear as child spans reached via another service's ``calls``.
    ``uses_db_pool`` / ``uses_cache`` gate which infra-specific incidents
    (pool exhaustion, cache failover) can target the service.
    """

    name: str
    endpoints: dict  # path -> Endpoint
    user_facing: bool = True
    uses_db_pool: bool = False
    uses_cache: bool = False
    pods: tuple = ()

    def endpoint(self, path: str) -> Optional[Endpoint]:
        return self.endpoints.get(path)


def _svc(name: str, endpoints: list, **kw) -> Service:
    return Service(name=name, endpoints={e.path: e for e in endpoints}, **kw)


# ---------------------------------------------------------------------------
# Service catalog
# ---------------------------------------------------------------------------

SERVICES: dict = {
    "auth-service": _svc(
        "auth-service",
        [
            Endpoint("POST", "/login", base_latency_ms=60.0, base_error_pct=0.10),
            Endpoint("POST", "/logout", base_latency_ms=30.0, base_error_pct=0.02),
            # Internal only -- called by checkout-api, never a top-level journey step.
            Endpoint("GET", "/validate-session", base_latency_ms=15.0, base_error_pct=0.02),
        ],
        uses_cache=True,  # session store in Redis
        pods=("auth-service-6b81a", "auth-service-2fd90", "auth-service-c7e14"),
    ),
    "listing-service": _svc(
        "listing-service",
        [
            Endpoint("GET", "/listings", base_latency_ms=120.0, base_error_pct=0.05),
        ],
        uses_cache=True,
        uses_db_pool=True,
        pods=("listing-service-9aa12", "listing-service-4be77"),
    ),
    "checkout-api": _svc(
        "checkout-api",
        [
            Endpoint(
                "POST", "/checkout", base_latency_ms=180.0, base_error_pct=0.05,
                calls=(
                    ("auth-service", "/validate-session"),
                    ("inventory-svc", "/reserve"),
                    ("fraud-scoring-svc", "/score"),
                ),
            ),
            Endpoint(
                "POST", "/payment", base_latency_ms=220.0, base_error_pct=0.05,
                calls=(("payment-service", "/charge"),),
            ),
        ],
        uses_db_pool=True,
        uses_cache=True,
        pods=("checkout-api-5fd22", "checkout-api-77b31", "checkout-api-a0c9e"),
    ),
    # --- Internal dependencies (not user-facing; only appear as child spans) ---
    "payment-service": _svc(
        "payment-service",
        [Endpoint("POST", "/charge", base_latency_ms=150.0, base_error_pct=0.06)],
        user_facing=False,
        uses_db_pool=True,
        pods=("payment-service-3d8e1", "payment-service-90cc4"),
    ),
    "inventory-svc": _svc(
        "inventory-svc",
        [Endpoint("POST", "/reserve", base_latency_ms=40.0, base_error_pct=0.03)],
        user_facing=False,
        pods=("inventory-svc-3a10f",),
    ),
    "fraud-scoring-svc": _svc(
        "fraud-scoring-svc",
        [Endpoint("POST", "/score", base_latency_ms=70.0, base_error_pct=0.04)],
        user_facing=False,
        pods=("fraud-scoring-svc-b22e8",),
    ),
}


# ---------------------------------------------------------------------------
# Canonical user journey -- one trace per journey
# ---------------------------------------------------------------------------
#
# Ordered top-level steps a simulated user takes. Downstream calls (child
# spans -- e.g. checkout-api's call into payment-service) are expanded from
# each endpoint's ``.calls`` at trace time by traffic.py, not listed here.

JOURNEY: tuple = (
    ("auth-service", "/login"),
    ("listing-service", "/listings"),
    ("checkout-api", "/checkout"),
    ("checkout-api", "/payment"),
    ("auth-service", "/logout"),
)


# ---------------------------------------------------------------------------
# Convenience accessors
# ---------------------------------------------------------------------------


def all_service_names() -> list:
    return list(SERVICES.keys())


def user_facing_services() -> list:
    return [n for n, s in SERVICES.items() if s.user_facing]


def get_endpoint(service: str, path: str) -> Optional[Endpoint]:
    svc = SERVICES.get(service)
    return svc.endpoint(path) if svc else None


def pods_for(service: str) -> tuple:
    svc = SERVICES.get(service)
    return svc.pods if svc and svc.pods else (f"{service}-00000",)
