---
title: "Postmortem: Checkout Payment Failures — July 18, 2026"
service: payment-service
incident_id: INC-5390
severity: SEV1
tags: [postmortem, payment-service, checkout-api, connection pool exhaustion, cascade]
date: 2026-07-18
duration_minutes: 32
customer_impact: "~11% of checkout attempts failed at the payment step between 10:14 and 10:46 UTC"
related_runbooks: ["payment-service-runbook.md", "checkout-api-runbook.md"]
related_github_issues: ["org/payment-service#118", "org/checkout-api#503"]
---

# Postmortem: Checkout Payment Failures — July 18, 2026

## Summary
A slow leak in payment-service's Postgres connection pool (a background reconciliation job left connections checked out without releasing them) caused payment-service's own latency and error rate to climb over roughly 15 minutes. Because checkout-api's `/payment` step calls payment-service synchronously, checkout-api's own error rate rose in lockstep — the on-call engineer initially triaged checkout-api itself, since that's where the customer-facing alert fired, before distributed-trace data pointed at the real source.

## Impact
- ~11% of checkout attempts between 10:14 and 10:46 UTC failed specifically at the payment step (users could browse and add to cart normally; only checkout's `/payment` call failed).
- Estimated 640 failed payment attempts; support received 22 related tickets, most describing "checkout got stuck after I confirmed my order."
- No double-charges occurred — failed attempts did not reach the charge step.

## Timeline (UTC)
- **10:14** — `checkout-api-error-rate-high` alert fires (5xx rate on `/payment` crosses 2%).
- **10:16** — On-call engineer begins triage on checkout-api, following `checkout-api-runbook.md`. Pool/cache dashboards for checkout-api itself are clean — active_connections flat, cache_hit_ratio flat at ~0.95.
- **10:19** — Engineer queries IncidentPilot with a description of the symptom. The agent's distributed-trace summary flags that failing checkout-api `/payment` spans all carry `error_type=upstream_error` with `payment-service` as the failed dependency, and surfaces one sample failed trace_id showing the full journey stopping at `checkout-api/payment(500)` right after a `payment-service/charge(500)` child span.
- **10:21** — Engineer pivots to payment-service's own dashboard per the trace pointer; `active_connections` confirmed pinned at `max_connections` (80), consistent with Known Issue #1 in `payment-service-runbook.md`.
- **10:24** — Payments-platform on-call paged directly (skipping a redundant checkout-api-side investigation, since the trace data already isolated the dependency).
- **10:33** — Root cause identified: a background reconciliation job (deployed the previous night) was opening a DB connection per batch item and only releasing it on job completion, slowly starving the pool under normal request traffic.
- **10:39** — Reconciliation job killed; connections begin draining back toward baseline.
- **10:46** — payment-service and checkout-api `/payment` both back to baseline; incident resolved.

## Root cause
This was a **connection pool exhaustion** incident in payment-service, not checkout-api. A reconciliation batch job introduced the previous night opened one DB connection per item processed and never released it back to the pool until the entire job completed — effectively a slow connection leak rather than a traffic-driven exhaustion. Because the job ran on a schedule overlapping normal peak traffic, it competed with regular `/charge` requests for the same pool, eventually starving it. checkout-api's own error rate rose purely as a consequence of its `/payment` step's synchronous call into payment-service failing — checkout-api itself never had a resource problem.

## Contributing factors
1. The reconciliation job's connection-handling was not reviewed against the pool's shared capacity before deploy — it was tested in isolation, never alongside representative `/charge` traffic.
2. Without a distributed trace, "checkout-api's error rate is up" and "payment-service's pool is exhausted" would have looked like two separate signals requiring correlation by hand; the trace's `upstream_error` attribution and sample failed journey made the dependency direction immediate rather than inferred.
3. There is no separate pool-utilization alert for payment-service — only the downstream symptom on checkout-api, which fired eight minutes after payment-service's own connections would already have shown sustained pressure.

## What went well
- The distributed-trace summary correctly identified payment-service as the failing dependency within 5 minutes of triage starting, preventing a redundant checkout-api-side investigation.
- Killing the reconciliation job was a clean, fully reversible mitigation with no secondary effects.

## Action items
| # | Action | Owner | Status | GitHub Issue |
|---|---|---|---|---|
| 1 | Add a pool-utilization alert for payment-service independent of checkout-api's downstream symptom alert | payments-platform | Not started | org/payment-service#118 |
| 2 | Require reconciliation/batch jobs to release DB connections per-item, not per-job, and load-test against representative `/charge` traffic before merge | payments-platform | In progress | org/checkout-api#503 |
