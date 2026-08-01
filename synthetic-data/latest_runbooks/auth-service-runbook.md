---
service: auth-service
doc_type: runbook
---

# Runbook: auth-service

## When to use this runbook
Open this runbook when either of these alerts fires for auth-service:
- `auth-service-p99-latency-high` (p99 > 400ms for 5 min)
- `auth-service-error-rate-high` (4xx+5xx rate > 2%)

auth-service backs `/login`, `/logout`, and an internal `/validate-session`
endpoint that checkout-api calls synchronously during checkout — a slow or
failing auth-service shows up both in its own metrics and in checkout-api's
`/checkout` latency/error rate.

## Triage: if `p99-latency-high` fired

1. **Check the session-store cache hit ratio.**
   Panel: `auth-service > Redis Session Store > cache_hit_ratio`
   - If it has dropped sharply (e.g. ~95% → under 50%) → go to
     Known Issue #1 (session-store failover).

2. **Check whether checkout-api is also degraded at the same time.**
   Panel: `checkout-api > /checkout endpoint latency`
   - If checkout-api's `/checkout` p99 rose in the same window → confirm via
     the `svc_upstream_duration_ms{service="checkout-api",upstream="auth-service"}`
     panel that the slow hop is specifically checkout-api's call into
     auth-service, not something else in checkout-api itself.

3. If neither of the above resolve it, escalate per the path below.

## Triage: if `error-rate-high` fired without elevated p99 latency

1. **Break down the error rate by status code.**
   Grafana panel: `auth-service > Errors by status code (4xx vs 5xx)`

2. **If 401 is what's climbing on `/login`:**
   - Check for a recent credential-store or token-signing key rotation.
     A spike concentrated right after a rotation points at clients/services
     still presenting tokens signed with the old key.

3. **If 5xx is what's climbing:**
   - Check application error logs for exception stack traces on `/login` or
     `/validate-session` specifically — a code-level bug, not a resource
     exhaustion pattern, since auth-service has no database connection pool.

## Known Issue #1: Redis session-store failover

**Symptom signature:** a sudden (not gradual) step-change in latency,
`cache_hit_ratio` drop, cluster-failover warnings in application logs.

**Mechanism:** session lookups on `/login` and `/validate-session` normally
hit Redis; when a cache node fails over, requests fall through to the
session's source of truth, which is slower and briefly less available.

**Mitigation:**
- Confirm failover completed: `redis-cli -c -h auth-session-cache.internal
  cluster info` should show `cluster_state:ok`.
- If `cluster_state` is not `ok`, page the infra on-call (`#infra-oncall`) —
  this is outside auth-service team's remediation scope.
- If `cluster_state` is `ok` but hit ratio stays low, the cache is likely
  still warming up; this typically self-resolves within 10-15 minutes as
  traffic repopulates it. No action needed beyond monitoring.

## Escalation path
- L1 (0-15 min): auth-service on-call, follow this runbook.
- L2 (15-30 min, unresolved): page identity-platform team lead, open incident
  channel `#inc-auth-latency`. Also notify checkout-api on-call if
  checkout-api's `/checkout` error rate is elevated at the same time.
- L3 (30+ min): declare SEV1, page the incident commander rotation.

## Resolution criteria
- p99 latency back under 90ms for 10 consecutive minutes.
- Error rate back under 0.2%.
- No further cache-failover warnings for 15 minutes.
