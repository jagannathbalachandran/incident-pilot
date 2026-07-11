---
title: "Postmortem: Checkout API Partial Outage — May 14, 2026"
service: checkout-api
incident_id: INC-4821
severity: SEV1
tags: [postmortem, checkout-api, connection pool exhaustion]
date: 2026-05-14
duration_minutes: 47
customer_impact: "~9% of checkout attempts failed or timed out between 14:02 and 14:49 UTC"
related_runbooks: ["checkout-api-runbook.md"]
related_github_issues: ["org/checkout-api#412", "org/checkout-api#415"]
---

# Postmortem: Checkout API Partial Outage — May 14, 2026

## Summary
A deploy that reduced the per-pod Postgres connection pool timeout without adjusting `max_connections` or pool size caused connection exhaustion under normal peak traffic (no unusual load event). This produced elevated checkout latency and a rising 5xx rate for 47 minutes, resolved by rolling back the deploy.

## Impact
- ~9% of checkout attempts between 14:02 and 14:49 UTC either failed with a 503 or exceeded the client-side 10s timeout.
- Estimated 1,140 failed checkout attempts; support received 63 related tickets.
- No data loss or payment double-charges occurred (retries were idempotent).

## Timeline (UTC)
- **13:55** — Deploy of `checkout-api v2.114.0` completes, including a change to reduce `pool_acquire_timeout_ms` from 30000 to 5000 (intended to fail fast on the belief that most acquisition failures were client-side hangs, not real exhaustion).
- **14:02** — `checkout-api-p99-latency-high` alert fires.
- **14:04** — On-call engineer begins triage; checks recent deploys (correctly identifies v2.114.0 as the most recent change) but initially suspects the downstream `fraud-scoring-svc` due to a coincidental, unrelated blip in its dashboard.
- **14:11** — Fraud-scoring-svc on-call confirms their service is healthy; triage redirects back to checkout-api.
- **14:17** — Connection pool dashboard reviewed; `active_connections` confirmed pinned at `max_connections` (200). Error logs show acquisition timeouts consistent with the new 5000ms setting causing faster, more frequent failures rather than fewer.
- **14:24** — Incident declared SEV1; incident commander paged.
- **14:31** — Decision made to roll back to v2.113.2 rather than hotfix forward, given ongoing customer impact.
- **14:38** — Rollback deploy approved by incident commander and executed.
- **14:49** — Latency and error rate return to baseline; incident resolved.

## Root cause
This was a **connection pool exhaustion** incident. The change to `pool_acquire_timeout_ms` was intended as a resilience improvement (fail fast instead of hanging), but it was tested only in staging under low concurrency, where pool exhaustion never actually occurred. In production, at normal peak traffic, pods were already occasionally waiting close to the connection pool limit; shortening the timeout turned "occasionally slow" into "frequently failing," because requests that would have eventually succeeded within 30s now failed at 5s and were retried, compounding pool pressure rather than relieving it.

The retry-on-failure behavior in the client SDK amplified the problem: each failed acquisition triggered a client-side retry, roughly doubling effective request volume against an already-saturated pool.

## Why detection took ~15 minutes longer than it should have
The initial coincidental blip in `fraud-scoring-svc` sent triage down the wrong path for about 7 minutes. **Lesson:** always check the pool/connection dashboard for the affected service directly, in parallel with checking downstream dependencies — don't treat them as sequential steps.

## Contributing factors
1. The connection pool change was not load-tested against realistic peak concurrency before shipping.
2. No alert existed specifically for "connection pool utilization > 90%" — the team only had the downstream symptom alert (`p99-latency-high`), which fired later than it could have.
3. Client-side retry-on-timeout behavior is not currently configured with backoff or a circuit breaker, so retries add load precisely when the system is already struggling.

## What went well
- Rollback decision was made within 7 minutes of correctly identifying root cause, and the rollback itself was clean (no secondary issues).
- Runbook checklist item "check recent deploys first" correctly pointed at the right commit early, even though a false lead delayed acting on it.

## Action items
| # | Action | Owner | Status | GitHub Issue |
|---|---|---|---|---|
| 1 | Load-test any connection-pool or timeout config change against 2x peak concurrency in staging before merge | payments-platform | In progress | org/checkout-api#412 |
| 2 | Add exponential backoff + circuit breaker to checkout client SDK's retry logic | payments-platform | Not started | org/checkout-api#415 |


