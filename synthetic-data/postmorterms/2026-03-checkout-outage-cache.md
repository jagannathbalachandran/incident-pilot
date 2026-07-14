---
title: "Postmortem: Checkout API Partial Outage — March 2, 2026"
service: checkout-api
incident_id: INC-4522
severity: SEV2
tags: [postmortem, checkout-api, cache node failover]
date: 2026-03-02
duration_minutes: 18
customer_impact: "~3% of checkout attempts experienced elevated latency between 09:15 and 09:33 UTC; no failed transactions"
related_runbooks: ["checkout-api-runbook.md"]
related_github_issues: ["org/checkout-api#387"]
---

# Postmortem: Checkout API Partial Outage — March 2, 2026

## Summary
An underlying AWS availability-zone hardware fault took down one Redis node
backing checkout-api's cache. Redis Cluster's automatic failover promoted a
replica within about 2 minutes, but the newly promoted node started cold
(empty), causing a sharp drop in cache hit ratio and a corresponding latency
increase while it repopulated. No manual intervention was required; the
incident self-resolved as the cache warmed back up.

## Impact
- ~3% of checkout attempts between 09:15 and 09:33 UTC experienced elevated
  latency (requests succeeded, but slower than normal).
- No failed checkout attempts and no error-rate increase — this incident
  never crossed the `error-rate-high` threshold, only `p99-latency-high`.
- No customer tickets received.

## Timeline (UTC)
- **09:14** — One Redis node in the `checkout-cache` cluster becomes
  unreachable (AWS ElastiCache host-level failure, confirmed later via AWS
  status history).
- **09:15** — Redis Cluster automatically begins failover to a replica.
  `checkout-api-p99-latency-high` alert fires — this was a sudden step
  change in latency, not a gradual climb.
- **09:16** — On-call checks the connection pool dashboard first, per the
  runbook's triage checklist. `active_connections` is normal (not pinned) —
  Known Issue #1 ruled out.
- **09:17** — On-call checks cache hit ratio next. Dropped sharply from
  ~95% to ~41%.
- **09:18** — `redis-cli cluster info` shows `cluster_state:fail` during
  the failover window.
- **09:20** — Failover completes; `cluster_state:ok`, new primary elected.
  Hit ratio still low — cache is repopulating from a cold state.
- **09:21–09:32** — Hit ratio climbs gradually as traffic repopulates the
  cache; latency tracks back down in step with it.
- **09:33** — Hit ratio back to ~93%, latency back to baseline. Incident
  resolved without any manual action taken.

## Root cause
This was a **cache node failover** incident. The proximate trigger was an
AWS availability-zone hardware fault that took down one underlying Redis
node. Redis Cluster's built-in failover handled node replacement correctly
and automatically — the actual production impact came from a secondary
effect: the newly promoted replica had no cached data, so the increase in
cache misses pushed a larger share of read traffic directly to Postgres
until the cache naturally repopulated. The infrastructure handled the
failure correctly; the latency impact was a side effect of cold-cache
behavior, not a failure of the failover mechanism itself.

## Resolution
No manual mitigation was applied. Redis Cluster's automatic failover
completed on its own; on-call monitored cache hit ratio and latency until
both returned to baseline naturally (~18 minutes total). Resolution type:
self-resolved (infrastructure-automated), not a rollback and not a config
change.

## Contributing factors
1. No alerting exists on individual Redis node health — this incident was
   only detected via checkout-api's downstream symptom (`p99-latency-high`),
   not via a direct signal from the cache layer itself.
2. No cache pre-warming strategy exists for a freshly promoted replica, so
   every failover currently produces a cold-cache period by default.

## What went well
- The triage checklist worked exactly as designed: checking the connection
  pool dashboard first correctly ruled out Known Issue #1 within a minute,
  before moving on to the cache check — despite this incident presenting
  with the same initial alert (`p99-latency-high`) as the May 14 connection
  pool exhaustion incident, the two were correctly distinguished early.
- No manual action was needed or taken; the system's own failover handled
  the fault without human intervention.

## Action items
| # | Action | Owner | Status | GitHub Issue |
|---|---|---|---|---|
| 1 | Add direct node-health alerting for the checkout-cache Redis cluster, independent of downstream latency symptoms | infra-platform | Not started | org/checkout-api#387 |
| 2 | Investigate cache pre-warming options for newly promoted replicas | infra-platform | Not started | n/a |
