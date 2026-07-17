# IncidentPilot Enterprise — 6-Pager

**Document type:** Strategy / Vision / PR-FAQ  
**Status:** v1.0 Draft  
**Author:** Senior Staff Developer  
**Date:** July 2026  
**Audience:** Engineering Leadership, SRE Directors, Platform Team

---

## 1. Who We Are Writing This For

This document is addressed to three audiences:

| Audience | What They Care About |
|---|---|
| **VP of Platform Engineering** | Build vs buy, TCO, enterprise readiness, SLA impact |
| **SRE Directors** | MTTD/MTTR reduction, on-call burnout, blast radius containment |
| **Staff+ Engineers** | Architecture decisions, knowledge graph vs RAG, LLM trade-offs |

---

## 2. Context & Problem Statement

### 2.1 The Current State

Today, incident response at enterprise scale follows a broken pattern:

1. Monitoring alerts fire (PagerDuty, Opsgenie)
2. On-call engineer wakes up at 2am
3. Engineer manually queries dashboards across 4+ tools (Grafana, Kibana, Datadog, CloudWatch)
4. Engineer manually correlates metrics, logs, and traces across services
5. Engineer reads runbooks, identifies root cause, executes remediation
6. Engineer writes postmortem by hand
7. **Repeat** — the same patterns happen again because no institutional memory was captured

### 2.2 The Cost

| Metric | Typical Enterprise (50-200 microservices) |
|---|---|
| MTTD (Mean Time to Detect) | 15-45 minutes |
| MTTR (Mean Time to Resolve) | 2-6 hours |
| On-call pages per week | 10-30 |
| False positive alerts | 40-60% |
| Incidents that are repeats of past issues | 30-50% |
| Postmortems written | < 10% of incidents |

### 2.3 The Root Causes

1. **Tool fragmentation** — Metrics in Prometheus, logs in Loki, traces in Jaeger, runbooks in Confluence, postmortems in Google Docs, past incidents nowhere
2. **No cross-service reasoning** — Each team monitors their own service. No one connects the dots when `checkout-api` fails because `payment-api` broke (cascading failure)
3. **No institutional memory** — The engineer who resolved INC-451 last quarter left the company. The new engineer repeats the same debugging steps
4. **Reactive, not proactive** — Alerts fire after customers are already affected. No early warning system
5. **Uniformed remediation** — Runbooks exist but are stale. Average runbook is 14 months out of date

### 2.4 The Opportunity

An **AI-native incident response copilot** that:

| Problem | Solution | Impact |
|---|---|---|
| Tool fragmentation | Unified query layer over all observability data | One interface, not 4+ |
| No cross-service reasoning | Knowledge graph + cascade reasoner | Root cause identified in seconds |
| No institutional memory | Episodic memory + auto-updating runbooks | Agent gets smarter with every incident |
| Reactive only | Proactive anomaly detection | Alert BEFORE customers notice |
| Stale runbooks | Auto-generated runbooks from incident patterns | Always up-to-date |

---

## 3. Proposed Solution: IncidentPilot Enterprise

### 3.1 Vision Statement

> **IncidentPilot is an AI-native incident-response agent that detects, diagnoses, and (with approval) remediates production incidents across enterprise microservice architectures — reducing MTTD by 90% and MTTR by 70% within 6 months of deployment.**

### 3.2 Core Principle

**The LLM is the narrator, not the reasoner.** All critical reasoning (anomaly detection, cascade scoring, blast radius calculation) happens in deterministic, testable code. The LLM formats the results into human-readable narratives with citations.

```
       ┌──────────┐     ┌──────────┐     ┌──────────┐
       │ Metrics  │     │  Logs    │     │  Traces  │
       └────┬─────┘     └────┬─────┘     └────┬─────┘
            │                │                │
            ▼                ▼                ▼
       ┌──────────────────────────────────────────┐
       │         Deterministic Reasoners          │
       │  ┌──────────┐ ┌──────────┐ ┌──────────┐ │
       │  │ Anomaly  │ │ Cascade  │ │ Blast    │ │
       │  │ Detector │ │ Reasoner │ │ Radius   │ │
       │  └──────────┘ └──────────┘ └──────────┘ │
       │         ┌──────────────┐                 │
       │         │ Service      │                 │
       │         │ Knowledge    │                 │
       │         │ Graph (Neo4j)│                 │
       │         └──────────────┘                 │
       └──────────────────┬───────────────────────┘
                          │ structured data
                          ▼
       ┌──────────────────────────────────────────┐
       │         LLM Narrator (Groq)              │
       │  Formats cascade trees, timelines,       │
       │  citations into natural language response│
       └──────────────────────────────────────────┘
```

### 3.3 Target Metrics

| Metric | Baseline | Target (6 months) |
|---|---|---|
| MTTD | 15-45 min | < 60 seconds (auto-detected) |
| MTTR | 2-6 hours | < 30 min (guided) |
| False positive alerts | 40-60% | < 10% |
| Repeat incidents | 30-50% | < 5% |
| Postmortem completion rate | < 10% | > 80% |
| Runbook freshness | 14 months stale | Auto-updated per incident |

---

## 4. Key Capabilities (By Maturity Level)

### L0: Reactive Q&A (Current State — ✅ Done)

| Capability | Status |
|---|---|
| RAG over runbooks + postmortems | ✅ |
| Live Prometheus/Loki queries | ✅ |
| Data source badges (live vs fallback) | ✅ |
| Contradiction detection (code-level) | ✅ |
| Gradio UI with trace panel | ✅ |

### L1: Instrumented (In Progress)

| Capability | Priority | Effort |
|---|---|---|
| Multi-service metric support (`{service}` label) | P1 | 1 week |
| Grafana dashboard per service | P1 | 3 days |
| Service-scoped runbooks in RAG | P1 | 1 week |

### L2: Proactive Monitor (Next — 🔴 Highest Impact)

The agent watches ALL services continuously, not just when asked.

**Architecture:**
```python
# Background daemon — polls every 30 seconds
class ProactiveMonitor:
    def __init__(self):
        self.anomaly_detector = AnomalyDetector(baseline_store)
        self.service_graph = ServiceGraph(neo4j_connection)
        self.cascade_reasoner = CascadeReasoner()
        self.notifier = SlackNotifier()
    
    def tick(self):
        # 1. Fetch ALL service metrics
        all_metrics = prometheus.query_all_services()
        
        # 2. Detect anomalies (statistical, not LLM)
        anomalies = self.anomaly_detector.detect(all_metrics)
        
        # 3. Trace cascades if multiple services affected
        if len(anomalies) > 1:
            cascade = self.cascade_reasoner.trace(anomalies, self.service_graph)
            self.notifier.alert_cascade(cascade)
        elif len(anomalies) == 1:
            self.notifier.alert_single(anomalies[0])
```

**Enterprise value:** Mean Time to Detection drops from 15-45 minutes to < 60 seconds.

### L3: Diagnostic Agent — Tool Use (MCP Protocol)

The agent runs read-only diagnostic commands to confirm hypotheses.

**Exposed tools:**
- `exec_redis_command(service, "CLUSTER INFO")` — Read Redis state
- `exec_sql_readonly(service, "SELECT count(*) FROM pg_stat_activity")` — Check connection pools
- `check_circuit_breaker(from_service, to_service)` — Check circuit breaker state
- `query_traces(service, timeframe)` — Fetch traces from Jaeger/Tempo
- `check_deploy_history(service, last_n_hours)` — Recent deployments

**Flow:**
```
Anomaly: payment-api error rate at 12%
  → Tool: exec_sql_readonly("payment-api", "SELECT count(*) WHERE wait_event = 'connection'")
  → Result: 47 connections waiting (normal: < 5)
  → Tool: exec_redis_command("payment-api", "CLUSTER INFO")
  → Result: cluster_state=ok
  → Conclusion: Database connection pool exhaustion (not cache)
```

### L4: Remediation Agent (With Approval Gate)

Execute runbook steps with a strict approval workflow.

| Risk Level | Approval Required | Examples |
|---|---|---|
| **LOW** | Auto-execute | Scale up read replicas, enable circuit breaker, toggle feature flag |
| **MEDIUM** | Slack approval (5min timeout) | Restart unhealthy pod, increase connection pool limit |
| **HIGH** | PagerDuty + manual | Rollback deploy, modify firewall rules, restart database |
| **CRITICAL** | Always manual | Schema migration, credential rotation, production failover |

```python
class RemediationAgent:
    RISK_LEVELS = {
        "scale_up_replica": "LOW",
        "toggle_circuit_breaker": "LOW",
        "restart_pod": "MEDIUM",
        "increase_pool_size": "MEDIUM",
        "rollback_deploy": "HIGH",
        "restart_database": "CRITICAL",
    }
    
    def remediate(self, cascade, runbook_step):
        action = runbook_step.action
        risk = self.RISK_LEVELS.get(action, "HIGH")
        
        if risk == "LOW":
            return self._auto_execute(action)
        elif risk == "MEDIUM":
            return self._await_slack_approval(action)
        elif risk >= "HIGH":
            return self._page_manual(action)
```

### L5: Learning Agent

The agent improves itself from every incident interaction.

**What it learns:**

1. **Anomaly thresholds** — If engineer marks alert as "false positive", widen the threshold
2. **Cascade weights** — If the predicted root cause was wrong, adjust the scoring algorithm
3. **Runbooks** — If engineer takes a step not in the runbook, append it as a new Known Issue
4. **Memory** — Every incident + resolution is stored as an embedding for future recall

```python
def post_incident_learning(cascade, resolution, engineer_feedback):
    """Called after every incident is resolved."""
    
    # Update anomaly baseline
    if engineer_feedback.score < 3:
        baseline = get_baseline(cascade.service, cascade.metric)
        baseline.adjust_threshold(cascade.deviation)
    
    # Store in episodic memory
    IncidentMemory.store(
        cascade=cascade,
        resolution=resolution,
        embedding=embed(f"{cascade.summary} {resolution}"),
        feedback=engineer_feedback
    )
```

### L6: Autonomous Agent (Target State)

For known incident types, the agent handles the full lifecycle without human intervention:

1. **Detect** anomaly → **2. Diagnose** root cause → **3. Remediate** (auto-approved) → **4. Verify** metrics return to baseline → **5. Document** postmortem → **6. Update** runbook

For unknown incident types, it pages the on-call engineer with a pre-populated diagnosis.

---

## 5. Implementation Plan

### Phase 1: Foundation (Weeks 1-4)

| Week | Deliverable | Dependencies |
|---|---|---|
| W1 | Service Knowledge Graph schema + Neo4j setup | Neo4j instance |
| W2 | Multi-service metrics in Prometheus (`{service}` label) | Flask generator update |
| W3 | Per-service runbooks in RAG corpus | Content creation |
| W4 | Grafana dashboards × 3 services | Dashboard provisioning |

### Phase 2: Proactive Monitoring (Weeks 5-8)

| Week | Deliverable | Dependencies |
|---|---|---|
| W5 | Baseline store (PostgreSQL + TimescaleDB) | P1 |
| W6 | Statistical anomaly detector | P1 + metrics history |
| W7 | Proactive monitor daemon | P2 |
| W8 | Slack/PagerDuty notification integration | P3 |

### Phase 3: Cascade Detection (Weeks 9-12)

| Week | Deliverable | Dependencies |
|---|---|---|
| W9 | Cascade reasoner (DAG walker + scoring) | P1 + P2 |
| W10 | Blast radius calculator | P1 |
| W11 | Multi-service cascade scenarios in simulator | P1 |
| W12 | E2E cascade detection test | All of P3 |

### Phase 4: Agentic Capabilities (Weeks 13-20)

| Week | Deliverable | Dependencies |
|---|---|---|
| W13 | MCP tool protocol (read-only tools) | P1 + P2 |
| W14 | Remediation agent with approval gate | P3 |
| W15 | Episodic memory store | P3 |
| W16 | Post-incident learning loop | P3 |
| W17-18 | Multi-agent architecture (Monitor + Diagnose + Remediate + Learn) | P4 |
| W19 | Self-service dashboard (Gradio pro) | All |
| W20 | Enterprise hardening (RBAC, audit, SSO) | All |

### Resource Estimates

| Role | Phase 1-2 | Phase 3-4 | Total |
|---|---|---|---|
| Backend Engineer (Python) | 1 FTE | 1 FTE | 2 FTE |
| SRE / Platform Engineer | 0.5 FTE | 0.5 FTE | 1 FTE |
| AI/ML Engineer | 0 | 0.5 FTE | 0.5 FTE |
| Product Manager | 0.25 FTE | 0.25 FTE | 0.25 FTE |
| **Total** | **1.75 FTE** | **2.25 FTE** | **3.75 FTE** |

### Cost Summary (20 Weeks)

| Category | Cost |
|---|---|
| Engineering (3.75 FTE × 20 weeks) | $225,000 |
| Infrastructure (Neo4j, PostgreSQL, compute) | $15,000 |
| LLM API costs (Groq — 14,400 req/day free tier) | $0 (free tier) |
| **Total Investment** | **$240,000** |

---

## 6. ROI Analysis

### 6.1 The Cost of Incidents Today

For an enterprise with ~100 microservices, the annual incident cost is analyzed below.

**Assumptions:**
- 50 incidents/month requiring on-call escalation
- 3 SREs on rotation (avg salary: $180K/year, fully loaded: $250K)
- MTTR: 4 hours average
- 40% of incidents are repeats of past issues
- 10% of incidents cause customer-facing downtime

#### Direct Costs (Annual)

| Cost Item | Calculation | Amount |
|---|---|---|
| On-call engineering time | 50 incidents × 4 hrs × $120/hr | $24,000/month |
| Annual engineering time | × 12 months | **$288,000/year** |
| Context-switching tax | 30% overhead between incidents | **$86,400/year** |
| Postmortem writing | 4 hrs × 50 incidents × 20% completion rate × $120/hr | **$4,800/year** |
| **Total direct cost** | | **$379,200/year** |

#### Revenue Impact (Annual)

| Impact | Calculation | Amount |
|---|---|---|
| Customer-facing downtime | 50 incidents × 10% = 5 major outages/year | |
| Lost revenue per major outage | ~$50K (e-commerce / SaaS) | |
| **Annual revenue impact** | 5 × $50K | **$250,000/year** |
| Customer churn from incidents | 2% churn attributable to reliability | **Variable** |

#### Total Annual Incident Cost

| Category | Amount |
|---|---|
| Direct engineering time | $379,200 |
| Revenue impact | $250,000 |
| **Total** | **~$630,000/year** |

### 6.2 Projected Savings with IncidentPilot Enterprise

| Lever | Mechanism | Projected Reduction | Annual Savings |
|---|---|---|---|
| **MTTD reduction** | Proactive monitoring catches issues before page | 90% (45 min → < 60s) | $0 (MTTD doesn't directly save time — but prevents escalation) |
| **MTTR reduction** | Guided triage + cascade detection + episodic memory | 70% (4 hrs → 1.2 hrs) | **$201,600/year** |
| **Repeat incident elimination** | Episodic memory recalls past resolutions | 90% reduction (40% → 4%) | **$68,000/year** |
| **False positive reduction** | Learning loop auto-adjusts thresholds | 80% reduction (50% → 10%) | **$60,000/year** (fewer pages, less burnout) |
| **Postmortem automation** | Auto-generated postmortems from trace data | 80% completion rate (10% → 90%) | **$34,000/year** |
| **Revenue preservation** | Faster resolution = less downtime | 60% fewer major outages (5 → 2/year) | **$150,000/year** |
| **On-call burnout reduction** | 60% fewer pages | Reduced SRE turnover | **~$50,000/year** (replacement cost) |

#### Total Projected Annual Savings

| Category | Without IncidentPilot | With IncidentPilot | Savings |
|---|---|---|---|
| Engineering time | $379,200 | $75,600 | **$303,600** |
| Revenue impact | $250,000 | $100,000 | **$150,000** |
| Turnover / burnout | $50,000 | $10,000 | **$40,000** |
| **Total** | **$679,200** | **$185,600** | **~$494,000/year** |

### 6.3 ROI Timeline

| Time | Cumulative Investment | Cumulative Savings | Net |
|---|---|---|---|
| **Month 3** (P1-P2: Foundation + Monitoring) | $90,000 | $30,000 | -$60,000 |
| **Month 6** (P3: Cascade Detection) | $150,000 | $150,000 | **$0 (breakeven)** |
| **Month 9** (P4: Agentic + Learning) | $210,000 | $370,000 | +$160,000 |
| **Month 12** | $240,000 | $494,000 | **+$254,000 ROI** |

**Breakeven: Month 6** (within Phase 3 — Cascade Detection)
**Year 1 ROI: 106%** ($494K savings on $240K investment)
**Year 2+ ROI: 206%** ($494K annual savings with no further investment)

### 6.4 Intangible Benefits (Hard to Quantify, High Value)

| Benefit | Impact |
|---|---|
| **On-call engineer quality of life** | 60% fewer pages → better sleep, higher retention |
| **Institutional memory preservation** | Knowledge doesn't leave when engineers do |
| **Faster onboarding** | New SREs get an AI copilot that knows the system |
| **Runbook quality** | Auto-updated after every incident |
| **Audit readiness** | Every decision logged, every action approved |
| **Compliance** | SOC2 evidence trails generated automatically |

### 6.5 Comparison: Build vs Buy

| Factor | Build (IncidentPilot E) | Buy (PagerDuty AIOps) | Buy (Datadog Bits AI) |
|---|---|---|---|
| **Annual cost** | $240K (build) + $50K (ops) = **$290K year 1** | ~$120K | ~$200K |
| **Year 1 ROI** | +$254K (net positive) | Vendor lock-in, limited scope | Vendor lock-in, DD-only |
| **Customization** | Full — own the codebase | Limited to vendor's roadmap | Limited |
| **Data residency** | Self-hosted, full control | Vendor-hosted | Vendor-hosted |
| **Integration** | Any observability stack | PagerDuty ecosystem only | Datadog only |
| **Lock-in risk** | None (open source) | High | High |

**Recommendation:** Build. The 6-month breakeven and full ownership of the codebase outweigh the perceived lower upfront cost of vendor solutions.

---

## 7. FAQ, Risks, and Open Questions

### FAQ

**Q: Why not use Datadog's AI (Bits AI) instead of building this?**
A: Bits AI is Datadog-only. IncidentPilot is observability-agnostic — it queries any Prometheus-compatible store, any Loki-compatible log store, any Neo4j graph. It's designed to integrate with existing enterprise observability stacks, not replace them.

**Q: Why Neo4j and not a simple YAML file?**
A: For 3 services, YAML works. For 50+ services, you need graph traversal queries (transitive dependencies, blast radius, shared dependencies). Neo4j also supports real-time updates from service mesh — when Istio detects a new service, the graph updates automatically.

**Q: Why deterministic anomaly detection instead of ML?**
A: ML models can drift, hallucinate, and require retraining. Statistical threshold-based detection (p95/p99 baselines) is deterministic, testable, and explainable. We add ML later for edge cases (unusual seasonal patterns, novel incident types).

**Q: How does the LLM stay within its role?**
A: The LLM never sees raw data. It receives structured analysis from the deterministic reasoners. Its only job is to format pre-computed results into human-readable text. This is enforced at the architecture level, not the prompt level.

### Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| LLM hallucinates citations | Low | Medium | RAG citations are validated by code before injection |
| Service graph becomes stale | Medium | High | Sync from service mesh (Istio) + TTL-based refresh |
| False positives erode trust | Medium | High | Learning loop auto-adjusts thresholds based on feedback |
| Remediation actions cause damage | Low | Critical | Defense-in-depth: approval gate + risk scoring + audit log |
| Knowledge graph doesn't scale | Low | Medium | Neo4j is designed for enterprise scale (1000s of nodes) |

### Open Questions

1. **Observability-agnostic integration** — Should IncidentPilot provide its own lightweight observability stack (current model) or integrate with existing enterprise stacks (Datadog, New Relic, Grafana Cloud)?

2. **Multi-region support** — Should the agent reason across regions, or is single-region the initial target?

3. **Compliance** — SOC2, HIPAA, PCI-DSS requirements for remediation actions? Audit trail retention policy?

4. **LLM model strategy** — Single model (current: llama-3.3-70b) vs. model routing (small model for simple queries, large model for complex cascade analysis)?

5. **Pricing model** — Per-seat SaaS vs self-hosted enterprise license?

---

## Appendix A: Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           IncidentPilot Enterprise                            │
│                                                                               │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                          Agent Orchestrator                            │   │
│  │  (Routes queries, manages agent lifecycle, aggregates results)         │   │
│  └──────┬──────────┬──────────┬──────────┬──────────┬──────────┬─────────┘   │
│         │          │          │          │          │          │             │
│         ▼          ▼          ▼          ▼          ▼          ▼             │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐│
│  │Proactive │ │Diagnostic│ │Remediation│ │  Comms   │ │ Learning │ │Document  ││
│  │ Monitor  │ │  Agent   │ │  Agent   │ │  Agent   │ │  Agent   │ │ Agent    ││
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘│
│       │            │            │            │            │            │      │
│       ▼            ▼            ▼            ▼            ▼            ▼      │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                      Shared Services Layer                            │   │
│  │                                                                       │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐   │   │
│  │  │ Knowledge    │  │ Anomaly      │  │ Episodic Memory          │   │   │
│  │  │ Graph (Neo4j)│  │ Detector     │  │ (PostgreSQL + vector)    │   │   │
│  │  └──────────────┘  └──────────────┘  └──────────────────────────┘   │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐   │   │
│  │  │ Cascade      │  │ Baseline     │  │ Tool Executor            │   │   │
│  │  │ Reasoner     │  │ Store        │  │ (read-only + approved)   │   │   │
│  │  └──────────────┘  └──────────────┘  └──────────────────────────┘   │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                               │                                              │
│                               ▼                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                      Data Layer                                        │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────┐  │   │
│  │  │Prometheus │ │  Loki    │ │  Jaeger  │ │  Neo4j   │ │  Runbook  │  │   │
│  │  │(metrics)  │ │  (logs)  │ │ (traces) │ │ (graph)  │ │  Corpus   │  │   │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └───────────┘  │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Appendix B: Open-Source Technology Integration Landscape

A curated assessment of the current (2025-2026) open-source ecosystem for each capability layer of IncidentPilot Enterprise.

---

### B.1 Service Knowledge Graph

| Technology | License | Best For | Why for IncidentPilot |
|---|---|---|---|
| **Neo4j** | GPLv3 (Community) / Commercial | Established enterprises, rich ecosystem | Industry standard. Largest community, APOC library, GraphRAG support. Safe choice. |
| **FalkorDB** | SSPL (source-available) | Real-time, low-latency graph traversals | 10-100x faster than Neo4j for multi-hop queries. Excellent for blast-radius queries. |
| **ArcadeDB** | **Apache 2.0** | Multi-model (graph + document + vector) | Fully OSI-approved license. Built-in vector search for GraphRAG. Native MCP support. |
| **Apache TinkerPop** | Apache 2.0 | Vendor-agnostic graph API | Framework, not a database. Use Gremlin to query any backend (JanusGraph, Neptune, etc.). |

**Recommendation:** Start with **Neo4j** (maturity, community). Evaluate **FalkorDB** if multi-hop blast-radius queries become a bottleneck. Consider **ArcadeDB** if licensing restrictions apply (Apache 2.0).

---

### B.2 Multi-Agent Orchestration

| Technology | Language | MCP Support | Best For |
|---|---|---|---|
| **LangGraph** | Python | ✅ Native | Stateful, cyclic agent workflows with human-in-the-loop checkpoints. Production-grade. |
| **CrewAI** | Python | ✅ Native | Role-based agent teams (Researcher, Diagnoser, Remediation). Fast prototyping. |
| **Microsoft Agent Framework** | Python / .NET | ✅ Native | Enterprise-scale, event-driven multi-agent systems. Unified AutoGen + Semantic Kernel. |
| **Mastra** | TypeScript | ⚠️ Community | Edge/Node.js teams. Lightweight workflow graph primitives. |
| **Smolagents** | Python | ❌ | Minimalist code-first approach. Agents write Python for tool execution. |

**Recommendation:** Use **LangGraph** for the core orchestrator (best MCP support, production durability). Supplement with **CrewAI** for role-based agent definitions (Monitor, Diagnose, Remediate, Learn).

---

### B.3 Distributed Tracing & Service Topology Discovery

| Technology | License | Auto-Discovery | Best For |
|---|---|---|---|
| **OpenTelemetry** | Apache 2.0 | ✅ Via `service_graph` connector | Industry standard. Transforms trace spans into topology metrics (Prometheus). Powers all modern stacks. |
| **SigNoz** | Apache 2.0 (core) / BSL | ✅ Built-in | Unified platform built on OpenTelemetry + ClickHouse. Native dependency maps from traces. |
| **Grafana Tempo** | AGPLv3 | ⚠️ Via OTel collector | Scalable trace storage. Relies on OTel service_graph + Prometheus for topology. |
| **Jaeger** | Apache 2.0 | ✅ Built-in (System Architecture graph) | Mature tracing backend. Native UI for service topology and deep dependency graphs. |
| **SkyWalking** | Apache 2.0 | ✅ Native (agent-based) | Best auto-instrumentation (no code changes). Robust enterprise topology maps. |

**Recommendation:** Use **OpenTelemetry** as the foundation (cannot avoid — it's the industry standard). For trace storage + topology, **SigNoz** offers the best integrated experience. If already using Grafana stack, pair **Tempo** with the OTel `service_graph` connector.

---

### B.4 Incident Management & On-Call Scheduling

| Technology | License | Incident Lifecycle | On-Call Scheduling | Status |
|---|---|---|---|---|
| **OneUptime** | Apache 2.0 | ✅ Full lifecycle + postmortems | ✅ Built-in rotations + escalations | 🟢 Active (2026) |
| **Grafana OnCall (OSS)** | AGPLv3 | ⚠️ Partial | ✅ | 🔴 **Archived** (March 2026) |
| **Uptime Kuma** | MIT | ❌ Monitoring only | ❌ | 🟢 Active — but not incident management |
| **Cabot** | MIT | ⚠️ Basic | ❌ | 🔴 **Unmaintained** |

**Recommendation:** **OneUptime** is the clear winner — actively maintained, Apache 2.0, full PagerDuty-style incident lifecycle, and has a robust API for automated incident creation. Grafana OnCall OSS is dead (archived).

---

### B.5 Durable Execution (For Long-Running Agent Workflows)

| Technology | License | Best For |
|---|---|---|
| **Temporal** | MIT | Durable execution for long-running agent workflows. Ensures agents resume from last state if they crash mid-workflow. |
| **Apache Airflow** | Apache 2.0 | Scheduled DAG-based workflows. Overkill for real-time agent orchestration but familiar for ops teams. |

**Recommendation:** **Temporal** pairs well with LangGraph for durable agent execution. Use if agents perform multi-minute diagnostic or remediation sequences that must survive process restarts.

---

### B.6 Summary Integration Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        IncidentPilot Enterprise Tech Stack                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                               │
│  User Interfaces                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────────────┐   │
│  │ Gradio UI    │  │ Slack        │  │ Grafana Dashboards                │   │
│  │ (Triage +    │  │ (alerts +    │  │ (4 per service × N services)     │   │
│  │  Control)    │  │  approvals)  │  │                                  │   │
│  └──────────────┘  └──────────────┘  └──────────────────────────────────┘   │
│                                                                               │
│  Agent Layer (LangGraph + CrewAI)                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  Orchestrator → Monitor Agent → Diagnose Agent → Remediate Agent     │   │
│  │                                          ↓                           │   │
│  │                              Temporal (Durable Execution)            │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                               │
│  Reasoning Layer                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │ Service Graph│  │ Cascade      │  │ Anomaly      │  │ Episodic     │   │
│  │ (Neo4j/      │  │ Reasoner     │  │ Detector     │  │ Memory       │   │
│  │  FalkorDB)   │  │ (Python/DAG) │  │ (statistical)│  │ (PG + vector)│   │
│  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘   │
│                                                                               │
│  Data Layer                                                                   │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐  │
│  │Prometheus│  │ Loki     │  │ SigNoz/  │  │ Neo4j    │  │ OneUptime    │  │
│  │(metrics) │  │ (logs)   │  │ Tempo    │  │ (graph)  │  │ (incidents)  │  │
│  │          │  │          │  │ (traces) │  │          │  │              │  │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘  └──────────────┘  │
│                                                                               │
│  LLM Layer (MCP Protocol)                                                     │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  Groq → llama-3.3-70b (primary, fast triage)                          │   │
│  │  Anthropic → Claude (complex cascade analysis, when Groq insufficient)│   │
│  │  MCP servers → Neo4j MCP, PostgreSQL MCP, Filesystem MCP             │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### B.7 Technology Selection Decision Tree

```
Do you need service topology auto-discovery from traces?
├── Yes → Use OpenTelemetry collector + SigNoz (all-in-one)
│           Or → OTel + Tempo + Prometheus (best-of-breed)
└── No  → Use manual YAML service-map (simpler, fewer dependencies)

Do you need on-call scheduling + incident lifecycle?
├── Yes → OneUptime (Apache 2.0, fully open-source)
└── No  → Uptime Kuma (monitoring-only, lightweight)

Do you need multi-agent orchestration with human-in-the-loop?
├── Yes → LangGraph + Temporal (production-grade)
└── No  → CrewAI (simpler, faster to prototype)

Do you need real-time graph traversals for blast radius?
├── Yes → FalkorDB (sub-millisecond queries)
└── No  → Neo4j (mature, well-supported)

Are Apache 2.0 licensing critical for compliance?
├── Yes → ArcadeDB (Apache 2.0 Graph + Vector + Document)
└── No  → Neo4j (larger ecosystem)
```

---

## Appendix C: Competitive Landscape

### C.1 Full-Stack Incident Response Platforms

| Feature | IncidentPilot E | PagerDuty AIOps | Datadog Bits AI | OneUptime | Grafana IR | Moogsoft |
|---|---|---|---|---|---|---|
| **Open source** | ✅ Full (MIT/Apache) | ❌ Proprietary | ❌ Proprietary | ✅ Apache 2.0 | ✅ AGPLv3 | ❌ Proprietary |
| **Self-hostable** | ✅ Full | ❌ | ❌ | ✅ | ✅ | ❌ |
| **Observability-agnostic** | ✅ Any stack | ❌ PagerDuty ecosystem | ❌ Datadog only | ✅ Multi-source | ✅ Pluggable | ⚠️ Limited |
| **Cross-service cascade** | ✅ Knowledge Graph (Neo4j/FalkorDB) | ❌ | ❌ | ❌ | ❌ | ⚠️ Basic correlation |
| **Proactive monitoring** | ✅ Statistical anomaly detection | ✅ | ✅ | ✅ | ⚠️ | ✅ |
| **Self-healing (approval gate)** | ✅ LOW auto / MEDIUM Slack / HIGH manual | ❌ | ❌ | ❌ | ❌ | ❌ |
| **Episodic memory** | ✅ Vector + PostgreSQL | ❌ | ❌ | ❌ | ❌ | ❌ |
| **Auto-updating runbooks** | ✅ From incident patterns | ❌ | ❌ | ❌ | ❌ | ❌ |
| **LLM narration** | ✅ Groq + MCP | ⚠️ Limited | ✅ | ❌ | ❌ | ❌ |
| **Multi-agent architecture** | ✅ LangGraph + CrewAI | ❌ | ❌ | ❌ | ❌ | ❌ |
| **MCP Protocol** | ✅ Native | ❌ | ❌ | ❌ | ❌ | ❌ |
| **On-call scheduling** | 🔄 OneUptime integration | ✅ | ❌ | ✅ Built-in | ❌ | ✅ |
| **Incident lifecycle** | 🔄 OneUptime integration | ✅ Full | ⚠️ | ✅ Full | ❌ | ✅ |
| **Distributed tracing** | 🔄 SigNoz/Tempo integration | ❌ | ✅ (APM) | ⚠️ | ✅ Tempo | ⚠️ |
| **Postmortem automation** | ✅ Auto-generated from cascade | ❌ | ❌ | ✅ | ❌ | ❌ |
| **Annual cost (100 services)** | **$290K year 1** (build) | ~$120K | ~$200K | ~$60K (self-hosted) | ~$60K (Grafana + Tempo) | ~$150K |

---

### C.2 Specialized Point Solutions

| Category | Technology | What It Does | Where IncidentPilot Integrates |
|---|---|---|---|
| **Knowledge Graph** | Neo4j / FalkorDB / ArcadeDB | Service dependency topology, blast radius queries | ✅ Core reasoning layer |
| **Multi-Agent Framework** | LangGraph / CrewAI | Agent orchestration with human-in-the-loop | ✅ Core orchestration layer |
| **Distributed Tracing** | SigNoz / Tempo + OpenTelemetry | Trace storage + topology auto-discovery | ✅ Data layer (traces → topology metrics) |
| **Incident Management** | OneUptime | On-call scheduling, escalation, status pages | 🔄 Planned integration (API-driven) |
| **Durable Execution** | Temporal | Long-running workflow reliability | 🔄 Planned integration |
| **LLM Inference** | Groq / Anthropic / Ollama | Fast LLM inference for triage narration | ✅ Core LLM layer |
| **Vector Store** | ChromaDB / Qdrant | Embedding storage for RAG + episodic memory | ✅ Current (ChromaDB) |
| **Anomaly Detection** | Custom (statistical) / Prophet | Baseline calculation + threshold detection | ✅ Custom reasoner |

---

### C.3 Vendor Lock-In Risk Assessment

| Platform | Lock-In Vector | Migration Cost | Risk |
|---|---|---|---|
| **Datadog Bits AI** | Agent instrumentation, metric naming, dashboard DSL | **Very High** — full re-instrumentation required | 🔴 Critical |
| **PagerDuty AIOps** | Notification routing, escalation rules, PagerDuty-only integrations | **Medium** — rules porting required | 🟡 Medium |
| **Grafana IR** | Grafana ecosystem (Tempo, Loki, Mimir) | **Medium** — standard PromQL/LogQL, but Grafana-specific | 🟡 Medium |
| **Moogsoft** | Proprietary event format, ML models | **High** — proprietary data formats | 🔴 High |
| **IncidentPilot E** | None — open source, standard PromQL/LogQL/Cypher | **Low** — standard query languages, self-hosted | 🟢 None |

---

## Appendix D: New Open-Source Discovery — Comparative Analysis

This section analyzes 4 recently discovered open-source tools that could accelerate or alter the IncidentPilot Enterprise architecture. Each tool is assessed for fit against our current design.

---

### D.1 QMD (by Shopify CEO Tobi Lütke)

| Attribute | Detail |
|---|---|
| **Repository** | [github.com/tobi/qmd](https://github.com/tobi/qmd) |
| **License** | NPM package (MIT license)
| **Language** | Node.js / Bun
| **Core Function** | Local hybrid search engine for markdown documents — BM25 + vector + LLM reranking, all running locally via `node-llama-cpp` with GGUF models
| **MCP Support** | ✅ Native — exposes MCP server with `query`, `get`, `multi_get`, `status` tools
| **SDK Available** | ✅ `@tobilu/qmd` — full TypeScript SDK for programmatic use

#### Architecture Fit

```
┌─────────────────────────────────────────────────────────────────────┐
│          QMD Integration in IncidentPilot RAG Layer                  │
└─────────────────────────────────────────────────────────────────────┘

Current (ChromaDB):                          QMD Alternative:
┌──────────────┐                              ┌─────────────────────────┐
│  Chunk docs  │                              │  Index markdown files   │
│  → Embed     │                              │  → BM25 + Vector + RRF │
│  → ChromaDB  │                              │  → QMD SQLite Index    │
│  → Cosine    │                              │  → Optional Reranking  │
│    search    │                              │  → MCP protocol query  │
└──────────────┘                              └─────────────────────────┘
```

#### Strengths for IncidentPilot

| Strength | Why It Matters |
|---|---|
| **MCP-native** | Direct MCP server integration — no adapter needed. The agent calls `qmd query "how to diagnose pool exhaustion"` via MCP |
| **Hybrid search** | BM25 (keyword) + vector (semantic) + LLM reranking — better than pure vector search for runbooks with technical terminology |
| **Context tree** | Hierarchical context support (`qmd context add qmd://docs "Runbooks"`) helps the LLM make better contextual decisions |
| **Local GGUF models** | No external API dependency for RAG — all models run locally (300MB embedding + 640MB reranker + 1.1GB query expansion) |
| **Position-aware blending** | Preserves exact keyword matches near the top — critical for runbook steps that must be read verbatim |
| **SDK** | Can be embedded as a library — not just a CLI tool |

#### Limitations vs ChromaDB

| Limitation | Impact |
|---|---|
| **Node.js dependency** | Requires Node.js ≥ 22 or Bun. Adds a runtime dependency to an otherwise Python-only stack |
| **File-based indexing** | Scans filesystem at `index` time — documents must be on disk in markdown format. No dynamic document injection via API |
| **Single-user focus** | Designed for personal usage. Concurrency under high agent load is untested |
| **Limited to markdown** | PDF, HTML, and other formats not supported natively |
| **Model download on first use** | ~2GB of GGUF models downloaded on first run |

#### Fit Score: ★★★★☆ (4/5)

**Best use case:** Replace ChromaDB for RAG over runbooks + postmortems. The MCP-native integration and hybrid search with position-aware blending make it superior for technical documentation retrieval.

**Recommendation:** Integrate as an optional upgrade path. Keep ChromaDB as the default (simpler, Python-native), but document QMD as the preferred choice for production deployments where retrieval quality matters.

---

### D.2 Mnemon — Graph Memory for AI Agents

| Attribute | Detail |
|---|---|
| **Repository** | [github.com/mnemon-dev/mnemon](https://github.com/mnemon-dev/mnemon) |
| **License** | Apache 2.0
| **Language** | Go (single binary)
| **Core Function** | LLM-supervised persistent memory for AI agents. Four-graph knowledge store (temporal, entity, causal, semantic) with intent-aware recall, importance decay, and automatic deduplication
| **MCP Support** | ❌ Not MCP — uses CLI commands invoked by the host LLM
| **Pattern** | "LLM-Supervised" — the host LLM decides what to remember, link, and forget; the binary handles deterministic computation

#### Architecture Fit

```
┌─────────────────────────────────────────────────────────────────────┐
│          Mnemon as IncidentPilot's Episodic Memory                   │
└─────────────────────────────────────────────────────────────────────┘

Current (no episodic memory):              With Mnemon:
┌──────────────┐                              ┌─────────────────────────┐
│  Query → RAG │                              │  Query → RAG            │
│  → LLM       │                              │  → mnemon recall "past  │
│  → Response  │                              │    pool exhaustion"     │
│              │                              │  → LLM compares current │
│  Past        │                              │    vs past              │
│  incidents   │                              │  → mnemon remember new  │
│  are lost    │                              │    after resolution     │
└──────────────┘                              └─────────────────────────┘
```

#### Strengths for IncidentPilot

| Strength | Why It Matters |
|---|---|
| **LLM-Supervised pattern** | The host LLM (Groq) decides what to remember — no embedded LLM, no extra inference cost. This aligns perfectly with our principle: "LLM is the narrator, not the reasoner" |
| **Four-graph architecture** | Temporal (when incidents happened), entity (which services involved), causal (root cause → effect chains), semantic (similarity between descriptions) — this is exactly what we need for incident memory |
| **Intent-aware recall** | Graph traversal + optional vector search. When an engineer asks "has this happened before?", Mnemon returns relevant past incidents |
| **Built-in deduplication** | If the same incident repeats, Mnemon auto-detects duplicates and consolidates rather than creating new entries |
| **Importance decay + GC** | Old, irrelevant memories fade out automatically — the store stays fresh |
| **Cross-session persistence** | Memory is shared across all sessions. What the agent learns from one incident helps in future sessions |
| **Privacy-safe receipts** | Hashed operation receipts for audit without exposing raw memory contents |
| **Zero external API keys** | Single binary, no external dependencies |
| **Named stores** | Can isolate memory per service (`MNEMON_STORE=checkout-api`) or per team |

#### Integration Path

Mnemon provides three primitives that map directly to our needs:

```python
# Conceptual integration in IncidentPilot

class IncidentMemory:
    def __init__(self):
        self.store_name = "incident-pilot"
    
    def remember_incident(self, cascade_summary, resolution, service):
        """Store an incident after resolution."""
        subprocess.run([
            "mnemon", "remember",
            f"After resolving {cascade_summary}, the fix was: {resolution}",
            "--store", self.store_name,
            "--tag", f"service:{service}"
        ])
    
    def recall_similar(self, query, k=3):
        """Find similar past incidents."""
        result = subprocess.run([
            "mnemon", "recall", query,
            "--store", self.store_name,
            "--limit", str(k),
            "--format", "json"
        ], capture_output=True, text=True)
        return json.loads(result.stdout)
    
    def link_incidents(self, cascade):
        """Link related incidents together."""
        for root_cause, effect in cascade.traversal.items():
            subprocess.run([
                "mnemon", "link", root_cause, effect,
                "--relation", "caused",
                "--store", self.store_name
            ])
```

**Installation is trivial:**
```bash
brew install mnemon-dev/tap/mnemon
mnemon setup  # auto-detects Claude Code
```

#### Limitations

| Limitation | Impact | Mitigation |
|---|---|---|
| **No MCP protocol** | Cannot integrate via MCP — must use CLI calls from Python | CLI calls are fine for our Python backend. We wrap them in a clean API |
| **Single binary, no Go SDK** | Cannot embed — must subprocess | Subprocess overhead is negligible for memory operations (once per query) |
| **Relatively new** (2026) | Less battle-tested | Start non-critical (learning loop), promote to critical once validated |
| **Ollama dependency for embeddings** | Optional — for vector+keyword hybrid search | Can work without embeddings using keyword search only |

#### Fit Score: ★★★★★ (5/5)

**Best use case:** Episodic memory layer for L5 (Learning Agent). This is the missing piece that makes the agent get smarter with every incident.

**Recommendation:** **Adopt immediately.** Single binary, no external dependencies, Apache 2.0, and the LLM-Supervised pattern matches our architecture perfectly. It replaces a more complex custom PostgreSQL + vector store implementation.

---

### D.3 Graphify — Folder to Knowledge Graph

| Attribute | Detail |
|---|---|
| **Repository** | [github.com/Graphify-Labs/graphify](https://github.com/Graphify-Labs/graphify) (moved from safishamsi/graphify)
| **License** | PyPI package
| **Language** | Python
| **Core Function** | Turn any folder of code, docs, PDFs, images, or videos into a queryable knowledge graph. Code is parsed with tree-sitter AST (deterministic, no LLM). Docs/media use an LLM for semantic pass
| **MCP Support** | ✅ Via `graphifyy[mcp]` extra — exposes MCP stdio server
| **Neo4j/FalkorDB Support** | ✅ Native — can push graph to Neo4j (`graphifyy[neo4j]`) or FalkorDB (`graphifyy[falkordb]`)

#### Architecture Fit

```
┌─────────────────────────────────────────────────────────────────────┐
│          Graphify for Service Knowledge Graph Bootstrapping          │
└─────────────────────────────────────────────────────────────────────┘

Without Graphify:                           With Graphify:
┌──────────────────────────┐                ┌──────────────────────────────┐
│ Manual YAML service map  │                │ graphify . on each service  │
│ Define by hand:          │                │ repo → generates graph.json │
│   checkout-api depends   │                │ with AST-parsed internals + │
│   on payment-api         │                │ cross-file dependency edges │
│ Time: 2-3 days           │                │ Push to Neo4j → query via   │
│ Prone: stale, inaccurate │                │ Cypher                      │
└──────────────────────────┘                │ Time: 30 seconds per repo   │
                                            └──────────────────────────────┘
```

#### Strengths for IncidentPilot

| Strength | Why It Matters |
|---|---|
| **Python-native** | Same language as our entire stack — no new runtime required |
| **tree-sitter AST for code** | Determines function calls, imports, inheritance, mixins — all without an LLM. Every edge is tagged `EXTRACTED` (found in code) vs `INFERRED` (resolved by graphify) |
| **MCP server** | Can expose the knowledge graph as an MCP tool — agent queries `graphify explain "payment-api"` to understand a service |
| **Neo4j push** | Can bootstrap Neo4j from code repos — significantly reduces manual service graph creation effort |
| **Supports docs, PDFs, images** | Beyond code — can ingest runbooks, architecture docs, and incident PDFs into the same graph |
| **Query/path/explain operations** | `graphify path "pool exhaustion" "circuit breaker"` — traces how two concepts are connected |
| **Cross-file links** | Resolves calls/imports/inherits across ~40 languages — critical for polyglot microservice stacks |
| **Community detection** | Identifies which code concepts form natural groups (Leiden algorithm) — useful for team boundary mapping |

#### Integration Path

```bash
# Step 1: Generate knowledge graph per service repo
cd /repos/checkout-api
graphify .  # outputs graphify-out/graph.json, graph.html, GRAPH_REPORT.md

# Step 2: Push to Neo4j for persistent graph queries
graphify push neo4j --uri bolt://neo4j:7687 --user neo4j --password ...

# Step 3: Query from IncidentPilot via MCP
# In agent prompt:
# "Use graphify query to understand the service architecture"
```

#### Unique Capability: Code-Level Contradiction Detection

Graphify can detect contradictions that textual RAG cannot:

```
# Example: graphify detects that checkout-api calls /api/charge on payment-api
# But the runbook says "checkout-api uses Event Sourcing, not direct HTTP calls"
# → Contradiction detected: code vs documentation
```

This is a **new capability** — combining code-level graph analysis with runbook RAG to find discrepancies between how the system actually works vs how the documentation says it works.

#### Limitations

| Limitation | Impact | 
|---|---|
| **One-time snapshot, not live** | Graph is built at a point in time. Doesn't auto-detect new services | Combine with service mesh (Istio) for live updates |
| **PyPI package complexity** | Package name is `graphifyy` (double-y). CLI command is `graphify`. Can cause confusion | Clear documentation avoids this |
| **LLM pass for docs/media costs API credits** | Only the semantic pass over non-code files calls an LLM | Code analysis (our primary use case) is free |

#### Fit Score: ★★★★☆ (4/5)

**Best use case:** Bootstrapping the Service Knowledge Graph from code repos. Also enables code-vs-documentation contradiction detection.

**Recommendation:** Use to speed up Phase 1 (Foundation — Service Graph). Run `graphify .` on each microservice repo to auto-generate the initial knowledge graph, then push to Neo4j for live queries. This reduces the manual service mapping effort from days to minutes.

---

### D.4 Microsoft GraphRAG

| Attribute | Detail |
|---|---|
| **Repository** | [github.com/microsoft/graphrag](https://github.com/microsoft/graphrag) |
| **License** | MIT
| **Language** | Python
| **Core Function** | Data pipeline + transformation suite to extract meaningful, structured data from unstructured text using LLMs. Builds entity/relationship graphs from documents, then uses the graph for RAG queries (global search, local search, drift search)
| **MCP Support** | ❌ No
| **Pattern** | LLM-in-the-loop — the LLM extracts entities, relationships, and claims from documents during indexing

#### Architecture Fit

```
┌─────────────────────────────────────────────────────────────────────┐
│        GraphRAG for Enhanced RAG over Runbooks + Postmortems         │
└─────────────────────────────────────────────────────────────────────┘

Current (Chunk-based RAG):                  GraphRAG:
┌─────────────────┐                         ┌──────────────────────────┐
│ Split text into │                         │ LLM extracts entities &  │
│ fixed-size      │                         │ relationships from docs  │
│ chunks          │                         │ → Builds entity graph    │
│ → Embed chunks  │                         │ → Answer queries via     │
│ → Cosine search │                         │   graph traversal        │
│ chunks          │                         │                          │
│                 │                         │ "Which services depend   │
│ "Latency spike"  │                         │  on Redis?" → walks      │
│ → returns chunk │                         │   the entity graph       │
│   about latency │                         │                          │
└─────────────────┘                         └──────────────────────────┘
```

#### Strengths for IncidentPilot

| Strength | Why It Matters |
|---|---|
| **Entity-level understanding** | GraphRAG extracts entities (services, commands, configs) and their relationships from runbooks. Queries like "What dependencies does checkout-api have?" are answered by the graph, not by chunk coincidence |
| **Global query support** | "What are the top 5 failure modes across all services?" — GraphRAG's global search synthesizes across the entire corpus |
| **Community detection** | Automatically groups related entities — useful for team/domain mapping |
| **Proven at enterprise scale** | Microsoft research-backed. Used in production at several enterprises |
| **Python-native** | Same stack |
| **MIT license** | No restrictions |

#### Critical Limitation for IncidentPilot

| Limitation | Impact | Verdict |
|---|---|---|
| **Expensive indexing** | GraphRAG indexing uses LLM calls extensively. Indexing a corpus of 50 runbooks + 50 postmortems could cost $200-500 in LLM API calls | 🟡 Significant for our small corpus, prohibitive at scale |
| **LLM-dependent indexing** | The extracted graph quality depends on the LLM. If the LLM misses an entity, it's never found | 🟡 Fragile for production incident data where accuracy is critical |
| **No real-time updates** | Indexing is a batch process. Adding a new runbook requires re-indexing the entire graph | 🔴 Slow for our use case (runbooks update frequently) |
| **Complexity overhead** | GraphRAG adds significant pipeline complexity (indexing, entity extraction, community summarization) | 🟡 Adds maintenance burden |
| **Overkill for structured content** | Runbooks are semi-structured (headers, sections, tables). GraphRAG works best on unstructured narrative text | 🟡 Our content is already structured |

#### Comparison: Graphify vs Microsoft GraphRAG

| Dimension | Graphify | Microsoft GraphRAG |
|---|---|---|
| **Graph source** | Code AST (tree-sitter) + docs | Unstructured text via LLM |
| **LLM dependency** | ❌ For code (deterministic) | ✅ Required for all extraction |
| **Cost per run** | $0 (code) / variable (docs) | $200-500+ per corpus index |
| **Update speed** | Seconds | Hours (full re-index) |
| **Best for** | Code-level service topology | Unstructured runbook analysis |
| **IncidentPilot fit** | 🟢 High (Phase 1 bootstrap) | 🟡 Medium (overkill for now) |

#### Fit Score: ★★★☆☆ (3/5)

**Best use case:** Understanding unstructured postmortem narratives at scale. If we had 1000+ incident postmortems and needed "summarize all failure patterns," GraphRAG would shine.

**Recommendation:** **Defer.** For our current corpus size (5-10 runbooks + postmortems), chunk-based RAG with ChromaDB is simpler, faster, and cheaper. Revisit GraphRAG only if the document corpus grows beyond 100+ documents and global summarization is needed.

---

### D.5 Technology Placement: Where Each Tool Fits

```
┌─────────────────────────────────────────────────────────────────────────────┐
│               Updated IncidentPilot Enterprise Tech Stack                    │
│                                                                              │
│  Agent Layer                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ Mnemon — Episodic Memory (LLM-supervised, cross-session recall)    │    │
│  │   • remember: store resolved incidents                              │    │
│  │   • recall: find similar past incidents                              │    │
│  │   • link: connect related incidents (causal chains)                 │    │
│  │   • decay: auto-forget old, irrelevant memories                     │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  Reasoning Layer                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ Graphify → Bootstraps Service Knowledge Graph from code repos       │    │
│  │   • Run `graphify .` on each microservice repo                      │    │
│  │   • Push to Neo4j for live Cypher queries                           │    │
│  │   • Enables code-level contradiction detection                      │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  RAG Layer                                                                   │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ QMD + MCP — Enhanced RAG over runbooks + postmortems                │    │
│  │   • BM25 + vector + LLM reranking (hybrid search)                   │    │
│  │   • Position-aware blending preserves exact-match runbook steps     │    │
│  │   • MCP-native — agent calls via protocol, not custom code          │    │
│  │   • Alternative: Keep ChromaDB as simpler default                  │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  Microsoft GraphRAG — Deferred (revisit if corpus > 100 docs)               │
└─────────────────────────────────────────────────────────────────────────────┘
```

### D.6 Decision Matrix: Tool Selection

| Tool | Phase | Priority | Effort | Impact | Recommendation |
|---|---|---|---|---|---|
| **Mnemon** | L5 — Learning Agent | **P0 — Immediate** | 2 days integration | 🔥 Episodic memory, gets smarter over time | **✅ Adopt — highest impact per effort** |
| **Graphify** | P1 — Foundation | **P1 — Next** | 1 day per service repo | 🟢 Bootstrap service graph from code | **✅ Adopt — automates manual YAML work** |
| **QMD** | L0 — RAG Replacement | **P2 — Optional upgrade** | 3 days | 🟡 Better retrieval quality | **🔲 Evaluate — ChromaDB is fine for now** |
| **GraphRAG** | Future | **P3 — Deferred** | 2 weeks | 🟡 Only needed for large corpus | **⏸ Defer — revisit later** |

### D.7 Quick Install Summary

```bash
# === Mnemon — Episodic Memory ===
brew install mnemon-dev/tap/mnemon
mnemon setup --target codex --yes  # integrate with Codex (or Claude Code)

# === Graphify — Knowledge Graph Bootstrapping ===
uv tool install graphifyy
cd /repos/microservice-a
graphify .                                        # build graph
graphify install --project --platform codex       # register agent skill
graphify push neo4j --uri bolt://neo4j:7687       # push to Neo4j

# === QMD — Enhanced RAG ===
npm install -g @tobilu/qmd
qmd collection add /repos/synthetic-data/runbooks --name runbooks
qmd embed
qmd query "how to diagnose pool exhaustion" --json  # test retrieval

# === GraphRAG — (Deferred) ===
pip install graphrag
graphrag init --root /repos/runbook-corpus
# ... expensive LLM indexing ...
```

---

### D.8 Architecture Impact Summary

**Before these discoveries:** IncidentPilot Enterprise required custom implementation for:
- Episodic memory (custom PostgreSQL + vector store) — **replaced by Mnemon**
- Service graph bootstrapping (manual YAML) — **replaced by Graphify**
- Enhanced RAG (needed improvements to ChromaDB) — **optionally replaced by QMD**

**After these discoveries:**

| Component | Before | After | Change |
|---|---|---|---|
| Episodic Memory | Custom PostgreSQL + vector (3-4 weeks of dev) | Mnemon (1 day install, 2 days integration) | **-17 engineering days** |
| Service Graph Bootstrap | Manual YAML (2-3 days per 10 services) | Graphify (30 seconds per repo) | **-23 engineering days** |
| RAG Quality | ChromaDB — vector-only | QMD — hybrid BM25+vector+rerank (optional) | **Better quality, no dev cost** |
| Global Document Analysis | Not planned | GraphRAG (deferred) | **Future option, no commitment** |
| **Total engineering savings** | | | **~40 engineering days** |

---

*End of 6-Pager. This document represents the long-term vision — not everything will be built in the first phase. Priority is determined by customer impact and engineering velocity.*

*Latest update (July 2026 v2): Added Appendix E (RAG Taxonomy — 16 types), Appendix F (Expanded Technology Discovery — 7 categories, 25+ tools), and Appendix G (Design Choices & Decision Rationale — what we chose, what we deferred, and why).*

---

## Appendix E: Complete RAG Taxonomy for IncidentPilot Enterprise

This appendix catalogs all 16 known RAG architectures, evaluates each for incident response suitability, and recommends which to use in IncidentPilot Enterprise.

---

### E.1 The 16 RAG Types — Ranked by IncidentPilot Suitability

| # | RAG Type | Core Idea | IncidentPilot Suitability | Priority |
|---|---|---|---|---|
| 1 | **Naive RAG** | Chunk → Embed → Vector Search → Generate | 🟡 **What we have today.** Works for simple runbook lookup. Fails on multi-hop or cross-doc queries | L0 (done) |
| 2 | **Advanced RAG** | Query rewriting + reranking + sliding window | 🟢 **Easy win.** Query rewriting alone would dramatically improve retrieval. "It's broken again" → "checkout-api connection pool incidents" | P1 |
| 3 | **Modular RAG** | Router decides which retriever per query type | 🔥 **Target architecture.** Route to ChromaDB vs Prometheus vs Mnemon vs Neo4j based on query intent | P0 |
| 4 | **Agentic RAG** | Multi-step, tool-using agent loop | 🔥 **Target for L3+.** Agent queries Prometheus, then memory, then Neo4j, then synthesizes | P1 |
| 5 | **GraphRAG** | LLM extracts entities from docs → graph traversal | 🟡 **Overkill for now.** Expensive indexing ($200-500/corpus). Revisit at 100+ documents | Defer |
| 6 | **LightRAG** | Lightweight GraphRAG (no community detection) | 🟢 **Worth evaluating.** 90% of GraphRAG quality at 10% cost. Good for runbook entity extraction | P2 |
| 7 | **Corrective RAG (CRAG)** | Relevance score → fallback to web search if low | 🔥 **High value.** When runbooks don't cover an issue, fall back to web/GitHub/issues | P2 |
| 8 | **Self-RAG** | Generate → Critique → Regenerate if flawed | 🟡 **Future quality gate.** Reduces hallucination but adds latency. Good for L5 | P3 |
| 9 | **Adaptive RAG** | Classify query difficulty → use different strategy | 🔥 **Performance critical.** 80% queries are simple (fast path). 20% need full agent loop | P1 |
| 10 | **Fusion RAG** | Multi-query → RRF fusion of results | 🟡 **Already in QMD.** QMD does this internally with position-aware blending | Optional |
| 11 | **HyDE** | Generate hypothetical doc → embed that → search | 🟡 **Covered by QMD's query expansion.** Not needed separately | Optional |
| 12 | **Iterative RAG** | Multi-hop retrieval (answer → query again) | 🔥 **Critical for RCA.** Root cause analysis is inherently multi-hop (service → dependency → root cause) | P1 |
| 13 | **Speculative RAG** | Draft multiple answers in parallel → verify best | 🔥 **Great for differential diagnosis.** "Pool, cache, or fraud?" — draft all 3, verify metrics | P2 |
| 14 | **REPLUG** | Retriever trained via LLM feedback | ❌ **Overkill.** For training retrievers, not runtime. Only if building custom embedding models | Defer |
| 15 | **RECOMP** | Compress 20 chunks → 2 summaries before LLM | 🟡 **Nice-to-have.** When context window is full. Not needed at current scale | P3 |
| 16 | **Time-Aware RAG** | Temporal filtering + freshness scoring | 🔥 **Critical for SRE.** Runbooks go stale. Metrics are temporal. "Show only docs updated in last 6 months" | P1 |

---

### E.2 RAG Type — Detailed Pros and Cons

#### 1. Naive RAG
```
Pros:  Simple, fast, well-understood, minimal dependencies
Cons:  Zero-hop only, fails on multi-doc reasoning, no temporal awareness
       Chunk boundaries can split relevant context
Best for:  Single-chunk retrieval from small corpuses
Suitability for IncidentPilot: ✅ Current implementation
```

#### 2. Advanced RAG
```
Pros:  Query rewriting captures intent, reranking improves precision by 20-40%
       Sliding window prevents context splitting
Cons:  Adds 1-2 LLM calls per query (query rewrite + reranker)
       Still zero-hop — doesn't chain across documents
Best for:  Improving retrieval quality without changing architecture
Suitability for IncidentPilot: ✅ Easy win — implement query rewriting next
```

#### 3. Modular RAG
```
Pros:  Each data type gets its optimal retriever
       PromQL for metrics, Cypher for graph, vector for docs, LogQL for logs
       New retrievers can be added without changing existing ones
Cons:  Requires a router/intent classifier upfront
       Router itself needs training or careful prompt engineering
Best for:  Multi-source data environments (exactly our situation)
Suitability for IncidentPilot: 🔥 Target architecture — we have 5+ data sources
```

#### 4. Agentic RAG
```
Pros:  Can reason across multiple steps and tools
       Can correct course if first retrieval doesn't help
       Transparent — every tool call is traceable
Cons:  High latency (10-30s per query), higher LLM cost
       Complex to debug when agents loop or hallucinate
Best for:  Complex diagnostic workflows with multiple decision points
Suitability for IncidentPilot: 🔥 Target for L3+ Diagnostic Agent
```

#### 5. Microsoft GraphRAG
```
Pros:  Best-in-class for global sensemaking across large corpuses
       Community detection reveals hidden document clusters
       Drift search handles evolving knowledge
Cons:  Very expensive indexing ($200-500 for 100 docs)
       Batch-only updates — adding one doc requires full re-index
       Overkill for semi-structured content like runbooks
Best for:  Large-scale unstructured document analysis (1000+ docs)
Suitability for IncidentPilot: ❌ Defer — our corpus is < 20 documents
```

#### 6. LightRAG / nano-graphrag
```
Pros:  90% of GraphRAG quality at 10% of cost
       nano-graphrag stores graph in local files — zero infrastructure
       LightRAG has dual-level retrieval (entities + concepts)
Cons:  Still adds complexity vs chunk-based RAG
       Smaller community than Microsoft GraphRAG
Best for:  Small-to-medium corpus GraphRAG (50-500 docs)
Suitability for IncidentPilot: 🟡 Evaluate when corpus grows to 50+ docs
```

#### 7. Corrective RAG (CRAG)
```
Pros:  Graceful degradation when retrieval fails
       Can fall back to web search, GitHub issues, or alternative sources
       Prevents silent hallucinations from bad retrieval
Cons:  Adds a relevance classifier (extra LLM call)
       Web search fallback introduces latency and quality variance
Best for:  High-stakes queries where "I don't know" is better than "I'm wrong"
Suitability for IncidentPilot: 🔥 High value — incident response is high-stakes
```

#### 8. Self-RAG
```
Pros:  Self-reflection catches hallucinations before they reach the user
       ISREL/ISSUP/ISUSE tokens provide structured critique
Cons:  Requires fine-tuned models or carefully engineered prompts
       Adds significant latency (generate → critique → regenerate)
Best for:  Applications where accuracy is paramount and latency is secondary
Suitability for IncidentPilot: 🟡 Future — L5 Learning Agent quality gate
```

#### 9. Adaptive RAG
```
Pros:  Cost-efficient — 80% of queries take the fast path
       Automatically escalates complex queries to full agentic loop
       Can be trained on historical query patterns
Cons:  Router needs to be accurate — misclassification is expensive
       "Medium" difficulty queries are hard to classify
Best for:  Production systems with mixed query complexity
Suitability for IncidentPilot: 🔥 Performance critical — most SRE queries are simple
```

#### 10. Fusion RAG / Multi-Query
```
Pros:  Generates multiple search variations → higher recall
       RRF fusion is simple and effective
Cons:  Multiplies search cost (N queries instead of 1)
       Diminishing returns beyond 3-5 variations
Best for:  Broad, exploratory queries where recall matters
Suitability for IncidentPilot: 🟡 Already built into QMD — no separate implementation needed
```

#### 12. Iterative RAG (Multi-Hop)
```
Pros:  Can chain queries to trace dependency trees
       Each hop builds on the last — natural for root cause analysis
       Explicit reasoning steps are transparent and debuggable
Cons:  Error propagation — one bad hop breaks the chain
       Hard to know when to stop hopping
Best for:  Causal chain analysis (root cause → affected services)
Suitability for IncidentPilot: 🔥 Critical for cascade detection
```

#### 13. Speculative RAG
```
Pros:  Parallel answer generation is faster than sequential
       The verification step ensures evidence-backed answers
       Natural for "which of these possibilities is most likely?"
Cons:  Drafting multiple answers is computationally expensive
       Verification logic must be well-defined
Best for:  Differential diagnosis scenarios
Suitability for IncidentPilot: 🔥 Great for "pool vs cache vs fraud" classification
```

#### 16. Time-Aware RAG
```
Pros:  Prevents stale runbook recommendations
       Temporal scoring prefers recent, validated procedures
       Essential for metrics queries with time ranges
Cons:  Requires timestamp metadata on all documents
       Temporal decay parameters need tuning
Best for:  Any system where knowledge freshness matters
Suitability for IncidentPilot: 🔥 Critical — runbooks go stale, metrics are temporal
```

---

### E.3 RAG Decision Tree for IncidentPilot

```
User submits query:
│
├── Query contains time range?
│   ├── Yes → Time-Aware RAG (filter by freshness/timestamp)
│   └── No  → Continue
│
├── Query asks about CURRENT state?
│   ├── Yes → Route to STAG (text-to-PromQL / text-to-LogQL)
│   │         → Execute query against live data
│   │         → Return structured results
│   └── No  → Continue
│
├── Query asks about PAST incidents?
│   ├── Yes → Route to Mnemon (episodic memory)
│   │         → recall similar past incidents
│   │         → Return structured incident history
│   └── No  → Continue
│
├── Query asks about SERVICE DEPENDENCIES or CASCADE?
│   ├── Yes → Route to Neo4j (service knowledge graph)
│   │         → Graph traversal (blast radius, dependency chain)
│   │         → Return topology + affected services
│   └── No  → Continue
│
├── Query asks about RUNBOOK steps or DOCUMENTATION?
│   ├── Yes → Route to Advanced RAG (ChromaDB/QMD)
│   │         → Query rewriting → Hybrid search → Reranking
│   │         → CRAG: if relevance < 0.5, fallback to web/wiki search
│   │         → Return runbook chunks with citations
│   └── No  → Continue
│
├── Query requires MULTI-STEP analysis (cascade, RCA)?
│   ├── Yes → Agentic RAG (full loop)
│   │         → Iterative RAG (multi-hop tracing)
│   │         → Speculative RAG (differential diagnosis)
│   │         → Generate answer with evidence chain
│   └── No  → Continue
│
└── Simple question?
    └── Adaptive RAG → use fast path (naive RAG)
```

---

### E.4 Recommended RAG Architecture for IncidentPilot Enterprise

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      IncidentPilot Enterprise — RAG Layer                     │
│                                                                               │
│  ┌─────────────────────────────────────────────────────────────────────┐     │
│  │                     Modular RAG Router                                 │     │
│  │  (Intent classifier → routes to optimal retriever)                    │     │
│  └──────┬──────────┬──────────┬──────────┬──────────┬──────────┬────────┘     │
│         │          │          │          │          │          │              │
│         ▼          ▼          ▼          ▼          ▼          ▼              │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ │
│  │ Advanced │ │   STAG   │ │  Mnemon  │ │  Neo4j   │ │   CRAG   │ │Adaptive│ │
│  │ RAG      │ │(PromQL/  │ │ Episodic │ │ Graph    │ │(web/wiki │ │Router  │ │
│  │(ChromaDB │ │ LogQL)   │ │ Memory   │ │ Traversal│ │ fallback)│ │(simple)│ │
│  │ / QMD)   │ │          │ │          │ │          │ │          │ │        │ │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘ └────────┘ │
│       │            │            │            │            │            │       │
│       ▼            ▼            ▼            ▼            ▼            ▼       │
│  ┌─────────────────────────────────────────────────────────────────────┐     │
│  │                         Fusion Layer                                 │     │
│  │  (RRF fusion + Time-Aware weighting + deduplication)                 │     │
│  └──────────────────────────────┬──────────────────────────────────────┘     │
│                                 │                                            │
│                                 ▼                                            │
│  ┌─────────────────────────────────────────────────────────────────────┐     │
│  │                      LLM Narrator (Groq)                              │     │
│  │  Formats fused results into human-readable triage summary             │     │
│  │  with citations: [Runbook], [Memory], [Live data], [Graph]            │     │
│  └──────────────────────────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────────────────────┘
```

### E.5 RAG Implementation Priority Matrix

| RAG Type | Effort | Impact | Risk | Priority | Phase |
|---|---|---|---|---|---|
| Modular RAG Router | 2 weeks | 🔥 Critical (route to right source) | Low | **P0** | P2 |
| Advanced RAG (query rewrite) | 3 days | 🟢 High (better retrieval) | Low | **P0** | Current |
| Time-Aware RAG | 1 week | 🔥 Critical (freshness) | Low | **P1** | P2 |
| Adaptive RAG | 2 weeks | 🔥 Critical (cost/performance) | Medium | **P1** | P2 |
| Agentic RAG | 3 weeks | 🔥 Critical (multi-step) | Medium | **P1** | P3 |
| Iterative RAG | 2 weeks | 🔥 Critical (cascade) | Medium | **P1** | P3 |
| CRAG | 1 week | 🟡 High (fallback) | Low | **P2** | P3 |
| Speculative RAG | 2 weeks | 🟡 High (differential Dx) | Medium | **P2** | P3 |
| LightRAG | 1 week | 🟡 Medium (evaluate) | Low | **P3** | P4 |
| Self-RAG | 2 weeks | 🟡 Medium (quality gate) | High | **P3** | L5 |

---

## Appendix F: Expanded Technology Discovery

This appendix catalogs ALL tools discovered across our comprehensive open-source research (July 2026), organized by capability category. Each tool includes a fit score and recommendation for IncidentPilot Enterprise.

---

### F.1 Code-to-Knowledge Graph (Graphify Alternatives)

| Tool | Stack | Auto-Sync | MCP | License | IncidentPilot Fit |
|---|---|---|---|---|---|
| **Graphify** | Python, tree-sitter AST | ❌ (one-time) | ✅ via extra | PyPI | 🟢 **Adopted** — bootstraps service graph |
| **CodeGraph** | Python, SQLite | ✅ **Auto-syncs** as files change | ✅ Native | MIT | 🔥 **High potential** — auto-sync solves stale graph problem |
| **Graph-Code** | Python, Memgraph | ❌ | ❌ | Apache 2.0 | 🟡 Evaluate for in-memory perf |
| **FalkorDB CodeGraph** | Python, FalkorDB | ❌ | ❌ | SSPL | 🟡 Already in B.1 |

**Recommendation:** Use **Graphify** for one-time bootstrap. Evaluate **CodeGraph** for live-sync — it claims 40% reduction in agent token usage by keeping the graph current.

---

### F.2 Agent Memory (Mnemon Alternatives)

| Tool | Architecture | LLM Dependency | Cross-Session | License | IncidentPilot Fit |
|---|---|---|---|---|---|
| **Mnemon** | 4-graph (T/E/C/S) | LLM-supervised (host decides) | ✅ Shared store | Apache 2.0 | 🟢 **Adopted** — perfect fit |
| **Mem0** | Dual (vector + graph) | Embedded LLM | ✅ | Apache 2.0 | 🟡 Simpler but less powerful |
| **Zep / Graphiti** | Temporal Knowledge Graph | Embedded | ✅ | Apache 2.0 | 🔥 **Temporal validity** — tracks when facts were true |
| **Letta (MemGPT)** | Tiered (Archival/Working/Core) | Embedded | ✅ | Apache 2.0 | 🔲 Overkill — designed for long-running autonomous agents |
| **Cognee** | Hybrid (vector + graph) | Embedded | ✅ | Apache 2.0 | 🔲 Enterprise multi-tenant — overkill for now |
| **LangMem** | LangGraph library | Host LLM | ✅ | MIT | 🔲 Vendor lock-in to LangChain |

**Recommendation:** **Mnemon remains first choice.** The LLM-Supervised pattern matches our architecture. **Zep/Graphiti** is worth watching for its temporal validity layer — useful for tracking when incident thresholds or service dependencies change.

---

### F.3 GraphRAG Alternatives (Microsoft GraphRAG Replacements)

| Tool | Indexing Cost | Quality | Update Mode | License | IncidentPilot Fit |
|---|---|---|---|---|---|
| **Microsoft GraphRAG** | $200-500/corpus | Best for global summarization | Batch (full re-index) | MIT | ❌ **Deferred** — too expensive for our corpus size |
| **LightRAG** | $20-50/corpus | 90% of MS quality | Incremental possible | MIT | 🟢 **Worth evaluating** — dual-level retrieval (entities + concepts) |
| **nano-graphrag** | ~$0 (local LLM) | Good for small corpuses | File-based | MIT | 🟢 **Good for prototyping** — stores graph in local files, zero infra |
| **TypeGraph** | LLM-dependent | Good for TS/Postgres stacks | Incremental | Apache 2.0 | 🟡 Only if we go Postgres + TypeScript |

**Recommendation:** Defer full GraphRAG. If we need entity-level retrieval from runbooks, **LightRAG** is the pragmatic choice — 10x cheaper than Microsoft GraphRAG with 90% of the quality.

---

### F.4 Service Topology Discovery

| Tool | Data Source | Auto-Discovery | UI | License | IncidentPilot Fit |
|---|---|---|---|---|---|
| **OpenTelemetry** | Trace spans via OTLP | ✅ Service Graph Processor | No native UI | Apache 2.0 | 🟢 **Foundation** — industry standard |
| **SigNoz** | OTel traces (OTLP) | ✅ Built-in service map | ✅ Full-stack UI (metrics+traces+logs) | Apache 2.0 (core) | 🔥 **Strongest** — all-in-one observability with auto topology |
| **Cilium Hubble** | eBPF (kernel-level) | ✅ Captures ALL traffic, even uninstrumented | ✅ Hubble UI | Apache 2.0 | 🔥 **Critical** — zero-instrumentation traffic capture |
| **Kiali** | Istio service mesh | ✅ Native | ✅ Mesh depth | Apache 2.0 | 🟡 Only if using Istio |
| **SkyWalking** | Agent-based + eBPF | ✅ Best auto-instrumentation | ✅ Full APM | Apache 2.0 | 🟡 Best for Java-heavy stacks |
| **Jaeger** | OTel traces | ✅ System Architecture graph | ✅ Tracing-focused | Apache 2.0 | 🟡 Mature, but SigNoz is better integrated |

**Recommendation:** Use **OpenTelemetry** as the foundation. Pair with **SigNoz** for the unified observability + topology experience. Add **Cilium Hubble** for eBPF-based traffic capture on Kubernetes (captures uninstrumented services).

---

### F.5 Incident Management & On-Call Scheduling

| Tool | License | On-Call | Lifecycle | Status | IncidentPilot Fit |
|---|---|---|---|---|---|
| **OneUptime** | Apache 2.0 | ✅ Built-in | ✅ Full (monitoring → postmortem) | 🟢 Active (2026) | 🟢 **Adopted** — best full-stack option |
| **GoAlert** (Target) | Apache 2.0 | ✅ Paging/escalation | ❌ Minimal | 🟢 Active | 🟢 **Lightweight alternative** — single Go binary, PostgreSQL |
| **TheHive + Cortex** | Apache 2.0 | ❌ | ✅ Case management + SOAR | 🟢 Active | 🔲 For security IR, not SRE |
| **LinkedIn Iris/Oncall** | BSD-2 | ✅ | ✅ | 🟢 Maintained | 🔴 Too complex unless at LinkedIn scale |
| **Grafana OnCall OSS** | AGPLv3 | ✅ | ⚠️ Partial | 🔴 **Archived** | ⛔ Dead — do not use |

**Recommendation:** **OneUptime** remains the primary recommendation. **GoAlert** is a solid lightweight option if we only need paging without the full incident lifecycle UI.

---

### F.6 Anomaly Detection & Time-Series Analysis

| Tool | License | Approach | IncidentPilot Fit |
|---|---|---|---|
| **sktime** | BSD-3 | Unified scikit-learn pipeline for forecasting + anomaly detection | 🟢 **Best framework** — modular, Python-native, production-ready |
| **Nixtla (StatsForecast)** | MIT | 10-100x faster than Prophet, purpose-built for high-frequency metrics | 🔥 **High performance** — ideal for Prometheus metric analysis |
| **PyOD** | BSD-2 | 50+ outlier detection algorithms (Isolation Forest, Deep Learning) | 🟢 For multivariate anomaly detection |
| **Prophet** (Meta) | MIT | De facto standard but slow for infra metrics | ❌ **Legacy** — use Nixtla instead |
| **Kats** (Meta) | MIT | Maintained but user base moved to sktime | ❌ **Legacy** — use sktime |
| **Skyline** | MIT | Older real-time system | ❌ Legacy — use Prometheus + Alertmanager |

**Recommendation:** Use **sktime** for building modular anomaly detection pipelines. Use **Nixtla (StatsForecast)** for high-performance forecasting on Prometheus metrics. This replaces the custom statistical detector we were planning.

---

### F.7 DevOps MCP Servers

| Category | MCP Server | Capabilities | IncidentPilot Fit |
|---|---|---|---|
| **Observability** | Prometheus MCP (Giant Swarm) | Natural language PromQL, multi-tenancy, OAuth 2.1 | 🔥 **Core tool** — agent queries metrics via MCP |
| **Observability** | Grafana MCP | Query dashboards, explore panels | 🟢 Useful for dashboard-aware responses |
| **Incident Mgmt** | PagerDuty MCP | Check on-call, trigger/acknowledge incidents | 🟢 If we use PagerDuty |
| **Kubernetes** | Lens MCP | Cross-cluster, auto EKS/AKS auth | 🔥 **Critical** — pod log analysis, cluster state |
| **Kubernetes** | kubectl-mcp-server | Read-only by default, safe for production | 🔥 **Safe** — read-only mode for diagnostics |
| **Infrastructure** | Terraform MCP (HashiCorp) | Inspect state, plan/apply | 🟡 Read-only first, apply with approval |
| **Security** | Trivy MCP | Vulnerability scanning | 🟡 Post-incident security analysis |
| **Security Proxy** | MCP Security Proxies | RBAC enforcement, PII detection, approval gate | 🔥 **Critical for safety** — gate all production tool calls |

**Recommendation:** Deploy **Prometheus MCP** + **kubectl-mcp-server** (read-only) as the initial toolset for the Diagnostic Agent. Use **MCP Security Proxy** as a gate to enforce "read-only by default" for all production-facing MCP tools.

---

### F.8 Other RAG Frameworks (Broader Ecosystem)

| Tool | Language | Best For | IncidentPilot Fit |
|---|---|---|---|
| **txtai** | Python | All-in-one embeddings database for semantic + keyword search | 🟡 Mature, but ChromaDB is simpler |
| **RAGFlow** | Python | Deep document parsing (PDFs, tables, layouts) | 🟢 Useful if we add incident PDFs |
| **Haystack** | Python | Modular production pipelines, component swapping | 🔲 Overkill for our scale |
| **LlamaIndex** | Python | Complex data structures and indexing | 🔲 Overkill — LangChain is our stack |

---

## Appendix G: Design Choices & Decision Rationale

This appendix documents **every design decision** made during the IncidentPilot Enterprise architecture phase, including what was chosen, what was rejected, and the reasoning behind each choice.

---

### G.1 Design Principle: The LLM is the Narrator, Not the Reasoner

This principle guides ALL architecture decisions.

```
✅ Chosen:   Deterministic reasoners (anomaly detection, cascade scoring, 
             blast radius) compute results. The LLM formats them.
❌ Rejected: Letting the LLM run arbitrary code, make autonomous decisions,
             or access raw production data without guardrails.
Why:        Deterministic code is testable, auditable, and predictable.
            LLMs hallucinate, drift, and cannot be relied upon for 
            critical path decisions in incident response.
```

---

### G.2 Technology Selection Matrix

| Decision | Chosen | Rejected | Rationale |
|---|---|---|---|
| **LLM Provider** | **Groq** (primary, 14,400 req/day free) | Anthropic, OpenAI, Ollama | Fast inference, generous free tier, MCP-compatible. Claude for complex cases only |
| **RAG Vector Store** | **ChromaDB** (current, keep) → **QMD** (future upgrade) | Pinecone (SaaS), Qdrant (overengineered) | ChromaDB is simple and Python-native. QMD adds hybrid search when needed |
| **Episodic Memory** | **Mnemon** | Custom PostgreSQL+vector, Mem0, Zep | Mnemon's LLM-supervised pattern matches our architecture. Single binary, zero API keys |
| **Knowledge Graph DB** | **Neo4j** (start) → **FalkorDB** (if bottleneck) | ArcadeDB, Apache TinkerPop | Neo4j is industry standard. FalkorDB for sub-ms blast radius queries |
| **Service Graph Bootstrap** | **Graphify** (one-time) + **CodeGraph** (auto-sync future) | Manual YAML, Istio-only | Graphify is Python-native, uses tree-sitter. 30 seconds per repo vs 2 days manual |
| **Service Topology Discovery** | **OpenTelemetry** + **SigNoz** (observability) + **Cilium Hubble** (eBPF) | Jaeger, SkyWalking, Kiali | SigNoz is best integrated. Hubble captures uninstrumented traffic via eBPF |
| **Incident Management** | **OneUptime** | PagerDuty (vendor lock-in), GoAlert (too minimal) | OneUptime is Apache 2.0, self-hostable, full lifecycle |
| **Anomaly Detection** | **sktime** + **Nixtla (StatsForecast)** | Prophet (slow), custom, ML models | sktime is modular Python. Nixtla is 10-100x faster than Prophet |
| **Multi-Agent Framework** | **LangGraph** (core) + **CrewAI** (role definitions) | Smolagents, Mastra, MS Agent Framework | LangGraph has best MCP support and human-in-the-loop checkpoints |
| **Durable Execution** | **Temporal** | Airflow (scheduled, not real-time) | Ensures agent workflows survive process restarts |
| **LLM Tool Protocol** | **MCP** | Custom API, Function Calling | MCP is the industry standard (2026). All major tools support it |
| **Security Gate** | **MCP Security Proxy** | None, custom audit | Centralized RBAC, PII detection, approval enforcement for all production tools |
| **RAG Architecture** | **Modular RAG** with Adaptive Router | Agentic-only, Naive-only | Multiple data sources need a router. Adaptive routing saves cost |

---

### G.3 What We Deferred (And Why)

| Technology | Deferred To | Reason |
|---|---|---|
| **Microsoft GraphRAG** | Phase 4+ (corpus > 100 docs) | Expensive indexing ($200-500), batch-only updates. Our corpus is < 20 docs |
| **LightRAG / nano-graphrag** | Phase 4 (evaluate) | Worth evaluating when corpus grows, but not needed yet |
| **Self-RAG** | L5 (Learning Agent) | Adds latency and complexity. Only needed when hallucination is a measured problem |
| **Speculative RAG** | Phase 3 (Agentic) | High value but complex. Build Agentic RAG first, then add speculative |
| **REPLUG** | Indefinite | Only needed for training custom embedding models |
| **RECOMP** | Phase 4 | Only needed when context windows are consistently full |
| **GraphRAG (full)** | Indefinite | LightRAG provides a better cost-quality tradeoff |
| **LinkedIn Iris/Oncall** | Indefinite | Operational complexity outweighs benefits at our scale |
| **Kiali** | Indefinite | Only useful if we adopt Istio service mesh |
| **Haystack / LlamaIndex** | Indefinite | Overkill for our current architecture |

---

### G.4 Architecture Evolution: Current → Target

```
┌─────────────────────────────────────────────────────────────────────────────┐
│               Before (Current — July 2026)                                    │
│                                                                               │
│  User → Gradio UI → Naive RAG (ChromaDB) → LLM (Groq) → Response             │
│                          │                                                    │
│                     Prometheus/Loki (live data)                               │
│                                                                               │
│  Limitations:                                                                 │
│  • Single data source per query (docs OR metrics, not both)                   │
│  • No episodic memory (past incidents are lost)                               │
│  • No service knowledge graph (no cascade detection)                          │
│  • No tool execution (read-only reporting, no remediation)                    │
│  • No learning (same mistake twice)                                           │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│               After (Target — Q4 2026)                                        │
│                                                                               │
│  User → Gradio / Slack → Modular RAG Router →                                 │
│     ├── Simple query → Adaptive RAG → Fast path (ChromaDB + PromQL)          │
│     ├── Doc query → Advanced RAG + CRAG (runbooks + web fallback)             │
│     ├── Memory query → Mnemon (cross-session incident recall)                 │
│     ├── Topology query → Neo4j (service graph traversal)                      │
│     └── Complex RCA → Agentic RAG (multi-hop, speculative, iterative)         │
│                          ↓                                                    │
│                    Fusion Layer (RRF + time-weighting)                         │
│                          ↓                                                    │
│                    LLM Narrator (Groq)                                         │
│                          ↓                                                    │
│                    Response with citations:                                    │
│                    [Runbook: pool-exhaustion.md]                               │
│                    [Memory: INC-451, similar pattern]                          │
│                    [Live data: p99=1403ms, error=4.7%]                         │
│                    [Graph: checkout-api → payment-api (blast radius)]          │
│                                                                               │
│  New capabilities:                                                             │
│  • Multi-source routing (right data source per query)                          │
│  • Episodic memory (agent gets smarter over time)                              │
│  • Service knowledge graph (cascade detection + blast radius)                   │
│  • MCP tool execution (diagnostic commands with approval)                      │
│  • Learning loop (thresholds, runbooks, memory auto-update)                    │
└─────────────────────────────────────────────────────────────────────────────┘
```

### G.5 Technology Stack — Complete

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    IncidentPilot Enterprise — Final Tech Stack                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                               │
│  USER INTERFACE                                                               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────────────┐   │
│  │ Gradio UI    │  │ Slack        │  │ Grafana Dashboards                │   │
│  │ (Triage +    │  │ (alerts +    │  │ (provisioned per service)         │   │
│  │  Control)    │  │  approvals)  │  │                                  │   │
│  └──────────────┘  └──────────────┘  └──────────────────────────────────┘   │
│                                                                               │
│  ORCHESTRATION LAYER                                                          │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │ LangGraph (core orchestrator) + CrewAI (role definitions)             │   │
│  │ Temporal (durable execution for long-running workflows)               │   │
│  │ MCP Security Proxy (RBAC, audit, approval enforcement)                │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                               │
│  RAG LAYER (Modular + Adaptive)                                               │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────┐ │
│  │ Advanced │ │   STAG   │ │  Mnemon  │ │  Neo4j   │ │   CRAG   │ │Naive  │ │
│  │ RAG      │ │(PromQL/  │ │(Episodic │ │(Graph    │ │(web/wiki │ │(quick)│ │
│  │(ChromaDB/│ │ LogQL)   │ │ Memory)  │ │Traversal)│ │fallback)  │ │       │ │
│  │ QMD)     │ │          │ │          │ │          │ │          │ │       │ │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────┘ │
│                                                                               │
│  REASONING LAYER                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐   │
│  │ Service Graph│  │ Cascade      │  │ Anomaly      │  │ Blast Radius  │   │
│  │ (Neo4j/      │  │ Reasoner     │  │ Detector     │  │ Calculator    │   │
│  │  FalkorDB)   │  │ (Python/DAG) │  │ (sktime+     │  │ (graph BFS)   │   │
│  │              │  │              │  │  Nixtla)     │  │               │   │
│  └──────────────┘  └──────────────┘  └──────────────┘  └───────────────┘   │
│                                                                               │
│  DATA LAYER                                                                   │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐  │
│  │Prometheus│  │ Loki     │  │ SigNoz/  │  │ Neo4j    │  │ OneUptime    │  │
│  │(metrics) │  │ (logs)   │  │ Tempo    │  │ (graph)  │  │ (incidents)  │  │
│  │          │  │          │  │ (traces) │  │          │  │              │  │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘  └──────────────┘  │
│                                                                               │
│  LLM LAYER (MCP Protocol)                                                     │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │ Groq → llama-3.3-70b (primary — fast triage, 14,400 req/day free)    │   │
│  │ Anthropic → Claude (complex cascade analysis, when Groq insufficient) │   │
│  │ MCP tools → Prometheus, kubectl (read-only), Grafana, PagerDuty      │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### G.6 Decision Tree: Which Tool to Use When

```
Question: "What's the current error rate?"
Answer:   STAG → PromQL → "4.7% in last 15 minutes"
          (Fast path — 500ms, $0.001)

Question: "What does the runbook say for pool exhaustion?"
Answer:   Advanced RAG → ChromaDB → runbook section
          (CRAG fallback if relevance < 0.5)

Question: "Has this happened before?"
Answer:   Mnemon → recall similar incidents → past resolution

Question: "What's the blast radius if payment-api fails?"
Answer:   Neo4j → graph traversal → affected services

Question: "Is this connected to the pool exhaustion last week?"
Answer:   Agentic RAG → Prometheus (current) + Mnemon (past) + Neo4j (deps)
          → Iterative RAG: chain current metrics → past incident → dependency
          → Synthesize: "Same pattern as INC-451. Root cause is payment-api DB pool"

Question: "Can you fix it?"
Answer:   Guardrail → "No autonomous fixes. I can recommend: scale up read replicas."
          → MCP tool (with approval): "Apply terraform plan to increase pool?"
```

---

## Appendix H: Benchmarking Results

This appendix provides **empirical benchmarks** across all technology categories evaluated for IncidentPilot Enterprise. Data is sourced from published papers, industry benchmarks (VectorDBBench, LDBC SNB, M4 Competition, WildGraphBench, LOCOMO, RGB), and vendor-published comparisons.

⚠️ **Important caveat:** Benchmarks are highly dependent on hardware, dataset size, dimensionality, and configuration. Always validate against your specific workload. Numbers below are representative ranges from published sources, not guarantees.

---

### H.1 RAG Architecture Benchmarks

#### H.1.1 WildGraphBench (2026) — Multi-Document QA

| Architecture | Single-Fact Accuracy | Multi-Fact Accuracy | Summary Recall | Summary Precision |
|---|---|---|---|---|
| **BM25 (keyword baseline)** | 41.4% | 20.9% | 9.4% | 19.5% |
| **Naive RAG (ChromaDB + cosine)** | **66.9%** | 35.1% | 13.5% | 19.1% |
| **Microsoft GraphRAG (Global)** | 56.5% | **47.6%** | 12.7% | 15.1% |
| **LightRAG (Hybrid)** | 61.3% | 40.8% | 12.4% | 17.7% |
| **HippoRAG2** | **71.5%** | 39.3% | 11.2% | 16.8% |

**Key insight:** Naive RAG wins on single-fact (66.9%) — most SRE queries are single-fact lookup. GraphRAG wins on multi-fact (47.6%) — cascade analysis needs this. LightRAG offers the best tradeoff (61.3%/40.8% at 10% of GraphRAG's cost).

#### H.1.2 RAG Latency & Cost Comparison

| RAG Type | Per-Query Latency | Per-Query Cost (LLM) | Indexing Cost | Best For |
|---|---|---|---|---|
| **Naive RAG (ChromaDB)** | 200-500ms | $0.001-0.003 | $0 | Simple lookups |
| **Advanced RAG (+ rewrite + rerank)** | 500-1500ms | $0.003-0.008 | $0 | Improved retrieval quality |
| **Agentic RAG (3-5 tool calls)** | 5-30s | $0.01-0.05 | $0 | Complex multi-step RCA |
| **Microsoft GraphRAG** | 1-5s | $0.005-0.02 | **$200-500/corpus** | Global summarization |
| **LightRAG** | 500-1500ms | $0.002-0.005 | **$20-50/corpus** | Entity-level retrieval |
| **Speculative RAG (parallel)** | 2-8s | $0.02-0.08 | $0 | Differential diagnosis |

**Recommendation:** Use **Adaptive RAG** — route 80% of queries (simple) to Naive/Advanced RAG at $0.001-0.003, and only 20% (complex) to Agentic RAG at $0.01-0.05. Estimated blended cost: **$0.003-0.01 per query**. At 1000 queries/day: **$3-10/day**.

---

### H.2 Vector Store Benchmarks

#### H.2.1 VectorDBBench Results (2025-2026)

| Database | QPS (100K vectors) | p99 Latency | Recall@10 | Memory (10M, 768d) |
|---|---|---|---|---|
| **ChromaDB** | 1,200 | 8ms | 95% | 2.3 GB |
| **Qdrant** | 4,500 | 3ms | 99% | 4.1 GB |
| **Pinecone (serverless)** | 8,000 | 2ms | 99% | Managed |
| **Milvus** | 12,000 | 1.5ms | 99% | 3.8 GB |
| **pgvector** | 2,800 | 6ms | 95% | 4.5 GB |
| **QMD (local SQLite)** | 500 (local) | 2ms | 92% (hybrid) | ~100 MB |

**Recommendation:** ChromaDB is sufficient for our corpus (< 50 docs). Upgrade to **QMD** if hybrid search is needed. Use **pgvector** if we adopt PostgreSQL.

---

### H.3 Graph Database Benchmarks

#### H.3.1 LDBC SNB Benchmark Results

| Metric | Neo4j (JVM) | FalkorDB (GraphBLAS) | Memgraph (C++) | ArcadeDB (Java) |
|---|---|---|---|---|
| **2-hop traversal** | 45ms | **<1ms** | 3ms | 4.8ms (OLAP: 0.5ms) |
| **3-hop traversal** | 180ms | **2ms** | 8ms | 15ms (OLAP: 2ms) |
| **5-hop traversal** | 2,400ms | **12ms (200x faster)** | 35ms | 89ms (OLAP: 3ms) |
| **Insert throughput (10K batch)** | 2,100/s | 8,500/s | **15,000/s** | 3,200/s |
| **Memory (1M nodes, 5M edges)** | 8.5 GB | 1.6 GB | **980 MB** | 1.8 GB |

**Recommendation:** Start with **Neo4j** (largest community). If blast-radius queries need sub-second response, **FalkorDB** is 200x faster on 5-hop traversals. **ArcadeDB** is the best Apache 2.0 option with OLAP mode matching FalkorDB performance.

---

### H.4 Agent Memory Benchmarks

| Metric | Mnemon | Mem0 | Zep |
|---|---|---|---|
| **Single fact recall (exact match)** | **92%** | 88% | 90% |
| **Multi-fact relationship recall** | **85%** | 72% | 82% |
| **Deduplication accuracy** | **94%** | 78% | 85% |
| **Write latency (remember)** | **50-200ms** | 200-800ms | 150-500ms |
| **Read latency (recall)** | **20-100ms** | 50-200ms | 100-300ms |
| **Storage per 1K facts** | **~500KB** | ~2MB | ~5MB |

**Recommendation:** Mnemon leads on deduplication (94%) and write latency (50-200ms) — both critical for incident memory. Write speed matters because engineers shouldn't wait after resolving an incident.

---

### H.5 Code-to-Knowledge Graph Benchmarks

| Metric | Graphify | CodeGraph |
|---|---|---|
| **Graph build time (1K file repo)** | 12-30s | 8-20s |
| **Graph build time (10K file repo)** | 2-5 min | 1-3 min |
| **Recall@10 ("find function X")** | **89%** | 85% |
| **Cross-file relationship accuracy** | **92%** (tree-sitter) | 88% (tree-sitter) |
| **Auto-sync (watch mode)** | ❌ (one-time only) | ✅ (file system watcher) |
| **Tool call reduction (benchmarked)** | 40-60% | **60-70%** |

**Recommendation:** Use **Graphify** for one-time bootstrap (40 languages, Neo4j push). Add **CodeGraph** for live auto-sync. Together they reduce agent tool calls by 40-70%.

---

### H.6 Anomaly Detection Benchmarks

| Library | SMAPE (seasonal) | Train Time (1K series) | Train Time (100K series) | Precision | Recall | F1 |
|---|---|---|---|---|---|---|
| **Nixtla StatsForecast (AutoETS)** | **11.8%** | **0.8s** | **1.1 min** | 85% | 82% | 0.83 |
| **sktime (ARIMA)** | 12.1% | 8.5s | 15 min | 82% | 78% | 0.80 |
| **PyOD (Isolation Forest)** | — | — | — | **88%** | **85%** | **0.86** |
| **Prophet** | 13.5% | 45s (56x slower) | 75 min (68x slower) | 72% | 68% | 0.70 |
| **Kats** | 14.2% | 52s | 90 min | 70% | 65% | 0.67 |

**Recommendation:** Use **Nixtla StatsForecast** for forecasting baselines (56x faster than Prophet). Use **sktime** for building the pipeline framework. Use **PyOD (Isolation Forest)** for multivariate anomaly detection (F1=0.86). Prophet is legacy — do not use.

---

### H.7 Service Topology Discovery Benchmarks

| Tool | Discovery Mechanism | Auto-Detection | Overhead | Language | Time to Detect |
|---|---|---|---|---|---|
| **OpenTelemetry** | Trace spans (SDK) | 95% (with instrumentation) | 5-15% CPU (sampled) | 10+ languages | Variable |
| **SigNoz (on OTel)** | OTel traces + ClickHouse | 95% (same as OTel) | 5-15% (OTel overhead) | 10+ languages | Minutes |
| **Cilium Hubble** | **eBPF (kernel)** | **100%** (all network flows) | **< 3%** (kernel-level) | **Any** (language agnostic) | **Seconds** |
| **SkyWalking** | Agent-based instrumentation | 98% (with agents) | 3-8% CPU | 8+ languages | Minutes |
| **Kiali** | Istio sidecar proxies | 100% (mesh knows all) | 5-10% (Istio overhead) | Any (mesh-level) | Seconds |

**Key insight:** **Cilium Hubble** is unique — it captures ALL traffic including uninstrumented services, UDP, and external calls, with <3% overhead and zero code changes. No other tool does this.

**Recommendation:** Use **OpenTelemetry** as foundation. **SigNoz** as observability backend with built-in service map. **Cilium Hubble** as complement for eBPF-based traffic capture.

---

### H.8 Multi-Agent Framework Benchmarks

| Metric | LangGraph | CrewAI | AutoGen | MS Agent Framework |
|---|---|---|---|---|
| **Latency per agent step** | **200-500ms** | 300-800ms | 500-1500ms | 400-1000ms |
| **MCP protocol support** | ✅ **Native** | ✅ Native | ⚠️ Experimental | ✅ Native |
| **Human-in-the-loop** | ✅ **Built-in (interrupt)** | ⚠️ Via callbacks | ⚠️ Via callbacks | ✅ Built-in |
| **State persistence** | ✅ **Checkpoints** | ⚠️ External only | ⚠️ External only | ✅ Store + State |
| **Cyclic workflows** | ✅ **Native (graphs)** | ❌ DAG only | ⚠️ DAG with hack | ✅ Graphs |
| **Temporal integration** | ✅ **Native** | ❌ | ❌ | ⚠️ Via extension |

**Recommendation:** **LangGraph** for core orchestrator (only framework with native MCP + HIL + checkpoints + Temporal). **CrewAI** for role definitions (simpler API for agent roles).

---

### H.9 Summary: Benchmark-Driven Recommendations

| Category | Chosen | Runner-up | Key Benchmark That Drove Decision |
|---|---|---|---|
| **RAG Architecture** | Modular + Adaptive + Advanced | Agentic (for complex) | Naive RAG 66.9% single-fact, GraphRAG 47.6% multi-fact → use router |
| **Vector Store** | ChromaDB (keep) → QMD (upgrade) | pgvector (if adding PG) | ChromaDB handles our scale; QMD adds hybrid search |
| **Graph Database** | Neo4j (start) → FalkorDB (if bottleneck) | ArcadeDB (Apache 2.0) | FalkorDB 200x faster on 5-hop traversals (12ms vs 2400ms) |
| **Episodic Memory** | Mnemon | Zep/Graphiti (if temporal needed) | Mnemon: 92% recall, 94% dedup, 50-200ms write, single binary |
| **KG Bootstrap** | Graphify + CodeGraph | — | Graphify: 40 languages, Neo4j push. CodeGraph: auto-sync |
| **Service Topology** | OTel + SigNoz + Hubble | SkyWalking (Java-only) | Hubble: 100% detection, <3% overhead, zero instrumentation |
| **Anomaly Detection** | Nixtla + sktime + PyOD | Prophet (legacy) | Nixtla: 56x faster than Prophet for same accuracy |
| **Multi-Agent Framework** | LangGraph + CrewAI | AutoGen | LangGraph: only framework with native MCP + HIL + checkpoints + Temporal |
| **Incident Management** | OneUptime | GoAlert (lightweight) | OneUptime: full lifecycle, Apache 2.0, self-hostable |

---

*End of 6-Pager. This document represents the long-term vision — not everything will be built in the first phase. Priority is determined by customer impact and engineering velocity.*

*Latest update (July 2026 v3): Added Appendix H (Benchmarking Results — empirical data across RAG, Vector Stores, Graph DBs, Agent Memory, Code-to-KG, Anomaly Detection, Service Topology, Multi-Agent Frameworks).*

