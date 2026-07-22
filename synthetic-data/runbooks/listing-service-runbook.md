---
service: listing-service
doc_type: runbook
---

# Runbook: listing-service

## When to use this runbook
Open this runbook when either of these alerts fires for listing-service:
- `listing-service-p99-latency-high` (p99 > 400ms for 5 min)
- `listing-service-error-rate-high` (5xx+4xx rate > 2%)

listing-service is standalone — it makes no synchronous calls to any other
service, so a listing-service incident never cascades into checkout-api or
auth-service. Conversely, if checkout-api or auth-service degrade, browsing
listings should be unaffected; if it isn't, don't assume it's listing-service
without checking its own dashboard first.

## Triage: if `p99-latency-high` fired

1. **Check the connection pool dashboard.**
   Panel: `listing-service > Postgres Pool > active_connections / max_connections`
   - If `active_connections` is pinned at `max_connections` → go to
     Known Issue #1 (connection pool exhaustion).

2. **Check cache hit rate.**
   Panel: `listing-service > Redis > cache_hit_ratio`
   - If it has dropped sharply → go to Known Issue #2 (cache node failover).

3. If neither of the above resolve it, escalate per the path below.

## Triage: if `error-rate-high` fired without elevated p99 latency

1. **Break down the error rate by status code.**
   Grafana panel: `listing-service > Errors by status code (4xx vs 5xx)`

2. **If 5xx is what's climbing** (and the pool/cache checks above are clean):
   - Check application error logs for exception stack traces on `/listings`
     — a code-level bug rather than a resource-exhaustion pattern.
   - Check whether the errors are concentrated right after a recent deploy.

## Known Issue #1: Postgres connection pool exhaustion

**Symptom signature:** p99 latency climbs gradually over 10-20 minutes,
`active_connections` pinned at `max_connections`, application logs show
`could not obtain connection from pool within 5000ms`.

**Immediate mitigation (no deploy required):**
- Increase the PgBouncer pool size for listing-service:
  1. Edit `infra/pgbouncer/listing-pool.ini`, raise `default_pool_size`.
  2. Reload PgBouncer without dropping existing connections:
     ```
     psql -h <pgbouncer-host> -p 6432 pgbouncer -c "RELOAD;"
     ```
- Monitor `active_connections` for 5 minutes after the change before taking
  further action.

## Known Issue #2: Redis cache node failover

**Symptom signature:** a sudden step-change in latency, `cache_hit_ratio`
drop, cluster-failover warnings in application logs.

**Mitigation:**
- Confirm failover completed: `redis-cli -c -h listing-cache.internal
  cluster info` should show `cluster_state:ok`.
- If `cluster_state` is not `ok`, page the infra on-call (`#infra-oncall`).
- If `cluster_state` is `ok` but hit ratio stays low, the cache is likely
  still warming up; this typically self-resolves within 10-15 minutes.

## Escalation path
- L1 (0-15 min): listing-service on-call, follow this runbook.
- L2 (15-30 min, unresolved): page catalog-platform team lead, open incident
  channel `#inc-listing-latency`.
- L3 (30+ min): declare SEV1, page the incident commander rotation.

## Resolution criteria
- p99 latency back under 150ms for 10 consecutive minutes.
- Error rate back under 0.1%.
- No further connection-pool, cache, or error-rate alerts for 15 minutes.
