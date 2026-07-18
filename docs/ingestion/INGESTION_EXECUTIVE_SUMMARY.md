# IncidentPilot Ingestion Pipeline — Executive Summary

**Audience:** VP of Engineering  |  **Date:** 2026-07-17  |  **Full analysis:** `docs/ingestion/ingestion-analysis.md`

---

## The Problem in One Sentence

Our AI incident-response copilot can only ingest **Markdown files from a single service** — it cannot read PDFs, connect to Confluence, restrict access by role, or reason across services. Every re-index is a full rebuild, and the agent never learns from past incidents.

## Current State: Working L0 MVP

IncidentPilot today delivers cited triage summaries by combining RAG over runbooks/postmortems with live Prometheus/Loki queries. The copilot is functional but limited — it can only read Markdown files for a single service.

## The 7 Gaps (Derived from Codebase)

| # | Gap | Risk | Fix Timeline |
|---|---|---|---|
| 1 | **Single-format, single-source** — `.md` only, hardcoded paths | Cannot ingest PDFs, Confluence, Slack | Phase 0 (1 week) |
| 2 | **No access control** — any user sees all documents | Privilege escalation via LLM | Phase 1 (2 weeks) |
| 3 | **No traceability** — full rebuild every run, no version tracking | Stale citations, no audit | Phase 0 (1 week) |
| 4 | **Single-service scope** — hardcoded to `checkout-api` | Cannot detect cross-service cascades | Phase 2 (2 weeks) |
| 5 | **Tightly coupled pipeline** — monolithic `ingestion.py` | Swapping model/format = code rewrite | Phase 0 (2 weeks) |
| 6 | **No episodic memory** — agent never learns from past incidents | Every incident handled as first-time | Phase 4 (2 weeks) |
| 7 | **No service topology** — no dependency graph | Can't trace blast radius | Phase 2 (3 weeks) |

## The Plan: 6 Phases, 20 Weeks, 1.5 FTE Average

```
Phase 0: Foundation      L0→L1   Wks 1-4   $40K     🟢 Metadata + Loaders + PDF + Incremental sync
Phase 1: Vector DB       L1      Wks 5-6   $20K     🟢 Qdrant + RBAC foundation + HTML/DOCX
Phase 2: Monitoring      L2      Wks 7-10  $50K     🟢 Service graph + Anomaly detection
Phase 3: Diagnostics     L3      Wks 11-14 $50K     🟢 MCP tools + Semantic chunking + Cascade
Phase 4: Agents          L4→L5   Wks 15-20 $80K     🟢 Full RBAC + Memory + Confluence/Slack/Jira
Phase 5: Autonomous      L6      Future    TBD      🔄 Full lifecycle automation
```

**Total investment: ~$240K (20 weeks, 1.5 engineers: 1 FTE backend + 0.5 FTE SRE)**

## Business Value

| Metric | Today | Target (6 months) | Impact |
|---|---|---|---|
| **Incident detection (MTTD)** | 15-45 min manual | < 60s auto | 90% faster |
| **Incident resolution (MTTR)** | 2-6 hours | < 30 min guided | 70% faster |
| **Repeat incidents** | 30-50% | < 5% | Institutional memory |
| **False positive alerts** | 40-60% | < 10% | Learning loop |
| **Runbook freshness** | 14 months stale | Auto-updated | Always current |
| **Postmortem completion** | < 10% | > 80% | Auto-generated |

## Key Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Qdrant migration data loss** | Low | High | Phase 1 dual-write validates 100% parity before cutover |
| **Model accuracy / vendor dependency** | Low | Medium | RAG citations validated by code; Groq free tier adequate |
| **External API rate limits** | Medium | Medium | Phase 4 connectors support scheduled sync + backoff |
| **RBAC false negatives** | Low | Critical | Phase 4 penetration test required before go-live |
| **Graphify accuracy** | Medium | Medium | Phase 2 validates topology vs manual YAML |

## Investment Ask

| Category | Phase 0-2 (10 wks) | Phase 3-4 (10 wks) | Total |
|---|---|---|---|
| Engineering (Python) | 1 FTE | 1 FTE | $160K |
| SRE / Platform | 0.5 FTE | 0.5 FTE | $80K |
| Infrastructure (compute, storage) | $3K | $7K | $10K |
| LLM API costs (Groq — free tier) | $0 | $0 | $0 |
| **Subtotal** | **~$105K** | **~$135K** | **~$240K** |

## Decision Requested

> **Option A (Recommended):** Approve the full 20-week program — $240K, 2 engineers. Delivers L4 capability (RBAC, memory, multi-source) by Week 20.
>
> **Option B (Phase 0 only):** Approve Phase 0 — $40K, 4 weeks. Delivers immediate value (verifiable citations, PDF support, incremental sync) and fully de-risks subsequent phases.
>
> **Option C:** Defer. Risk: the 7 identified gaps widen as more services are added.

*Detailed 20-week roadmap in `docs/ingestion/ingestion-analysis.md` (Section 6-7).*
